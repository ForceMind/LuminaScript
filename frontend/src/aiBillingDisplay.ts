type AiLogIdentity = {
    user_id?: unknown
    user_name?: unknown
    username?: unknown
    billed_user_id?: unknown
    billed_username?: unknown
}

const nonEmptyText = (value: unknown) => typeof value === 'string' && value.trim() ? value.trim() : ''

const userLabel = (username: unknown, id: unknown) => {
    const name = nonEmptyText(username)
    if (name) return name
    return id === null || id === undefined || id === '' ? '未知用户' : `用户 #${id}`
}

export const getAiBillingDisplay = (log: AiLogIdentity) => {
    const actor = userLabel(log.user_name ?? log.username, log.user_id)
    const hasExplicitBilledUser = log.billed_user_id !== null && log.billed_user_id !== undefined
        || Boolean(nonEmptyText(log.billed_username))
    if (!hasExplicitBilledUser) {
        return {
            actor,
            billed: actor,
            legacy: true,
            billingNote: '历史：按操作者统计',
        }
    }
    return {
        actor,
        billed: userLabel(log.billed_username, log.billed_user_id),
        legacy: false,
        billingNote: '按计费对象统计',
    }
}
