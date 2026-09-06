import assert from 'node:assert/strict'
import test from 'node:test'
import { PwaManager, PwaReloadGuard, type PwaEnvironment } from '../src/pwa.ts'

class MockEvents {
    private listeners = new Map<string, Set<EventListener>>()

    addEventListener(type: string, listener: EventListener) {
        const entries = this.listeners.get(type) || new Set<EventListener>()
        entries.add(listener)
        this.listeners.set(type, entries)
    }

    removeEventListener(type: string, listener: EventListener) {
        this.listeners.get(type)?.delete(listener)
    }

    dispatch(type: string, event = new Event(type)) {
        this.listeners.get(type)?.forEach((listener) => listener(event))
    }
}

const pwaFixture = ({ waiting = false, userAgent = 'Mozilla/5.0 Chrome', sharedWorker }: {
    waiting?: boolean
    userAgent?: string
    sharedWorker?: { state: string; postMessage: (message: unknown) => void; addEventListener?: (type: string, listener: EventListener) => void }
} = {}) => {
    const windowEvents = new MockEvents()
    const serviceWorkerEvents = new MockEvents()
    const registrationEvents = new MockEvents()
    const workerEvents = new MockEvents()
    const messages: unknown[] = []
    const localWaitingWorker = {
        state: 'installed',
        postMessage: (message: unknown) => messages.push(message),
        addEventListener: workerEvents.addEventListener.bind(workerEvents),
    }
    const waitingWorker = sharedWorker || localWaitingWorker
    const registration = {
        waiting: waiting ? waitingWorker : null,
        installing: null,
        addEventListener: registrationEvents.addEventListener.bind(registrationEvents),
    }
    const registrations: Array<{ url: string; options: unknown }> = []
    const serviceWorker = {
        controller: { postMessage() {} } as any,
        register: async (url: string, options: unknown) => {
            registrations.push({ url, options })
            return registration
        },
        addEventListener: serviceWorkerEvents.addEventListener.bind(serviceWorkerEvents),
        removeEventListener: serviceWorkerEvents.removeEventListener.bind(serviceWorkerEvents),
    }
    let online = true
    let reloads = 0
    const environment = {
        window: {
            addEventListener: windowEvents.addEventListener.bind(windowEvents),
            removeEventListener: windowEvents.removeEventListener.bind(windowEvents),
            matchMedia: () => ({ matches: false }),
        },
        navigator: {
            get onLine() { return online },
            userAgent,
            serviceWorker,
        },
        location: { reload: () => { reloads += 1 } },
        fetch: async () => new Response('{}', { status: 200 }),
    } as unknown as PwaEnvironment
    return {
        environment,
        windowEvents,
        serviceWorkerEvents,
        waitingWorker,
        serviceWorker,
        messages,
        registrations,
        setWaitingWorkerState(state: string) {
            waitingWorker.state = state
            workerEvents.dispatch('statechange')
        },
        setOnline(value: boolean) { online = value },
        reloadCount: () => reloads,
    }
}

test('注册禁用 HTTP 缓存，waiting worker 不会自动激活或刷新', async () => {
    const fixture = pwaFixture({ waiting: true })
    const manager = new PwaManager(fixture.environment)
    await manager.start()

    assert.deepEqual(fixture.registrations, [{ url: '/sw.js', options: { updateViaCache: 'none' } }])
    assert.equal(manager.snapshot.updateAvailable, true)
    assert.deepEqual(fixture.messages, [])
    fixture.serviceWorkerEvents.dispatch('controllerchange')
    assert.equal(fixture.reloadCount(), 0)
    assert.equal(manager.snapshot.updateState, 'refresh-required')

    assert.equal(manager.activateWaitingWorker(), false)
})

test('明确许可绑定当前 waiting worker，只消费一次', async () => {
    const fixture = pwaFixture({ waiting: true })
    const manager = new PwaManager(fixture.environment)
    await manager.start()
    const revision = manager.snapshot.updateRevision

    assert.equal(manager.activateWaitingWorker(revision + 1), false)
    assert.deepEqual(fixture.messages, [])
    assert.equal(manager.activateWaitingWorker(revision), true)
    assert.deepEqual(fixture.messages, [{ type: 'SKIP_WAITING' }])
    assert.equal(manager.snapshot.updateState, 'activating-approved')
    assert.equal(manager.activateWaitingWorker(revision), false)

    fixture.serviceWorker.controller = fixture.waitingWorker
    fixture.serviceWorkerEvents.dispatch('controllerchange')
    assert.equal(fixture.reloadCount(), 1)
})

