import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import ts from 'typescript'
import { ProjectRequestGuard, analyzeProjectRequest } from '../src/projectRequestGuard.ts'
import { normalizeTitleDisplay } from '../src/setupFieldPresentation.ts'

const appSource = readFileSync(new URL('../src/App.vue', import.meta.url), 'utf8')

function install(state: Record<string, any>, ranges: Array<[string, string]>) {
    const code = ranges.map(([name, next]) => {
        const start = appSource.indexOf(`const ${name} =`)
        const end = appSource.indexOf(`const ${next} =`, start)
        assert.ok(start >= 0 && end > start)
        return appSource.slice(start, end) + `\nglobalThis.${name} = ${name};\n`
    }).join('\n')
    const context = vm.createContext(state)
    vm.runInContext(ts.transpileModule(code, {
        compilerOptions: { target: ts.ScriptTarget.ES2021 },
    }).outputText, context)
    return context
}

function harness(get: (url: string) => Promise<any>) {
    const guard = new ProjectRequestGuard()
    const state: Record<string, any> = {
        console: { error() {} },
        token: { value: 'synthetic' },
        currentProject: { value: null },
        projectList: { value: [{ id: 1 }] },
        projectJobs: { value: [] },
        interaction: { value: null },
        scenePromptMap: { value: {} },
        scenePromptLoadingMap: { value: {} },
        loading: { value: false },
        loadingText: { value: '' },
        switchingProject: { value: false },
        drawerOpen: { value: false },
        latestGenerationJob: { value: null },
        selectedOption: { value: '' }, customInput: { value: '' },
        logline: { value: '' },
        createProjectSubmitting: { value: false },
        submitChoiceSubmitting: { value: false },
        canEditCurrentProject: { value: true },
        ownsCurrentProject: { value: true },
        ElMessage: { error() {}, warning() {}, success() {} },
        ElMessageBox: { confirm: async () => {} },
        requireOnlineAction: () => true,
        api: { get },
        confirmQuickReviewLeave: async () => true,
        upsertProjectListItem() {}, resetQuickReviewState() {}, startPolling() {}, fetchProjects: async () => {},
        syncProjectTokensFromResponse() {}, initializeQuickReview() {},
        normalizeProjectStatus: (value: unknown) => String(value || '').toLowerCase(),
        toTextValue: (value: unknown) => String(value ?? ''),
        analyzeLogline: async () => { throw new Error('failed details must not start analysis') },
        fetchProjectJobs: async () => [],
        analyzeProjectRequest,
    }
    state.currentProjectRevision = () => state.currentProject.value?.context_revision || ''
    state.beginProjectRequest = (id: number, revision: string, channel: string) => guard.begin(id, revision, channel)
    state.isProjectRequestCurrent = (request: any) => guard.isCurrent(request, state.currentProject.value?.id, state.currentProjectRevision())
    state.isProjectRequestForActiveProject = (request: any) => guard.isSameProjectEpoch(request, state.currentProject.value?.id)
    state.invalidateProjectRequests = () => guard.invalidate()
    state.startProjectLoading = (request: any) => { guard.startLoading(request); state.loading.value = true }
    state.finishProjectLoading = (request: any) => {
        if (guard.mayFinishLoading(request, state.currentProject.value?.id)) state.loading.value = false
    }
    return { guard, state }
}

test('实际 loadProject 详情404会回到可操作空态，不残留loading/switching', async () => {
    const { state } = harness(async () => { throw { response: { status: 404 } } })
    const app = install(state, [
        ['fetchProjectDetail', 'fetchProjectJobs'],
        ['startNewProject', 'loadProject'],
        ['loadProject', 'deleteProject'],
    ])
    await app.loadProject({ id: 1, context_revision: 'setup-v2:0:0', status: 'pending' })
    assert.equal(state.currentProject.value, null)
    assert.equal(state.loading.value, false)
    assert.equal(state.switchingProject.value, false)
})

test('旧详情404不能清掉重新打开的同一项目', async () => {
    let rejectDetail: (reason: unknown) => void = () => {}
    const { state, guard } = harness(() => new Promise((_resolve, reject) => { rejectDetail = reject }))
    state.currentProject.value = { id: 1, context_revision: 'setup-v2:0:0' }
    const app = install(state, [['fetchProjectDetail', 'fetchProjectJobs'], ['startNewProject', 'loadProject']])
    const pending = app.fetchProjectDetail(1)
    guard.invalidate()
    state.currentProject.value = { id: 1, context_revision: 'setup-v2:0:0', title: '重新打开' }
    state.loading.value = true
    rejectDetail({ response: { status: 404 } })
    await pending
    assert.equal(state.currentProject.value.title, '重新打开')
    assert.equal(state.loading.value, true)
})

