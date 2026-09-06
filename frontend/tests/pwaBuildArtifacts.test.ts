import assert from 'node:assert/strict'
import test from 'node:test'
import { mkdtemp, mkdir, readFile, writeFile } from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import vm from 'node:vm'
import {
    checkServiceWorker,
    EXPLICIT_SHELL_FILES,
    extractEntryAssets,
    generateServiceWorker,
} from '../scripts/generate-pwa-sw.mjs'

const createBuildFixture = async (entry = '/assets/app.js') => {
    const root = await mkdtemp(path.join(os.tmpdir(), 'lumina-pwa-'))
    const dist = path.join(root, 'dist')
    await mkdir(path.join(dist, 'assets'), { recursive: true })
    await writeFile(path.join(root, 'package.json'), JSON.stringify({ version: '9.8.7' }))
    await writeFile(path.join(dist, 'index.html'), `<script type="module" src="${entry}"></script><link rel="stylesheet" href="/assets/app.css">`)
    for (const publicPath of EXPLICIT_SHELL_FILES) {
        if (publicPath === '/' || publicPath === '/index.html') continue
        await writeFile(path.join(dist, publicPath.slice(1)), publicPath)
    }
    await writeFile(path.join(dist, 'assets/app.css'), 'body{}')
    return { root, dist }
}

test('生成器从最终 index.html 只提取本地入口资产', () => {
    assert.deepEqual(
        extractEntryAssets('<script type="module" src="/assets/app.js"></script><link rel="stylesheet" href="/assets/app.css">'),
        ['/assets/app.css', '/assets/app.js'],
    )
    assert.throws(() => extractEntryAssets('<script src="https://cdn.example/app.js"></script>'), /must be local/)
    assert.throws(() => extractEntryAssets('<script src="/%2e%2e/secret.js"></script>'), /escapes dist/)
    assert.throws(() => extractEntryAssets('<script src="/assets/app%3Fdebug.js"></script>'), /decoded query/)
    assert.throws(() => extractEntryAssets('<script src="/assets/app%23fragment.js"></script>'), /decoded query/)
    assert.throws(() => extractEntryAssets('<script src="/app.js"></script>'), /safe \/assets/)
    assert.throws(() => extractEntryAssets('<script src="/assets/nested/app.js"></script>'), /safe \/assets/)
    assert.throws(() => extractEntryAssets('<script src="/assets/app.wasm"></script>'), /safe \/assets/)
    assert.throws(() => extractEntryAssets('<script src="/assets/app..js"></script>'), /safe \/assets/)
    assert.throws(() => extractEntryAssets('<link rel="stylesheet" href="/assets/app.css">'), /no local JavaScript/)
})

test('构建在白名单文件或入口资产缺失时失败', async () => {
    const fixture = await createBuildFixture('/assets/missing.js')
    await assert.rejects(
        generateServiceWorker({ frontendDirectory: fixture.root }),
        /ENOENT/,
    )
})

const installServiceWorkerHarness = (source: string) => {
    const listeners = new Map<string, (event: any) => void>()
    const offlineResponse = { source: 'offline' }
    const cachedResponse = { source: 'precache' }
    let rejectNetwork = false
    const networkRequests: any[] = []
    const context = vm.createContext({
        URL,
        Promise,
        Set,
        self: {
            location: { origin: 'https://lumina.test' },
            addEventListener(type: string, listener: (event: any) => void) { listeners.set(type, listener) },
            skipWaiting() {},
        },
        caches: {
            open: async () => ({ addAll: async () => {} }),
            keys: async () => [],
            delete: async () => true,
            match: async (request: any) => request === '/offline.html' ? offlineResponse : cachedResponse,
        },
        fetch: async (request: any) => {
            networkRequests.push(request)
            if (rejectNetwork) throw new TypeError('offline')
            return { source: 'network' }
        },
    })
    vm.runInContext(source, context)
    return {
        dispatchFetch({
            pathname,
            method = 'GET',
            mode = 'same-origin',
            origin = 'https://lumina.test',
            authorization = false,
            search = '',
        }: {
            pathname: string
            method?: string
            mode?: string
            origin?: string
            authorization?: boolean
            search?: string
        }) {
            let responsePromise: Promise<any> | undefined
            listeners.get('fetch')?.({
                request: {
                    method,
                    mode,
                    url: `${origin}${pathname}${search}`,
                    headers: new Headers(authorization ? { Authorization: 'Bearer secret' } : {}),
                },
                respondWith(response: Promise<any>) { responsePromise = response },
            })
            return responsePromise
        },
        rejectNetwork() { rejectNetwork = true },
        networkRequests,
        offlineResponse,
        cachedResponse,
    }
}

