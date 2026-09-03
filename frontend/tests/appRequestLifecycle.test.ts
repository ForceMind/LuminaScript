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
        ElMessage: { error() {}, warning() {}, success() {} },
        ElMessageBox: { confirm: async () => {} },
        api: { get },
        confirmQuickReviewLeave: async () => true,
        upsertProjectListItem() {}, resetQuickReviewState() {}, startPolling() {},
        syncProjectTokensFromResponse() {}, initializeQuickReview() {},
        normalizeProjectStatus: (value: unknown) => String(value || '').toLowerCase(),
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
    state.api.post = async (url: string) => {
        if (url.endsWith('/analyze')) return { data: { type: 'completed' } }
        assert.equal(url, '/projects/1/generate_scenes')
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