test('非活动A的旧后续详情请求不能抢占B正在加载的detail通道', async () => {
    let resolveDetail: (value: unknown) => void = () => {}
    const urls: string[] = []
    const { state } = harness((url) => {
        urls.push(url)
        return new Promise((resolve) => { resolveDetail = resolve })
    })
    state.currentProject.value = { id: 2, context_revision: 'setup-v2:0:0' }
    const app = install(state, [['fetchProjectDetail', 'fetchProjectJobs'], ['startNewProject', 'loadProject']])
    const pendingB = app.fetchProjectDetail(2)
    assert.equal(await app.fetchProjectDetail(1), null)
    resolveDetail({ data: { id: 2, context_revision: 'setup-v2:0:0', scenes: [{ id: 22 }] } })
    const resultB = await pendingB
    assert.deepEqual(urls, ['/projects/2'])
    assert.equal(resultB.scenes[0].id, 22)
})

test('实际 analyze 在生成请求等待时切项目，不再发旧项目的详情/jobs后续请求', async () => {
    let releaseGeneration: (value: unknown) => void = () => {}
    let started: () => void = () => {}
    const generating = new Promise<void>((resolve) => { started = resolve })
    const { state, guard } = harness(async () => ({}))
    let details = 0
    let jobs = 0
    state.currentProject.value = { id: 1, context_revision: 'setup-v2:0:0', status: 'pending', scenes: [] }
    state.fetchProjectDetail = async () => { details += 1; return state.currentProject.value }
    state.fetchProjectJobs = async () => { jobs += 1; return [] }
    state.api.post = async (url: string, _payload?: unknown, config?: any) => {
        if (url.endsWith('/analyze')) return { data: { type: 'completed' } }
        assert.equal(url, '/projects/1/generate_scenes')
        assert.equal(config?.params?.selected_option, 'auto')
        assert.equal(config?.params?.context_revision, 'setup-v2:0:0')
        started()
        return new Promise((resolve) => { releaseGeneration = resolve })
    }
    const app = install(state, [['analyzeLogline', 'submitChoice']])
    const pending = app.analyzeLogline(1)
    await generating
    guard.invalidate()
    state.currentProject.value = { id: 2, context_revision: 'setup-v2:0:0', scenes: [] }
    releaseGeneration({ data: { status: 'queued' } })
    await pending
    assert.equal(details, 1)
    assert.equal(jobs, 0)
})

test('实际删除成功清理旧loading，即使分析已推进同项目的revision', async () => {
    let releaseDelete: (value: unknown) => void = () => {}
    let started: () => void = () => {}
    const deleting = new Promise<void>((resolve) => { started = resolve })
    const { state, guard } = harness(async () => ({}))
    state.currentProject.value = { id: 1, context_revision: 'setup-v2:0:0' }
    state.loading.value = true
    state.switchingProject.value = true
    const oldAnalysis = guard.begin(1, 'setup-v2:0:0', 'analysis')
    guard.startLoading(oldAnalysis)
    state.fetchProjects = async () => {}
    state.api.delete = async (url: string) => {
        assert.equal(url, '/projects/1')
        started()
        return new Promise((resolve) => { releaseDelete = resolve })
    }
    const app = install(state, [['startNewProject', 'loadProject'], ['deleteProject', 'regenerateScene']])
    const pending = app.deleteProject()
    await deleting
    state.currentProject.value.context_revision = 'setup-v2:0:1'
    releaseDelete({ data: { status: 'success' } })
    await pending
    if (guard.mayFinishLoading(oldAnalysis, state.currentProject.value?.id)) state.loading.value = false
    assert.equal(state.currentProject.value, null)
    assert.equal(state.loading.value, false)
    assert.equal(state.switchingProject.value, false)
})