test('多标签只刷新明确同意的页，其他页进入待刷新状态', async () => {
    const sharedMessages: unknown[] = []
    const sharedWorker = { state: 'installed', postMessage: (message: unknown) => sharedMessages.push(message) }
    const approvingTab = pwaFixture({ waiting: true, sharedWorker })
    const otherTab = pwaFixture({ waiting: true, sharedWorker })
    const approvingManager = new PwaManager(approvingTab.environment)
    const otherManager = new PwaManager(otherTab.environment)
    await Promise.all([approvingManager.start(), otherManager.start()])

    assert.equal(approvingManager.activateWaitingWorker(approvingManager.snapshot.updateRevision), true)
    approvingTab.serviceWorker.controller = sharedWorker
    otherTab.serviceWorker.controller = sharedWorker
    approvingTab.serviceWorkerEvents.dispatch('controllerchange')
    otherTab.serviceWorkerEvents.dispatch('controllerchange')

    assert.deepEqual(sharedMessages, [{ type: 'SKIP_WAITING' }])
    assert.equal(approvingTab.reloadCount(), 1)
    assert.equal(otherTab.reloadCount(), 0)
    assert.equal(otherManager.snapshot.updateState, 'refresh-required')
    assert.equal(otherManager.activateWaitingWorker(), false)

    const refreshRevision = otherManager.snapshot.updateRevision
    assert.equal(otherManager.refreshAfterExternalUpdate(refreshRevision + 1), false)
    assert.equal(otherTab.reloadCount(), 0)
    assert.equal(otherManager.refreshAfterExternalUpdate(refreshRevision), true)
    assert.equal(otherTab.reloadCount(), 1)
})

test('明确放弃未提交内容后刷新只豁免一次 beforeunload', async () => {
    let workSnapshot = '未提交输入 A'
    const resetCallbacks: Array<() => void> = []
    const reloadGuard = new PwaReloadGuard(
        () => workSnapshot,
        (callback) => resetCallbacks.push(callback),
    )
    const fixture = pwaFixture({ waiting: true })
    const manager = new PwaManager(fixture.environment)
    manager.setBeforeReloadCheck(() => reloadGuard.checkBeforeReload())
    await manager.start()
    fixture.serviceWorkerEvents.dispatch('controllerchange')
    assert.equal(manager.snapshot.updateState, 'refresh-required')

    reloadGuard.approveCurrentSnapshot()
    assert.equal(manager.refreshAfterExternalUpdate(manager.snapshot.updateRevision), true)
    assert.equal(fixture.reloadCount(), 1)
    let nativePromptRequests = 0
    const simulateDirtyBeforeUnload = () => {
        if (reloadGuard.consumeBeforeUnloadBypass()) return
        nativePromptRequests += 1
    }
    simulateDirtyBeforeUnload()
    assert.equal(nativePromptRequests, 0)
    assert.equal(reloadGuard.consumeBeforeUnloadBypass(), false)
    resetCallbacks.forEach((callback) => callback())
    assert.equal(workSnapshot, '未提交输入 A')
})

test('已批准 waiting worker 激活前出现新编辑则不刷新，并转为待刷新', async () => {
    let workSnapshot = '快照 A'
    const reloadGuard = new PwaReloadGuard(() => workSnapshot, () => {})
    const fixture = pwaFixture({ waiting: true })
    const manager = new PwaManager(fixture.environment)
    manager.setBeforeReloadCheck(() => reloadGuard.checkBeforeReload())
    await manager.start()

    reloadGuard.approveCurrentSnapshot()
    assert.equal(manager.activateWaitingWorker(manager.snapshot.updateRevision), true)
    workSnapshot = '快照 B：等待期间的新编辑'
    fixture.serviceWorker.controller = fixture.waitingWorker
    fixture.serviceWorkerEvents.dispatch('controllerchange')

    assert.equal(fixture.reloadCount(), 0)
    assert.equal(manager.snapshot.updateState, 'refresh-required')
    assert.equal(reloadGuard.consumeBeforeUnloadBypass(), false)
})

