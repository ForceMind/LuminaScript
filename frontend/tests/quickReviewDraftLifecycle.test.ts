import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import ts from 'typescript'
import { draftValuesDiffer, savedDraftResult } from '../src/quickReviewDraft.ts'

const source = readFileSync(new URL('../src/App.vue', import.meta.url), 'utf8')

const install = (state: Record<string, any>, name: string, next: string) => {
    const start = source.indexOf(`const ${name} =`)
    const end = source.indexOf(`const ${next} =`, start)
    assert.ok(start >= 0 && end > start)
    const context = vm.createContext(state)
    vm.runInContext(ts.transpileModule(
        source.slice(start, end) + `\nglobalThis.${name} = ${name};`,
        { compilerOptions: { target: ts.ScriptTarget.ES2021 } },
    ).outputText, context)
    return context
}

const draftActionState = (post: (url: string, body: any) => Promise<any>) => {
    const state: Record<string, any> = {
        currentProject: { value: { id: 1, context_revision: 'setup-v2:2:3' } },
        interaction: { value: { field: 'quick_review', context_revision: 'setup-v2:2:3' } },
        interactionField: { value: 'quick_review' },
        quickReviewDraftStale: { value: false },
        quickReviewValues: { value: { title: '请求时标题' } },
        quickReviewBaselineValues: { value: { title: '生成基线' } },
        quickReviewEditedFields: { value: ['title'] },
        quickReviewAiAdjustedFields: { value: [] },
        quickReviewSavedValues: { value: { title: '生成基线' } },
        quickReviewSavedAt: { value: '' },
        api: { post },
        requireOnlineAction: () => true,
        toTextValue: (value: unknown) => String(value ?? ''),
        currentProjectRevision: () => state.currentProject.value.context_revision,
        beginProjectRequest: () => ({ request: true }),
        isProjectRequestCurrent: () => true,
        startProjectLoading() {}, finishProjectLoading() {},
        syncProjectTokensFromResponse: (payload: any, updateInteraction: boolean) => {
            state.currentProject.value.context_revision = payload.context_revision
            if (updateInteraction) state.interaction.value.context_revision = payload.context_revision
        },
        savedDraftResult,
        draftValuesDiffer,
        applyQuickReviewDraftResponse() { throw new Error('new edits must not be overwritten') },
        ElMessage: { success() {}, error() {} },
        formatApiErrorDetail: (_detail: unknown, fallback: string) => fallback,
        resetQuickReviewState() {},
        analyzeLogline: async () => {},
    }
    return state
}

test('实际保存动作固定请求快照，回包推进 token 但不覆盖保存期间的新编辑', async () => {
    let resolvePost: (value: any) => void = () => {}
    let requestBody: any
    const state = draftActionState(async (_url, body) => {
        requestBody = body
        return new Promise((resolve) => { resolvePost = resolve })
    })
    const app = install(state, 'runQuickReviewDraftAction', 'switchQuickReviewToGuided')
    const pending = app.runQuickReviewDraftAction('save')
    state.quickReviewValues.value = { title: '保存期间继续编辑' }
    resolvePost({ data: { context_revision: 'setup-v2:2:4', quick_setup_draft: { saved_at: '2026-09-03T12:00:00Z' } } })

    assert.equal(await pending, true)
    assert.equal(requestBody.values.title, '请求时标题')
    assert.equal(state.quickReviewValues.value.title, '保存期间继续编辑')
    assert.equal(state.quickReviewSavedValues.value.title, '请求时标题')
    assert.equal(state.interaction.value.context_revision, 'setup-v2:2:4')
})

test('实际 stale 草案拒绝 save，且不发请求', async () => {
    let calls = 0
    const state = draftActionState(async () => { calls += 1; return { data: {} } })
    state.quickReviewDraftStale.value = true
    const app = install(state, 'runQuickReviewDraftAction', 'switchQuickReviewToGuided')
    assert.equal(await app.runQuickReviewDraftAction('save'), false)
    assert.equal(calls, 0)
})

test('实际离开确认关闭即取消，放弃未保存修改不会删除服务器草案', async () => {
    const calls: string[] = []
    const state: Record<string, any> = {
        quickReviewHasUnsavedChanges: { value: true },
        quickReviewDraftStale: { value: false },
        ElMessage: { warning() {} },
        runQuickReviewDraftAction: async (action: string) => { calls.push(action); return true },
    }
    state.ElMessageBox = { confirm: async () => { throw 'close' } }
    const app = install(state, 'confirmQuickReviewLeave', 'requestStartNewProject')
    assert.equal(await app.confirmQuickReviewLeave(), false)
    assert.deepEqual(calls, [])

    state.ElMessageBox.confirm = async () => { throw 'cancel' }
    assert.equal(await app.confirmQuickReviewLeave(), true)
    assert.deepEqual(calls, [])
})

