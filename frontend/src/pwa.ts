export type BackendReachability = 'unknown' | 'reachable' | 'unreachable'
export type PwaUpdateState = 'idle' | 'waiting' | 'activating-approved' | 'refresh-required'

export interface PwaSnapshot {
    browserOnline: boolean
    backendReachability: BackendReachability
    installAvailable: boolean
    installed: boolean
    iosInstallHint: boolean
    updateAvailable: boolean
    updateState: PwaUpdateState
    updateRevision: number
}

interface BeforeInstallPromptEventLike extends Event {
    prompt: () => Promise<void>
    userChoice: Promise<{ outcome: 'accepted' | 'dismissed' | string }>
}

interface WorkerLike {
    state?: string
    postMessage: (message: unknown) => void
    addEventListener?: (type: string, listener: EventListener) => void
}

interface RegistrationLike {
    waiting?: WorkerLike | null
    installing?: WorkerLike | null
    addEventListener: (type: string, listener: EventListener) => void
}

interface ServiceWorkerContainerLike {
    controller?: WorkerLike | null
    register: (url: string, options: { updateViaCache: 'none' }) => Promise<RegistrationLike>
    addEventListener: (type: string, listener: EventListener) => void
    removeEventListener: (type: string, listener: EventListener) => void
}

export interface PwaEnvironment {
    window: Pick<Window, 'addEventListener' | 'removeEventListener' | 'matchMedia'>
    navigator: Pick<Navigator, 'onLine' | 'userAgent'> & { serviceWorker?: ServiceWorkerContainerLike }
    location: Pick<Location, 'reload'>
    fetch: typeof fetch
}

type PwaListener = (snapshot: Readonly<PwaSnapshot>) => void
type BeforeReloadCheck = () => boolean

const copySnapshot = (snapshot: PwaSnapshot): PwaSnapshot => ({ ...snapshot })

const detectStandalone = (environment: PwaEnvironment) =>
    environment.window.matchMedia('(display-mode: standalone)').matches ||
    Boolean((environment.navigator as Navigator & { standalone?: boolean }).standalone)

const detectIosSafari = (userAgent: string) => {
    const ios = /iPad|iPhone|iPod/.test(userAgent) ||
        (/Macintosh/.test(userAgent) && /Mobile/.test(userAgent))
    const webkit = /WebKit/.test(userAgent)
    const alternateBrowser = /CriOS|FxiOS|EdgiOS|OPiOS|DuckDuckGo/i.test(userAgent)
    return ios && webkit && !alternateBrowser
}

export class PwaReloadGuard {
    private approvedSnapshot: string | null = null
    private bypassBeforeUnloadOnce = false
    private readonly createSnapshot: () => string
    private readonly scheduleReset: (callback: () => void) => void

    constructor(
        createSnapshot: () => string,
        scheduleReset: (callback: () => void) => void,
    ) {
        this.createSnapshot = createSnapshot
        this.scheduleReset = scheduleReset
    }

    approveCurrentSnapshot() {
        this.approvedSnapshot = this.createSnapshot()
    }

    cancelApproval() {
        this.approvedSnapshot = null
        this.bypassBeforeUnloadOnce = false
    }

    checkBeforeReload() {
        const approvedSnapshot = this.approvedSnapshot
        this.approvedSnapshot = null
        if (approvedSnapshot === null || approvedSnapshot !== this.createSnapshot()) return false
        this.bypassBeforeUnloadOnce = true
        this.scheduleReset(() => {
            this.bypassBeforeUnloadOnce = false
        })
        return true
    }

    consumeBeforeUnloadBypass() {
        if (!this.bypassBeforeUnloadOnce) return false
        this.bypassBeforeUnloadOnce = false
        return true
    }
}

export class PwaManager {
    private readonly environment: PwaEnvironment
    private readonly listeners = new Set<PwaListener>()
    private readonly observedWorkers = new WeakSet<object>()
    private registration: RegistrationLike | null = null
    private installPrompt: BeforeInstallPromptEventLike | null = null
    private waitingWorker: WorkerLike | null = null
    private approvedWorker: WorkerLike | null = null
    private started = false
    private hasController: boolean
    private backendProbeSequence = 0
    private beforeReloadCheck: BeforeReloadCheck | null = null
    private snapshotValue: PwaSnapshot

