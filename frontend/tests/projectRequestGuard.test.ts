import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import ts from 'typescript'
import {
    ProjectRequestGuard,
    analyzeProjectRequest,
    applyQuickReviewCandidateAtomically,
    submitQuickReviewRequest,
} from '../src/projectRequestGuard.ts'

test('实际 logout 清理加载和草案，旧 finally 不会使重新登录卡住', () => {
    const guard = new ProjectRequestGuard()
    const oldRequest = guard.begin(1, 'setup-v2:1:1', 'analysis')
    guard.startLoading(oldRequest)
    const state: Record<string, any> = {
        console,
        token: { value: 'synthetic-token' },
        user: { value: { id: 1 } },
        showAdmin: { value: false },
        projectList: { value: [] },
        currentProject: { value: { id: 1 } },
        projectJobs: { value: [] },
        interaction: { value: { field: 'quick_review' } },
        scenePromptMap: { value: {} },
        scenePromptLoadingMap: { value: {} },
        loading: { value: true },
        switchingProject: { value: true },
        drawerOpen: { value: true },
        projectToolsVisible: { value: true },
        candidateVisible: { value: true },
        stopPolling() {},
        invalidateProjectRequests() { guard.invalidate() },
        localStorage: { removeItem() {} },
        ElMessage: { info() {} },
    }
    state.resetQuickReviewState = () => { state.candidateVisible.value = false }
    const source = readFileSync(new URL('../src/App.vue', import.meta.url), 'utf8')
    const start = source.indexOf('const logout = () =>')
    const end = source.indexOf('const changePassword =', start)
    assert.ok(start >= 0 && end > start)
    const script = ts.transpileModule(source.slice(start, end) + '\nlogout();', {
        compilerOptions: { target: ts.ScriptTarget.ES2021 },
    }).outputText
    vm.runInNewContext(script, state)
    if (guard.mayFinishLoading(oldRequest, state.currentProject.value?.id)) {
        state.loading.value = false
    }
    state.token.value = 'new-synthetic-token'
    assert.equal(state.loading.value, false)
    assert.equal(state.switchingProject.value, false)
    assert.equal(state.candidateVisible.value, false)
    assert.equal(state.drawerOpen.value, false)
    assert.equal(state.projectToolsVisible.value, false)
    assert.equal(guard.isCurrent(oldRequest, 1, 'setup-v2:1:1'), false)
})

test('A 的审核等待期间切到 B 后，A 不再是可继续提交的请求', () => {
    const guard = new ProjectRequestGuard()
    const reviewA = guard.begin(1, 'setup-v2:2:3')
    guard.startLoading(reviewA)
    guard.invalidate()
    const loadingB = guard.begin(2, 'setup-v2:5:7')
    guard.startLoading(loadingB)

    assert.equal(guard.isCurrent(reviewA, 2, 'setup-v2:5:7'), false)
    assert.equal(guard.mayFinishLoading(reviewA, 2, 'setup-v2:5:7'), false)
})

test('慢分析 A 不会污染 B，旧 finally 也不能关闭 B 的 loading', () => {
    const guard = new ProjectRequestGuard()
    const analysisA = guard.begin(1, 'setup-v2:2:3')
    guard.startLoading(analysisA)
    guard.invalidate()
    const analysisB = guard.begin(2, 'setup-v2:5:7')
    guard.startLoading(analysisB)

    assert.equal(guard.isCurrent(analysisA, 2, 'setup-v2:5:7'), false)
    assert.equal(guard.mayFinishLoading(analysisA, 2, 'setup-v2:5:7'), false)
    assert.equal(guard.isCurrent(analysisB, 2, 'setup-v2:5:7'), true)
    assert.equal(guard.mayFinishLoading(analysisB, 2, 'setup-v2:5:7'), true)
})