test('新生成稿切guided仍要求明确保存或放弃选择，不会静默切换', async () => {
    const actions: string[] = []
    const state: Record<string, any> = {
        quickReviewHasUnsavedChanges: { value: false },
        quickReviewSavedAt: { value: '' },
        ElMessageBox: { confirm: async () => { throw 'close' } },
        runQuickReviewDraftAction: async (action: string) => { actions.push(action); return true },
    }
    const app = install(state, 'switchQuickReviewToGuided', 'copyQuickReviewDraft')
    await app.switchQuickReviewToGuided()
    assert.deepEqual(actions, [])

    state.ElMessageBox.confirm = async () => {}
    await app.switchQuickReviewToGuided()
    assert.deepEqual(actions, ['save_guided'])
})

test('实际 save_guided 回包发现新编辑时保留内容并进入只读保护，不 reset 草案', async () => {
    const state = draftActionState(async () => ({ data: { context_revision: 'setup-v2:2:4' } }))
    let resetCalls = 0
    state.api.post = async () => {
        state.quickReviewValues.value = { title: '切换期间的新编辑' }
        return { data: { context_revision: 'setup-v2:2:4' } }
    }
    state.resetQuickReviewState = () => { resetCalls += 1 }
    const app = install(state, 'runQuickReviewDraftAction', 'switchQuickReviewToGuided')
    assert.equal(await app.runQuickReviewDraftAction('save_guided'), false)
    assert.equal(state.quickReviewValues.value.title, '切换期间的新编辑')
    assert.equal(state.quickReviewDraftStale.value, true)
    assert.equal(resetCalls, 0)
})

test('实际 stale 草案禁止确认和 AI 请求', async () => {
    let postCalls = 0
    const base: Record<string, any> = {
        currentProject: { value: { id: 1 } },
        interaction: { value: { context_revision: 'setup-v2:2:3' } },
        interactionField: { value: 'quick_review' },
        quickReviewDraftStale: { value: true },
        ElMessage: { warning() {} },
        api: { post: async () => { postCalls += 1; return { data: {} } } },
        requireOnlineAction: () => true,
    }
    const confirmApp = install(base, 'submitQuickReview', 'analyzeLogline')
    await confirmApp.submitQuickReview('confirm')
    const aiApp = install(base, 'quickReviewAiRequest', 'closeQuickReviewFieldOptions')
    await aiApp.quickReviewAiRequest(undefined, 'related')
    assert.equal(postCalls, 0)
})

test('save_guided 竞态留下的 stale 本地编辑仍触发刷新和离开保护', async () => {
    const unloadState: Record<string, any> = {
        hasPendingPwaWork: { value: true },
        pwaReloadGuard: { consumeBeforeUnloadBypass: () => false },
    }
    const unloadApp = install(unloadState, 'handleBeforeUnload', 'logout')
    const event: Record<string, any> = { prevented: false, preventDefault() { this.prevented = true } }
    unloadApp.handleBeforeUnload(event)
    assert.equal(event.prevented, true)
    assert.equal(event.returnValue, '')

    const leaveState: Record<string, any> = {
        quickReviewHasUnsavedChanges: { value: true },
        quickReviewDraftStale: { value: true },
        ElMessageBox: { confirm: async () => {} },
        ElMessage: { warning() {} },
        runQuickReviewDraftAction: async () => { throw new Error('stale leave must not attempt save') },
    }
    const leaveApp = install(leaveState, 'confirmQuickReviewLeave', 'requestStartNewProject')
    assert.equal(await leaveApp.confirmQuickReviewLeave(), true)
})

test('beforeunload 统一保护快速草案、创意、自定义输入和已选选项', () => {
    const values: Record<string, any> = {
        quickReviewHasUnsavedChanges: { value: false },
        logline: { value: '' },
        customInput: { value: '' },
        selectedOption: { value: '' },
        computed: (factory: () => unknown) => ({ get value() { return factory() } }),
    }
    const computedApp = install(values, 'hasPendingPwaWork', 'hasPendingNonDraftPwaWork')
    const cases = [
        ['quickReviewHasUnsavedChanges', true],
        ['logline', '未提交创意'],
        ['customInput', '未提交输入'],
        ['selectedOption', 'choice'],
    ] as const
    for (const [name, value] of cases) {
        values.quickReviewHasUnsavedChanges.value = false
        values.logline.value = ''
        values.customInput.value = ''
        values.selectedOption.value = ''
        values[name].value = value
        assert.equal(computedApp.hasPendingPwaWork.value, true)
    }
})