    constructor(environment: PwaEnvironment) {
        this.environment = environment
        const installed = detectStandalone(environment)
        this.hasController = Boolean(environment.navigator.serviceWorker?.controller)
        this.snapshotValue = {
            browserOnline: environment.navigator.onLine,
            backendReachability: 'unknown',
            installAvailable: false,
            installed,
            iosInstallHint: detectIosSafari(environment.navigator.userAgent) && !installed,
            updateAvailable: false,
            updateState: 'idle',
            updateRevision: 0,
        }
    }

    get snapshot(): Readonly<PwaSnapshot> {
        return copySnapshot(this.snapshotValue)
    }

    subscribe(listener: PwaListener) {
        this.listeners.add(listener)
        listener(this.snapshot)
        return () => this.listeners.delete(listener)
    }

    private publish(patch: Partial<PwaSnapshot>) {
        this.snapshotValue = { ...this.snapshotValue, ...patch }
        const snapshot = this.snapshot
        this.listeners.forEach((listener) => listener(snapshot))
    }

    private readonly handleOnline = () => {
        this.publish({ browserOnline: true })
        void this.probeBackend()
    }
    private readonly handleOffline = () => {
        this.backendProbeSequence += 1
        this.publish({ browserOnline: false, backendReachability: 'unknown' })
    }
    private readonly handleBeforeInstallPrompt = (event: Event) => {
        event.preventDefault()
        this.installPrompt = event as BeforeInstallPromptEventLike
        this.publish({ installAvailable: true })
    }
    private readonly handleAppInstalled = () => {
        this.installPrompt = null
        this.publish({ installAvailable: false, installed: true, iosInstallHint: false })
    }
    private readonly handleControllerChange = () => {
        const controller = this.environment.navigator.serviceWorker?.controller || null
        const wasControlled = this.hasController
        this.hasController = Boolean(controller)
        const approvedWorker = this.approvedWorker
        this.waitingWorker = null
        this.approvedWorker = null

        if (!wasControlled && controller) {
            this.setUpdateState('idle', true)
            return
        }
        if (approvedWorker && controller === approvedWorker) {
            if (!this.canReload()) {
                this.setUpdateState('refresh-required', true)
                return
            }
            this.setUpdateState('idle', true)
            this.environment.location.reload()
            return
        }
        this.setUpdateState('refresh-required', true)
    }

    private watchInstallingWorker(worker: WorkerLike | null | undefined) {
        if (!worker?.addEventListener) return
        if (this.observedWorkers.has(worker)) return
        this.observedWorkers.add(worker)
        worker.addEventListener('statechange', (() => {
            if (worker.state === 'installed' && this.environment.navigator.serviceWorker?.controller) {
                this.captureWaitingWorker(this.registration?.waiting || worker)
                return
            }
            if (worker.state !== 'redundant') return
            let invalidatedTarget = false
            if (this.waitingWorker === worker) {
                this.waitingWorker = null
                invalidatedTarget = true
            }
            if (this.approvedWorker === worker) {
                this.approvedWorker = null
                invalidatedTarget = true
            }
            if (invalidatedTarget && !this.waitingWorker && !this.approvedWorker) this.setUpdateState('idle', true)
        }) as EventListener)
    }

    private captureWaitingWorker(worker: WorkerLike | null | undefined) {
        if (!worker) return
        if (this.waitingWorker === worker && this.snapshotValue.updateState === 'waiting') return
        this.waitingWorker = worker
        this.approvedWorker = null
        this.watchInstallingWorker(worker)
        this.setUpdateState('waiting', true)
    }

    private setUpdateState(updateState: PwaUpdateState, advanceRevision = false) {
        this.publish({
            updateState,
            updateAvailable: updateState === 'waiting',
            updateRevision: this.snapshotValue.updateRevision + (advanceRevision ? 1 : 0),
        })
    }