test('同一项目中最新请求正常完成，旧 revision 请求失效', () => {
    const guard = new ProjectRequestGuard()
    const oldRequest = guard.begin(7, 'setup-v2:3:4')
    const latestRequest = guard.begin(7, 'setup-v2:3:5')
    guard.startLoading(latestRequest)

    assert.equal(guard.isCurrent(oldRequest, 7, 'setup-v2:3:5'), false)
    assert.equal(guard.isCurrent(latestRequest, 7, 'setup-v2:3:5'), true)
    assert.equal(guard.mayFinishLoading(latestRequest, 7, 'setup-v2:3:5'), true)
})

test('同项目同 revision 的后一分析请求会取代前一请求，版本推进后仍可结束自己的 loading', async () => {
    const guard = new ProjectRequestGuard()
    const first = guard.begin(7, 'setup-v2:3:4', 'analysis')
    const latest = guard.begin(7, 'setup-v2:3:4', 'analysis')
    guard.startLoading(latest)
    const response = await analyzeProjectRequest({
        request: latest,
        isCurrent: () => guard.isCurrent(latest, 7, 'setup-v2:3:4'),
        post: async () => ({ data: { context_revision: 'setup-v2:3:5' } }),
    })

    assert.equal(guard.isCurrent(first, 7, 'setup-v2:3:4'), false)
    assert.equal(response.status, 'success')
    assert.equal(guard.mayFinishLoading(latest, 7, 'setup-v2:3:5'), true)
})

test('AI 候选一次性生成新值，不改变原草案，冲突时拒绝应用', () => {
    const current = Object.freeze({ title: '旧标题', theme: '旧主题' })
    const base = Object.freeze({ title: '旧标题', theme: '旧主题' })
    const next = applyQuickReviewCandidateAtomically(current, base, [
        { field: 'title', after: '新标题' },
        { field: 'theme', after: '新主题' },
    ])
    assert.deepEqual(current, { title: '旧标题', theme: '旧主题' })
    assert.deepEqual(next, { title: '新标题', theme: '新主题' })
    assert.equal(applyQuickReviewCandidateAtomically({ title: '手改标题', theme: '旧主题' }, base, [{ field: 'theme', after: '新主题' }]), null)
})

test('submitQuickReview 使用的实际请求逻辑在审核 await 后切到 B 时不向 B 发写入', async () => {
    const guard = new ProjectRequestGuard()
    const requestA = guard.begin(1, 'setup-v2:2:3')
    let releaseReview: ((value: string) => void) | undefined
    const pendingReview = new Promise<string>((resolve) => { releaseReview = resolve })
    const posts: Array<{ url: string, payload: Record<string, unknown> }> = []

    const submission = submitQuickReviewRequest({
        request: requestA,
        isCurrent: () => guard.isCurrent(requestA, 1, 'setup-v2:2:3'),
        values: { title: 'A' },
        editedFields: ['title'],
        reviewInput: async () => pendingReview,
        getLabel: () => '故事题目',
        post: async (url, payload) => {
            posts.push({ url, payload })
            return { data: {} }
        },
    })
    guard.invalidate()
    guard.begin(2, 'setup-v2:8:9')
    releaseReview?.('改写 A')

    assert.deepEqual(await submission, { status: 'stale' })
    assert.deepEqual(posts, [])
})

test('analyzeLogline 使用的实际请求逻辑忽略 A 的慢回包', async () => {
    const guard = new ProjectRequestGuard()
    const requestA = guard.begin(1, 'setup-v2:2:3')
    let resolveAnalysis: ((value: { data: unknown }) => void) | undefined
    const pendingAnalysis = new Promise<{ data: unknown }>((resolve) => { resolveAnalysis = resolve })

    const analysis = analyzeProjectRequest({
        request: requestA,
        isCurrent: () => guard.isCurrent(requestA, 1, 'setup-v2:2:3'),
        post: async () => pendingAnalysis,
    })
    guard.invalidate()
    guard.begin(2, 'setup-v2:4:5')
    resolveAnalysis?.({ data: { type: 'interaction_required', payload: { field: 'quick_review' } } })

    assert.deepEqual(await analysis, { status: 'stale' })
})