test('SW fetch 行为只处理两个导航路径和精确静态白名单', async () => {
    const fixture = await createBuildFixture()
    await writeFile(path.join(fixture.dist, 'assets/app.js'), 'console.log("ready")')
    const result = await generateServiceWorker({ frontendDirectory: fixture.root })
    const sw = await readFile(path.join(fixture.dist, 'sw.js'), 'utf8')
    const harness = installServiceWorkerHarness(sw)

    assert.equal(result.version, '9.8.7')
    assert.ok(result.precacheUrls.includes('/offline.html'))
    assert.ok(result.precacheUrls.includes('/assets/app.js'))

    assert.ok(harness.dispatchFetch({ pathname: '/', mode: 'navigate' }))
    assert.ok(harness.dispatchFetch({ pathname: '/index.html', mode: 'navigate' }))
    assert.equal(harness.dispatchFetch({ pathname: '/project/123', mode: 'navigate' }), undefined)
    assert.equal(harness.dispatchFetch({ pathname: '/api/projects', mode: 'navigate' }), undefined)
    assert.ok(harness.dispatchFetch({ pathname: '/offline.html' }))
    assert.ok(harness.dispatchFetch({ pathname: '/assets/app.js' }))
    assert.ok(harness.dispatchFetch({ pathname: '/favicon.svg' }))
    assert.equal(harness.dispatchFetch({ pathname: '/assets/not-built.js' }), undefined)
    assert.equal(harness.dispatchFetch({ pathname: '/api/offline.html' }), undefined)
    assert.equal(harness.dispatchFetch({ pathname: '/offline.html', method: 'POST' }), undefined)
    assert.equal(harness.dispatchFetch({ pathname: '/offline.html', origin: 'https://other.test' }), undefined)
    assert.equal(harness.dispatchFetch({ pathname: '/offline.html', authorization: true }), undefined)
    assert.equal(harness.dispatchFetch({ pathname: '/offline.html', search: '?v=1' }), undefined)
    assert.equal(harness.dispatchFetch({ pathname: '/', mode: 'navigate', search: '?from=share' }), undefined)

    harness.rejectNetwork()
    assert.equal(await harness.dispatchFetch({ pathname: '/', mode: 'navigate' }), harness.offlineResponse)
    assert.equal(await harness.dispatchFetch({ pathname: '/assets/app.js' }), harness.cachedResponse)
})

test('--check 只读验证当前版本、入口资产、语法和完整内容', async () => {
    const fixture = await createBuildFixture()
    await writeFile(path.join(fixture.dist, 'assets/app.js'), 'console.log("ready")')
    await generateServiceWorker({ frontendDirectory: fixture.root })
    assert.equal((await checkServiceWorker({ frontendDirectory: fixture.root })).version, '9.8.7')

    const swPath = path.join(fixture.dist, 'sw.js')
    const current = await readFile(swPath, 'utf8')
    await writeFile(swPath, `${current}\n// stale`)
    await assert.rejects(checkServiceWorker({ frontendDirectory: fixture.root }), /stale or does not match/)

    await writeFile(swPath, 'const broken = ;')
    await assert.rejects(checkServiceWorker({ frontendDirectory: fixture.root }), SyntaxError)
})

test('实际 PWA 源产物、版本与构建链保持一致', async () => {
    const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
    const packageJson = JSON.parse(await readFile(path.join(frontend, 'package.json'), 'utf8'))
    const manifest = JSON.parse(await readFile(path.join(frontend, 'public/manifest.webmanifest'), 'utf8'))
    const index = await readFile(path.join(frontend, 'index.html'), 'utf8')
    const offline = await readFile(path.join(frontend, 'public/offline.html'), 'utf8')

    assert.equal(packageJson.version, '0.0.5')
    assert.equal(packageJson.scripts.build, 'vue-tsc && vite build && node scripts/generate-pwa-sw.mjs')
    assert.equal(manifest.lang, 'zh-CN')
    assert.equal(manifest.display, 'standalone')
    assert.ok(manifest.icons.some((icon: any) => icon.sizes === '192x192'))
    assert.ok(manifest.icons.some((icon: any) => icon.sizes === '512x512'))
    assert.ok(manifest.icons.some((icon: any) => icon.purpose === 'maskable'))
    assert.match(index, /<html lang="zh-CN">/)
    assert.match(index, /viewport-fit=cover/)
    assert.match(index, /rel="manifest" href="\/manifest\.webmanifest"/)
    assert.match(offline, /离线能力仅提供此提示页/)
    assert.match(offline, /项目数据、账户功能与 AI 生成都需要联网/)

    for (const [file, width, height] of [
        ['pwa-192.png', 192, 192],
        ['pwa-512.png', 512, 512],
        ['apple-touch-icon.png', 180, 180],
    ] as const) {
        const png = await readFile(path.join(frontend, 'public', file))
        assert.equal(png.subarray(1, 4).toString('ascii'), 'PNG')
        assert.equal(png.readUInt32BE(16), width)
        assert.equal(png.readUInt32BE(20), height)
    }
})

test('移动端头部保留完整横排品牌和 44px 图标操作区', async () => {
    const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
    const app = await readFile(path.join(frontend, 'src/App.vue'), 'utf8')
    const style = await readFile(path.join(frontend, 'src/style.css'), 'utf8')
    assert.match(app, /app-brand flex shrink-0[^"\n]*whitespace-nowrap/)
    assert.match(app, /brand-name shrink-0 whitespace-nowrap/)
    assert.match(app, /header-summary hidden md:block min-w-0 flex-1/)
    assert.match(app, /aria-label="打开项目菜单" title="打开项目菜单"/)
    assert.match(app, /aria-label="打开项目工具" title="项目工具"/)
    assert.match(app, /aria-label="导出剧本" title="导出剧本"/)
    assert.match(app, /aria-label="开始新创意" title="开始新创意"/)
    assert.match(style, /min-width: 44px !important;\s+min-height: 44px !important;/)
    assert.match(style, /height: 100dvh/)
    assert.match(style, /env\(safe-area-inset-top\)/)
    assert.match(style, /@media \(max-width: 359px\)/)
})
