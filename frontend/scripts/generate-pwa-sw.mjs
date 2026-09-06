import { access, readFile, realpath, stat, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import vm from 'node:vm'

export const NAVIGATION_PATHS = ['/', '/index.html']
export const EXPLICIT_STATIC_FILES = [
    '/offline.html',
    '/manifest.webmanifest',
    '/favicon.svg',
    '/pwa-192.png',
    '/pwa-512.png',
    '/apple-touch-icon.png',
    '/pwa-maskable.svg',
]
export const EXPLICIT_SHELL_FILES = [...NAVIGATION_PATHS, ...EXPLICIT_STATIC_FILES]

const ENTRY_ASSET_PATTERN = /^\/assets\/[A-Za-z0-9][A-Za-z0-9._-]*\.(?:js|css)$/

const decodeLocalPath = (rawUrl) => {
    const value = rawUrl.trim()
    if (!value || value.startsWith('//') || /^[a-z][a-z\d+.-]*:/i.test(value)) {
        throw new Error(`PWA entry asset must be local: ${rawUrl}`)
    }
    if (value.includes('?') || value.includes('#')) {
        throw new Error(`PWA entry asset cannot contain query or fragment: ${rawUrl}`)
    }
    let decoded
    try {
        decoded = decodeURIComponent(value)
    } catch {
        throw new Error(`PWA entry asset has invalid encoding: ${rawUrl}`)
    }
    if (decoded.includes('?') || decoded.includes('#')) {
        throw new Error(`PWA entry asset cannot contain decoded query or fragment: ${rawUrl}`)
    }
    if (decoded.includes('\\') || decoded.split('/').includes('..')) {
        throw new Error(`PWA entry asset escapes dist: ${rawUrl}`)
    }
    const rootRelative = `/${decoded.replace(/^\.?\//, '')}`
    const normalized = path.posix.normalize(rootRelative)
    if (!normalized.startsWith('/') || normalized.includes('\0') || normalized.split('/').includes('..')) {
        throw new Error(`PWA entry asset escapes dist: ${rawUrl}`)
    }
    const fileName = path.posix.basename(normalized)
    if (!ENTRY_ASSET_PATTERN.test(normalized) || fileName.includes('..')) {
        throw new Error(`PWA entry asset must be a safe /assets/*.js or /assets/*.css file: ${rawUrl}`)
    }
    return normalized
}

export const extractEntryAssets = (html) => {
    const assets = []
    const tagPattern = /<(script|link)\b[^>]*>/gi
    const attributePattern = /\b(src|href)\s*=\s*(["'])(.*?)\2/i
    for (const match of html.matchAll(tagPattern)) {
        const tag = match[0]
        const attribute = tag.match(attributePattern)
        if (!attribute) continue
        const isScript = match[1].toLowerCase() === 'script'
        const isStyleOrModulePreload = /\brel\s*=\s*(["'])(stylesheet|modulepreload)\1/i.test(tag)
        if (!isScript && !isStyleOrModulePreload) continue
        assets.push(decodeLocalPath(attribute[3]))
    }
    if (!assets.some((asset) => asset.endsWith('.js'))) {
        throw new Error('PWA build has no local JavaScript entry asset')
    }
    return [...new Set(assets)].sort()
}

const assertFileInsideDist = async (distDirectory, publicPath) => {
    if (publicPath === '/') return
    const distRealPath = await realpath(distDirectory)
    const candidate = path.resolve(distRealPath, `.${publicPath}`)
    if (candidate !== distRealPath && !candidate.startsWith(`${distRealPath}${path.sep}`)) {
        throw new Error(`PWA precache path escapes dist: ${publicPath}`)
    }
    await access(candidate)
    const candidateRealPath = await realpath(candidate)
    if (!candidateRealPath.startsWith(`${distRealPath}${path.sep}`)) {
        throw new Error(`PWA precache symlink escapes dist: ${publicPath}`)
    }
    if (!(await stat(candidateRealPath)).isFile()) {
        throw new Error(`PWA precache path is not a file: ${publicPath}`)
    }
}

export const validatePrecacheUrls = (precacheUrls) => {
    const uniqueUrls = new Set(precacheUrls)
    if (uniqueUrls.size !== precacheUrls.length) throw new Error('PWA precache entries must be unique')
    for (const requiredPath of EXPLICIT_SHELL_FILES) {
        if (!uniqueUrls.has(requiredPath)) throw new Error(`PWA precache is missing required path: ${requiredPath}`)
    }
    for (const publicPath of precacheUrls) {
        if (EXPLICIT_SHELL_FILES.includes(publicPath)) continue
        const fileName = path.posix.basename(publicPath)
        if (!ENTRY_ASSET_PATTERN.test(publicPath) || fileName.includes('..')) {
            throw new Error(`PWA precache path is not semantically allowlisted: ${publicPath}`)
        }
    }
    if (!precacheUrls.some((publicPath) => ENTRY_ASSET_PATTERN.test(publicPath) && publicPath.endsWith('.js'))) {
        throw new Error('PWA precache has no JavaScript entry asset')
    }
}

export const renderServiceWorker = ({ version, precacheUrls }) => {
validatePrecacheUrls(precacheUrls)
return `/* Generated file. Do not edit. LuminaScript ${version}. */
const CACHE_NAME = ${JSON.stringify(`lumina-shell-v${version}`)}
const PRECACHE_URLS = ${JSON.stringify(precacheUrls, null, 2)}
const NAVIGATION_PATHS = new Set(${JSON.stringify(NAVIGATION_PATHS)})
const STATIC_PRECACHE_PATHS = new Set(${JSON.stringify(precacheUrls.filter((value) => !NAVIGATION_PATHS.includes(value)))})

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(CACHE_NAME).then((cache) => cache.addAll(PRECACHE_URLS)))
})

self.addEventListener('activate', (event) => {
  event.waitUntil(caches.keys().then((names) => Promise.all(
    names.filter((name) => name.startsWith('lumina-shell-v') && name !== CACHE_NAME).map((name) => caches.delete(name))
  )))
})

self.addEventListener('message', (event) => {
  if (event.data?.type === 'SKIP_WAITING') self.skipWaiting()
})

self.addEventListener('fetch', (event) => {
  const request = event.request
  if (request.method !== 'GET') return
  const url = new URL(request.url)
  if (url.origin !== self.location.origin) return
  if (request.headers.has('Authorization')) return
  if (url.search !== '') return

  if (request.mode === 'navigate') {
    if (!NAVIGATION_PATHS.has(url.pathname)) return
    event.respondWith(fetch(request).catch(() => caches.match('/offline.html')))
    return
  }

  if (!STATIC_PRECACHE_PATHS.has(url.pathname)) return
  event.respondWith(caches.match(request, { ignoreSearch: false }).then((cached) => cached || fetch(request)))
})
`
}

const calculateServiceWorker = async (frontendDirectory) => {
    const root = frontendDirectory || path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
    const distDirectory = path.join(root, 'dist')
    const packageJson = JSON.parse(await readFile(path.join(root, 'package.json'), 'utf8'))
    const html = await readFile(path.join(distDirectory, 'index.html'), 'utf8')
    const precacheUrls = [...new Set([...EXPLICIT_SHELL_FILES, ...extractEntryAssets(html)])].sort()
    validatePrecacheUrls(precacheUrls)
    await Promise.all(precacheUrls.map((publicPath) => assertFileInsideDist(distDirectory, publicPath)))
    const source = renderServiceWorker({ version: packageJson.version, precacheUrls })
    new vm.Script(source, { filename: 'sw.js' })
    return { root, distDirectory, version: packageJson.version, precacheUrls, source }
}

export const generateServiceWorker = async ({ frontendDirectory } = {}) => {
    const result = await calculateServiceWorker(frontendDirectory)
    await writeFile(path.join(result.distDirectory, 'sw.js'), result.source, 'utf8')
    return { version: result.version, precacheUrls: result.precacheUrls }
}

export const checkServiceWorker = async ({ frontendDirectory } = {}) => {
    const result = await calculateServiceWorker(frontendDirectory)
    const swPath = path.join(result.distDirectory, 'sw.js')
    const existingSource = await readFile(swPath, 'utf8')
    new vm.Script(existingSource, { filename: swPath })
    if (existingSource !== result.source) {
        throw new Error('dist/sw.js is stale or does not match the current version and entry assets')
    }
    return { version: result.version, precacheUrls: result.precacheUrls }
}

const isDirectRun = process.argv[1] && pathToFileURL(path.resolve(process.argv[1])).href === import.meta.url
if (isDirectRun) {
    const checkOnly = process.argv.slice(2).includes('--check')
    const unknownArguments = process.argv.slice(2).filter((argument) => argument !== '--check')
    if (unknownArguments.length > 0) throw new Error(`Unknown argument: ${unknownArguments[0]}`)
    const result = checkOnly ? await checkServiceWorker() : await generateServiceWorker()
    console.log(`${checkOnly ? 'Verified' : 'Generated'} dist/sw.js with ${result.precacheUrls.length} controlled precache entries.`)
}