test('离线时需网操作会停止轮询并给出明确反馈', () => {
    let stops = 0
    let warnings = 0
    const state: Record<string, any> = {
        pwaManager: { snapshot: { browserOnline: false } },
        stopPolling: () => { stops += 1 },
        ElMessage: { warning: () => { warnings += 1 } },
    }
    const app = install(state, 'requireOnlineAction', 'quickReviewCanSave')
    assert.equal(app.requireOnlineAction(), false)
    assert.equal(stops, 1)
    assert.equal(warnings, 1)

    state.pwaManager.snapshot.browserOnline = true
    assert.equal(app.requireOnlineAction(), true)
    assert.equal(stops, 1)
})

const pwaUpdateState = () => {
    const state: Record<string, any> = {
        pwaUpdateActionVisible: { value: true },
        pwaSnapshot: { value: { updateRevision: 7, updateState: 'waiting' } },
        quickReviewHasUnsavedChanges: { value: true },
        quickReviewDraftStale: { value: false },
        hasPendingNonDraftPwaWork: { value: false },
        ElMessageBox: { confirm: async () => {} },
        ElMessage: { info() {}, warning() {} },
        copyQuickReviewDraft() {},
        runQuickReviewDraftAction: async () => true,
        pwaManager: {
            activateWaitingWorker: () => true,
            refreshAfterExternalUpdate: () => true,
        },
        pwaReloadGuard: {
            approveCurrentSnapshot() {},
            cancelApproval() {},
        },
    }
    return state
}

test('PWA “保存并更新”在保存后重查草案和更新目标', async () => {
    const calls: any[] = []
    const state = pwaUpdateState()
    state.runQuickReviewDraftAction = async (action: string) => {
        calls.push(action)
        state.quickReviewHasUnsavedChanges.value = false
        return true
    }
    state.pwaManager.activateWaitingWorker = (revision: number) => {
        calls.push(['activate', revision])
        return true
    }
    const app = install(state, 'requestPwaUpdate', 'handleAccountCommand')
    assert.equal(await app.requestPwaUpdate(), true)
    assert.deepEqual(calls, ['save', ['activate', 7]])

    calls.length = 0
    state.quickReviewHasUnsavedChanges.value = true
    state.runQuickReviewDraftAction = async () => true
    assert.equal(await app.requestPwaUpdate(), false)
    assert.deepEqual(calls, [])

    state.quickReviewHasUnsavedChanges.value = true
    state.runQuickReviewDraftAction = async () => {
        state.quickReviewHasUnsavedChanges.value = false
        state.pwaSnapshot.value.updateRevision = 8
        return true
    }
    assert.equal(await app.requestPwaUpdate(), false)
})

test('过期草案先复制则不更新，明确放弃后才继续', async () => {
    let activations = 0
    let copies = 0
    const state = pwaUpdateState()
    state.quickReviewDraftStale.value = true
    state.copyQuickReviewDraft = () => { copies += 1 }
    state.pwaManager.activateWaitingWorker = () => { activations += 1; return true }
    const app = install(state, 'requestPwaUpdate', 'handleAccountCommand')

    assert.equal(await app.requestPwaUpdate(), false)
    assert.equal(copies, 1)
    assert.equal(activations, 0)

    state.ElMessageBox.confirm = async () => { throw 'cancel' }
    assert.equal(await app.requestPwaUpdate(), true)
    assert.equal(activations, 1)
})

test('外部已更新状态只在用户确认后刷新当前页', async () => {
    let refreshes = 0
    const state = pwaUpdateState()
    state.pwaSnapshot.value = { updateRevision: 11, updateState: 'refresh-required' }
    state.quickReviewHasUnsavedChanges.value = false
    state.pwaManager.refreshAfterExternalUpdate = (revision: number) => {
        assert.equal(revision, 11)
        refreshes += 1
        return true
    }
    state.ElMessageBox.confirm = async () => { throw 'cancel' }
    const app = install(state, 'requestPwaUpdate', 'handleAccountCommand')
    assert.equal(await app.requestPwaUpdate(), false)
    assert.equal(refreshes, 0)

    state.ElMessageBox.confirm = async () => {}
    assert.equal(await app.requestPwaUpdate(), true)
    assert.equal(refreshes, 1)
})