test('许可后控制器换成其他 worker 时不刷新，且不保留旧许可', async () => {
    const fixture = pwaFixture({ waiting: true })
    const manager = new PwaManager(fixture.environment)
    await manager.start()
    assert.equal(manager.activateWaitingWorker(manager.snapshot.updateRevision), true)

    fixture.serviceWorker.controller = { postMessage() {} }
    fixture.serviceWorkerEvents.dispatch('controllerchange')
    assert.equal(fixture.reloadCount(), 0)
    assert.equal(manager.snapshot.updateState, 'refresh-required')

    fixture.serviceWorker.controller = fixture.waitingWorker
    fixture.serviceWorkerEvents.dispatch('controllerchange')
    assert.equal(fixture.reloadCount(), 0)
})

test('waiting worker 失效会清除等待或已批准状态', async () => {
    const waitingFixture = pwaFixture({ waiting: true })
    const waitingManager = new PwaManager(waitingFixture.environment)
    await waitingManager.start()
    waitingFixture.setWaitingWorkerState('redundant')
    assert.equal(waitingManager.snapshot.updateState, 'idle')
    assert.equal(waitingManager.activateWaitingWorker(), false)

    const approvedFixture = pwaFixture({ waiting: true })
    const approvedManager = new PwaManager(approvedFixture.environment)
    await approvedManager.start()
    assert.equal(approvedManager.activateWaitingWorker(approvedManager.snapshot.updateRevision), true)
    approvedFixture.setWaitingWorkerState('redundant')
    assert.equal(approvedManager.snapshot.updateState, 'idle')
    approvedFixture.serviceWorker.controller = approvedFixture.waitingWorker
    approvedFixture.serviceWorkerEvents.dispatch('controllerchange')
    assert.equal(approvedFixture.reloadCount(), 0)
})

test('开发环境可启动网络状态管理而不注册生产 SW', async () => {
    const fixture = pwaFixture()
    const manager = new PwaManager(fixture.environment)
    await manager.start({ registerServiceWorker: false })
    assert.deepEqual(fixture.registrations, [])
    fixture.setOnline(false)
    fixture.windowEvents.dispatch('offline')
    assert.equal(manager.snapshot.browserOnline, false)
})

test('安装提示只由用户动作触发，appinstalled 后清除入口', async () => {
    const fixture = pwaFixture()
    const manager = new PwaManager(fixture.environment)
    await manager.start()
    let promptCalls = 0
    const event = Object.assign(new Event('beforeinstallprompt', { cancelable: true }), {
        prompt: async () => { promptCalls += 1 },
        userChoice: Promise.resolve({ outcome: 'accepted' }),
    })
    fixture.windowEvents.dispatch('beforeinstallprompt', event)

    assert.equal(event.defaultPrevented, true)
    assert.equal(manager.snapshot.installAvailable, true)
    assert.equal(promptCalls, 0)
    assert.equal(await manager.promptInstall(), true)
    assert.equal(promptCalls, 1)
    assert.equal(manager.snapshot.installAvailable, false)

    fixture.windowEvents.dispatch('appinstalled')
    assert.equal(manager.snapshot.installed, true)
})

test('浏览器离线与后端不可达是独立状态', async () => {
    const fixture = pwaFixture()
    const manager = new PwaManager(fixture.environment)
    await manager.start()
    manager.reportBackendReachability('unreachable')
    assert.equal(manager.snapshot.browserOnline, true)
    assert.equal(manager.snapshot.backendReachability, 'unreachable')

    fixture.setOnline(false)
    fixture.windowEvents.dispatch('offline')
    assert.equal(manager.snapshot.browserOnline, false)
    assert.equal(manager.snapshot.backendReachability, 'unknown')

    fixture.setOnline(true)
    fixture.windowEvents.dispatch('online')
    assert.equal(manager.snapshot.browserOnline, true)
    await new Promise((resolve) => setTimeout(resolve, 0))
    assert.equal(manager.snapshot.backendReachability, 'reachable')
})

test('后端探测将代理 502/504 与网络失败标记为不可达', async () => {
    const fixture = pwaFixture()
    fixture.environment.fetch = async () => new Response('{}', { status: 502 })
    const manager = new PwaManager(fixture.environment)
    assert.equal(await manager.probeBackend(), false)
    assert.equal(manager.snapshot.backendReachability, 'unreachable')

    fixture.environment.fetch = async () => { throw new TypeError('network failed') }
    assert.equal(await manager.probeBackend(), false)
    assert.equal(manager.snapshot.backendReachability, 'unreachable')
})

test('iOS Safari 未以 standalone 运行时显示添加主屏幕说明', () => {
    const fixture = pwaFixture({
        userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148 Version/18.0 Safari/604.1',
    })
    const manager = new PwaManager(fixture.environment)
    assert.equal(manager.snapshot.iosInstallHint, true)
})
