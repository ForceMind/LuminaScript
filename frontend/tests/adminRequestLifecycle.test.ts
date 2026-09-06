import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import ts from 'typescript'

const source = readFileSync(new URL('../src/components/AdminDashboard.vue', import.meta.url), 'utf8')

function install(state: Record<string, any>) {
    const start = source.indexOf('const getSystemLogRequestParams')
    const end = source.indexOf('const copySystemLogs', start)
    assert.ok(start >= 0 && end > start)
const code = `let systemLogTimer = null;
let systemLogRequestSequence = 0;
let systemLogRequestActive = true;
let systemLogInFlight = null;
` + source.slice(start, end) + `
globalThis.fetchSystemLogs = fetchSystemLogs;
globalThis.handleSystemLogVisibilityChange = handleSystemLogVisibilityChange;
globalThis.disposeSystemLogRequests = disposeSystemLogRequests;
globalThis.syncSystemLogAutoRefresh = syncSystemLogAutoRefresh;
`
    const context = vm.createContext(state)
    vm.runInContext(ts.transpileModule(code, {
        compilerOptions: { target: ts.ScriptTarget.ES2021 },
    }).outputText, context)
    return context
}

function deferred<T>() {
    let resolve: (value: T) => void = () => {}
    let reject: (reason?: unknown) => void = () => {}
    const promise = new Promise<T>((resolvePromise, rejectPromise) => {
        resolve = resolvePromise
        reject = rejectPromise
    })
    return { promise, resolve, reject }
}

function logState(get: (url: string, config: any) => Promise<any>) {
    const intervals: Array<() => void> = []
    const state: Record<string, any> = {
        systemLogSource: { value: 'worker' },
        systemLogLines: { value: 300 },
        systemLogKeyword: { value: '' },
        systemLogAutoRefresh: { value: true },
        activeTab: { value: 'system_logs' },
        systemLogLoading: { value: false },
        systemLogViewer: { value: null },
        systemLogData: {
            path: '', available: false, size_bytes: 0, updated_at: '', line_count: 0,
            truncated: false, content: '', error: '', source: '', lines: 0, keyword: '',
        },
        api: { get },
        nextTick: async () => {},
        getApiErrorMessage: () => '无法读取系统日志',
        ElMessage: { error() {} },
        document: {
            hidden: false,
            removeEventListener() {},
        },
        window: {
            setInterval(callback: () => void) { intervals.push(callback); return intervals.length },
            clearInterval() {},
        },
    }
    return { state, intervals }
}

test('实际系统日志请求以参数快照和序号防止旧来源乱序回包覆盖最新内容', async () => {
    const requests: Array<{ params: any; pending: ReturnType<typeof deferred<any>> }> = []
    const { state } = logState((_url, config) => {
        const pending = deferred<any>()
        requests.push({ params: config.params, pending })
        return pending.promise
    })
    const app = install(state)

    const worker = app.fetchSystemLogs()
    state.systemLogSource.value = 'backend'
    state.systemLogLines.value = 1000
    state.systemLogKeyword.value = 'failed'
    const backend = app.fetchSystemLogs()
    assert.equal(requests[0].params.source, 'worker')
    assert.equal(requests[0].params.lines, 300)
    assert.equal(requests[0].params.keyword, undefined)
    assert.equal(requests[1].params.source, 'backend')
    assert.equal(requests[1].params.lines, 1000)
    assert.equal(requests[1].params.keyword, 'failed')

    requests[1].pending.resolve({ data: { available: true, content: 'backend newest', line_count: 1 } })
    await backend
    requests[0].pending.resolve({ data: { available: true, content: 'worker stale', line_count: 1 } })
    await worker

    assert.equal(state.systemLogData.content, 'backend newest')
    assert.equal(state.systemLogData.source, 'backend')
    assert.equal(state.systemLogData.lines, 1000)
    assert.equal(state.systemLogData.keyword, 'failed')
    assert.equal(state.systemLogLoading.value, false)
})

test('实际最新日志请求失败会恢复 loading，而过期请求不会干扰该状态', async () => {
    const pending = deferred<any>()
    const { state } = logState(() => pending.promise)
    const app = install(state)
    const request = app.fetchSystemLogs(false)
    assert.equal(state.systemLogLoading.value, true)
    pending.reject(new Error('network'))
    await request
    assert.equal(state.systemLogLoading.value, false)
})

test('页面隐藏会暂停自动刷新并让进行中的日志回包失效', async () => {
    const pending = deferred<any>()
    const { state, intervals } = logState(() => pending.promise)
    const app = install(state)
    app.syncSystemLogAutoRefresh()
    assert.equal(intervals.length, 1)
    const request = app.fetchSystemLogs(false)
    state.document.hidden = true
    app.handleSystemLogVisibilityChange()
    pending.resolve({ data: { available: true, content: 'hidden response' } })
    await request

    assert.equal(state.systemLogData.content, '')
    assert.equal(state.systemLogLoading.value, false)
})

test('实际自动刷新会复用同参数在途请求，慢网不会堆积或丢弃最终回包', async () => {
    const pending = deferred<any>()
    let calls = 0
    const { state, intervals } = logState(() => {
        calls += 1
        return pending.promise
    })
    const app = install(state)
    app.syncSystemLogAutoRefresh()
    intervals[0]()
    intervals[0]()

    assert.equal(calls, 1)
    pending.resolve({ data: { available: true, content: 'slow but current' } })
    await new Promise<void>((resolve) => setImmediate(resolve))

    assert.equal(state.systemLogData.content, 'slow but current')
    assert.equal(state.systemLogLoading.value, false)
    intervals[0]()
    assert.equal(calls, 2)
})

test('实际卸载清理后忽略晚到日志回包，不再写入组件状态', async () => {
    const pending = deferred<any>()
    const { state } = logState(() => pending.promise)
    const app = install(state)
    const request = app.fetchSystemLogs(false)
    app.disposeSystemLogRequests()
    pending.resolve({ data: { available: true, content: 'unmounted response' } })
    await request

    assert.equal(state.systemLogData.content, '')
    assert.equal(state.systemLogLoading.value, false)
})