    async start({ registerServiceWorker = true } = {}) {
        if (this.started) return
        this.started = true
        this.environment.window.addEventListener('online', this.handleOnline)
        this.environment.window.addEventListener('offline', this.handleOffline)
        this.environment.window.addEventListener('beforeinstallprompt', this.handleBeforeInstallPrompt)
        this.environment.window.addEventListener('appinstalled', this.handleAppInstalled)
        void this.probeBackend()

        const serviceWorker = this.environment.navigator.serviceWorker
        if (!registerServiceWorker || !serviceWorker) return
        serviceWorker.addEventListener('controllerchange', this.handleControllerChange)
        try {
            this.registration = await serviceWorker.register('/sw.js', { updateViaCache: 'none' })
            this.captureWaitingWorker(this.registration.waiting)
            this.watchInstallingWorker(this.registration.installing)
            this.registration.addEventListener('updatefound', (() => {
                this.watchInstallingWorker(this.registration?.installing)
            }) as EventListener)
        } catch (error) {
            console.error('PWA service worker registration failed', error)
        }
    }

    stop() {
        if (!this.started) return
        this.started = false
        this.environment.window.removeEventListener('online', this.handleOnline)
        this.environment.window.removeEventListener('offline', this.handleOffline)
        this.environment.window.removeEventListener('beforeinstallprompt', this.handleBeforeInstallPrompt)
        this.environment.window.removeEventListener('appinstalled', this.handleAppInstalled)
        this.environment.navigator.serviceWorker?.removeEventListener('controllerchange', this.handleControllerChange)
        this.waitingWorker = null
        this.approvedWorker = null
        this.setUpdateState('idle', true)
    }

    reportBackendReachability(reachability: BackendReachability) {
        this.publish({ backendReachability: reachability })
    }

    setBeforeReloadCheck(check: BeforeReloadCheck | null) {
        this.beforeReloadCheck = check
    }

    private canReload() {
        if (!this.beforeReloadCheck) return true
        try {
            return this.beforeReloadCheck()
        } catch (error) {
            console.error('PWA reload check failed', error)
            return false
        }
    }

    async probeBackend() {
        if (!this.environment.navigator.onLine) {
            this.handleOffline()
            return false
        }
        const sequence = ++this.backendProbeSequence
        try {
            const response = await this.environment.fetch('/api/', {
                method: 'GET',
                credentials: 'omit',
                cache: 'no-store',
                headers: { Accept: 'application/json' },
            })
            if (sequence !== this.backendProbeSequence || !this.environment.navigator.onLine) return false
            const reachable = response.status !== 502 && response.status !== 504
            this.publish({ backendReachability: reachable ? 'reachable' : 'unreachable' })
            return reachable
        } catch {
            if (sequence !== this.backendProbeSequence || !this.environment.navigator.onLine) return false
            this.publish({ backendReachability: 'unreachable' })
            return false
        }
    }

    async promptInstall() {
        if (!this.installPrompt) return false
        const prompt = this.installPrompt
        await prompt.prompt()
        const choice = await prompt.userChoice
        this.installPrompt = null
        this.publish({ installAvailable: false })
        return choice.outcome === 'accepted'
    }

    activateWaitingWorker(expectedRevision = this.snapshotValue.updateRevision) {
        const target = this.waitingWorker
        if (!target || this.snapshotValue.updateState !== 'waiting' || expectedRevision !== this.snapshotValue.updateRevision) {
            return false
        }
        this.waitingWorker = null
        this.approvedWorker = target
        this.setUpdateState('activating-approved')
        try {
            target.postMessage({ type: 'SKIP_WAITING' })
            return true
        } catch {
            if (this.approvedWorker === target) this.approvedWorker = null
            this.captureWaitingWorker(target)
            return false
        }
    }

    refreshAfterExternalUpdate(expectedRevision = this.snapshotValue.updateRevision) {
        if (this.snapshotValue.updateState !== 'refresh-required' || expectedRevision !== this.snapshotValue.updateRevision) {
            return false
        }
        if (!this.canReload()) return false
        this.waitingWorker = null
        this.approvedWorker = null
        this.setUpdateState('idle', true)
        this.environment.location.reload()
        return true
    }
}

let sharedManager: PwaManager | null = null

const browserEnvironment = (): PwaEnvironment => ({
    window,
    navigator: navigator as PwaEnvironment['navigator'],
    location,
    fetch: window.fetch.bind(window),
})

export const getPwaManager = () => {
    if (!sharedManager) sharedManager = new PwaManager(browserEnvironment())
    return sharedManager
}

export const startPwa = (options?: { registerServiceWorker?: boolean }) => getPwaManager().start(options)