test('实际 guided 分析将顶层保存稿标志传到返回快速审查入口', async () => {
    const { state } = harness(async () => ({}))
    state.currentProject.value = { id: 1, context_revision: 'setup-v2:1:2' }
    state.api.post = async () => ({ data: {
        type: 'interaction_required',
        setup_mode: 'guided',
        saved_draft_available: true,
        draft_stale: true,
        payload: { field: 'movie_duration', question: '电影时长', options: [] },
    } })
    const app = install(state, [['analyzeLogline', 'submitChoice']])
    await app.analyzeLogline(1)
    assert.equal(state.interaction.value.field, 'movie_duration')
    assert.equal(state.interaction.value.saved_draft_available, true)
    assert.equal(state.interaction.value.draft_stale, true)
})

test('viewer 打开无场次项目只进入等待态，不自动发起分析或生成写入', async () => {
    const { state } = harness(async () => ({}))
    let analyses = 0
    state.fetchProjectDetail = async () => ({
        id: 1,
        access_role: 'viewer',
        context_revision: 'setup-v2:0:0',
        status: 'pending',
        scenes: [],
    })
    state.fetchProjectJobs = async () => []
    state.analyzeLogline = async () => { analyses += 1 }
    const app = install(state, [['loadProject', 'deleteProject']])
    await app.loadProject({ id: 1, access_role: 'viewer', context_revision: 'setup-v2:0:0', status: 'pending' })
    assert.equal(analyses, 0)
    assert.equal(state.currentProject.value.access_role, 'viewer')
    assert.equal(state.loading.value, false)
})

test('延迟内容审核期间连续创建项目只审核和写入一次，锁在请求结束后释放', async () => {
    let releaseReview: (value: string) => void = () => {}
    let reviewStarted: () => void = () => {}
    const reviewPending = new Promise<void>((resolve) => { reviewStarted = resolve })
    const { state } = harness(async () => ({}))
    const writes: string[] = []
    let reviews = 0
    state.logline.value = '一个创意'
    state.reviewAndMaybeRewriteInput = async () => {
        reviews += 1
        reviewStarted()
        return new Promise((resolve) => { releaseReview = resolve })
    }
    state.api.post = async (url: string) => {
        writes.push(url)
        return { data: { id: 1, context_revision: '', scenes: [] } }
    }
    state.analyzeLogline = async () => {}
    const app = install(state, [['createProject', 'resetQuickReviewState']])
    const first = app.createProject()
    await reviewPending
    assert.equal(state.createProjectSubmitting.value, true)
    await app.createProject()
    assert.equal(reviews, 1)
    releaseReview('一个改写后的创意')
    await first
    assert.deepEqual(writes, ['/projects/'])
    assert.equal(state.createProjectSubmitting.value, false)
})

test('延迟内容审核期间连续提交选项只审核和写入一次，锁不会停留', async () => {
    let releaseReview: (value: string) => void = () => {}
    let reviewStarted: () => void = () => {}
    const reviewPending = new Promise<void>((resolve) => { reviewStarted = resolve })
    const { state } = harness(async () => ({}))
    const writes: string[] = []
    let reviews = 0
    state.currentProject.value = { id: 1, context_revision: 'setup-v2:0:0' }
    state.interaction.value = { field: 'genre', context_revision: 'setup-v2:0:0' }
    state.customInput.value = '科幻'
    state.reviewAndMaybeRewriteInput = async () => {
        reviews += 1
        reviewStarted()
        return new Promise((resolve) => { releaseReview = resolve })
    }
    state.api.post = async (url: string) => {
        writes.push(url)
        return { data: {} }
    }
    state.analyzeLogline = async () => {}
    const app = install(state, [['submitChoice', 'handleOptionSelect']])
    const first = app.submitChoice()
    await reviewPending
    assert.equal(state.submitChoiceSubmitting.value, true)
    await app.submitChoice()
    assert.equal(reviews, 1)
    releaseReview('改写后的科幻')
    await first
    assert.deepEqual(writes, ['/projects/1/interact'])
    assert.equal(state.submitChoiceSubmitting.value, false)
})

