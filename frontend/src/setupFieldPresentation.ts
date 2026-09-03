const normalizeKey = (value: unknown) => String(value ?? '')
    .trim()
    .replace(/([a-z0-9])([A-Z])/g, '$1_$2')
    .replace(/[\s-]+/g, '_')
    .toLowerCase()
    .replace(/[^a-z0-9_]/g, '')
    .replace(/_+/g, '_')
    .replace(/^_+|_+$/g, '')

const decimal = /^(\d+(?:\.\d+)?)$/
const minutes = /^(\d+(?:\.\d+)?)mins$/i
const positiveInteger = /^[1-9]\d*$/

export const formatSetupFieldValue = (rawKey: unknown, rawValue: unknown) => {
    const key = normalizeKey(rawKey)
    const value = String(rawValue ?? '').trim()
    if (!value) return ''

    if (key === 'movie_duration' && decimal.test(value)) return `${value} 分钟`
    if (key === 'episode_duration') {
        const match = value.match(minutes)
        if (match) return `${match[1]} 分钟`
    }
    if (key === 'video_duration_seconds' && positiveInteger.test(value)) return `${value} 秒`
    if (key === 'scene_count_target' && positiveInteger.test(value)) return `${value} 场`
    if (key === 'episode_count' && positiveInteger.test(value)) return `${value} 集`
    return value
}

const titlePrefix = /^(?:(?:故事|剧本|作品|电影)?(?:标题|题目|片名|名称)|title)\s*(?:[:：]|为|是)\s*/i
const titleWrappers: ReadonlyArray<readonly [string, string]> = [
    ['《', '》'], ['〈', '〉'], ['「', '」'], ['『', '』'],
    ['“', '”'], ['‘', '’'], ['"', '"'], ["'", "'"],
]

/** Only remove labels and a matched outer wrapper; title punctuation is content. */
export const normalizeTitleDisplay = (rawValue: unknown) => {
    let value = String(rawValue ?? '').trim()
    while (true) {
        const previous = value
        value = value.replace(titlePrefix, '')
        for (const [opening, closing] of titleWrappers) {
            if (value.startsWith(opening) && value.endsWith(closing) && value.length >= opening.length + closing.length) {
                value = value.slice(opening.length, -closing.length).trim()
                break
            }
        }
        if (value === previous) break
    }
    return value
}

export const firstMissingQuickReviewLabel = (
    sections: ReadonlyArray<{ key?: unknown, label?: unknown }>,
    values: Readonly<Record<string, unknown>>,
) => {
    for (const section of sections) {
        const key = String(section?.key ?? '').trim()
        if (!key) return String(section?.label ?? '') || key
        if (key === 'user_notes') continue
        if (!String(values[key] ?? '').trim()) return String(section?.label ?? '') || key
    }
    return ''
}

export const formatApiErrorDetail = (detail: unknown, fallback: string) => {
    if (typeof detail === 'string' && detail.trim()) return detail.trim()
    if (Array.isArray(detail)) {
        const messages = detail.map((item) => {
            if (typeof item === 'string') return item
            if (item && typeof item === 'object') {
                const value = item as { msg?: unknown, message?: unknown, loc?: unknown }
                const text = String(value.msg ?? value.message ?? '').trim()
                const location = Array.isArray(value.loc) ? value.loc.slice(-1).join('') : ''
                return text ? `${location ? `${location}：` : ''}${text}` : ''
            }
            return ''
        }).filter(Boolean)
        if (messages.length) return messages.join('；')
    }
    if (detail && typeof detail === 'object') {
        const value = detail as { message?: unknown, detail?: unknown }
        const text = String(value.message ?? value.detail ?? '').trim()
        if (text && text !== '[object Object]') return text
    }
    return fallback
}