test('审核等待期间出现新的自定义输入时保留新值且取消旧提交', async () => {
    let releaseReview: (value: string) => void = () => {}
    let reviewStarted: () => void = () => {}
    const reviewPending = new Promise<void>((resolve) => { reviewStarted = resolve })
    const { state } = harness(async () => ({}))
    const writes: string[] = []
    const warnings: string[] = []
    state.ElMessage = { error() {}, success() {}, warning: (message: string) => warnings.push(message) }
    state.currentProject.value = { id: 1, context_revision: 'setup-v2:0:0' }
    state.interaction.value = { field: 'genre', context_revision: 'setup-v2:0:0' }
    state.customInput.value = '原始输入'
    state.reviewAndMaybeRewriteInput = async () => {
        reviewStarted()
        return new Promise((resolve) => { releaseReview = resolve })
    }
    state.api.post = async (url: string) => {
        writes.push(url)
        return { data: {} }
    }
    const app = install(state, [['submitChoice', 'handleOptionSelect']])
    const pending = app.submitChoice()
    await reviewPending
    state.customInput.value = '更新后的输入'
    releaseReview('审核后的旧输入')
    await pending
    assert.equal(state.customInput.value, '更新后的输入')
    assert.deepEqual(writes, [])
    assert.equal(state.interaction.value.field, 'genre')
    assert.equal(state.submitChoiceSubmitting.value, false)
    assert.equal(warnings.length, 1)
})

test('非 owner 直接调用删除函数时不发删除请求', async () => {
    const { state } = harness(async () => ({}))
    let deletions = 0
    state.currentProject.value = { id: 1, context_revision: 'setup-v2:0:0' }
    state.ownsCurrentProject.value = false
    state.api.delete = async () => { deletions += 1 }
    const app = install(state, [['deleteProject', 'regenerateScene']])
    await app.deleteProject()
    assert.equal(deletions, 0)
})

test('场次请求等待时切到 B，A 的转写和重写回包不污染 B 或显示旧提示', async () => {
    let releaseRegenerate: () => void = () => {}
    let releasePrompt: () => void = () => {}
    let regenerateStarted: () => void = () => {}
    let promptStarted: () => void = () => {}
    const regeneratePending = new Promise<void>((resolve) => { regenerateStarted = resolve })
    const promptPending = new Promise<void>((resolve) => { promptStarted = resolve })
    const { state, guard } = harness(async () => ({}))
    const success: string[] = []
    const errors: string[] = []
    let details = 0
    state.ElMessage = { success: (message: string) => success.push(message), error: (message: string) => errors.push(message), warning() {} }
    state.currentProject.value = {
        id: 1,
        context_revision: 'setup-v2:0:0',
        scenes: [{ id: 11, scene_index: 1, status: 'completed', content: 'A 内容' }],
    }
    state.api.post = async (url: string) => {
        if (url.endsWith('/regenerate')) {
            regenerateStarted()
            return new Promise((resolve) => { releaseRegenerate = () => resolve({ data: {} }) })
        }
        assert.equal(url, '/projects/1/scenes/1/to_prompt')
        promptStarted()
        return new Promise((resolve) => { releasePrompt = () => resolve({ data: { prompt: 'A 提示词' } }) })
    }
    state.fetchProjectDetail = async () => { details += 1; return state.currentProject.value }
    state.toTextValue = (value: unknown) => String(value ?? '')
    const app = install(state, [
        ['regenerateScene', 'getScenePrompt'],
        ['convertSceneToPrompt', 'exportScript'],
    ])
    const regenerate = app.regenerateScene(11, 1)
    await regeneratePending
    const prompt = app.convertSceneToPrompt({ id: 11, scene_index: 1 })
    await promptPending
    guard.invalidate()
    state.currentProject.value = {
        id: 2,
        context_revision: 'setup-v2:0:0',
        scenes: [{ id: 21, scene_index: 1, status: 'completed', content: 'B 内容' }],
    }
    state.scenePromptMap.value = { 21: 'B 提示词' }
    state.scenePromptLoadingMap.value = { 21: true }
    releaseRegenerate()
    releasePrompt()
    await Promise.all([regenerate, prompt])
    assert.equal(state.currentProject.value.scenes[0].content, 'B 内容')
    assert.deepEqual(state.scenePromptMap.value, { 21: 'B 提示词' })
    assert.deepEqual(state.scenePromptLoadingMap.value, { 21: true })
    assert.equal(details, 0)
    assert.deepEqual(success, [])
    assert.deepEqual(errors, [])
})

test('实际 getProjectTitle 优先保留权威 project.title 的内部标点', () => {
    const app = install({
        normalizeTitleDisplay,
        toTextValue: (value: unknown) => String(value ?? ''),
    }, [
        ['extractStoryTitleText', 'getProjectTitle'],
        ['getProjectTitle', 'getProjectDisplayText'],
    ])
    assert.equal(
        app.getProjectTitle({
            title: '标题： 《暮色: 第二章—归来》',
            global_context: { title: '旧标题' },
        }),
        '暮色: 第二章—归来',
    )
})
