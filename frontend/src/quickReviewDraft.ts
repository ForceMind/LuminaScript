export type QuickReviewDraft = {
    values: Record<string, string>
    baselineValues: Record<string, string>
    savedValues: Record<string, string>
    editedFields: string[]
    aiAdjustedFields: string[]
    savedAt: string
    stale: boolean
}

const asRecord = (value: unknown): Record<string, string> => {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return {}
    return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, String(item ?? '')]))
}

const asFields = (value: unknown) => Array.isArray(value)
    ? value.map((item) => String(item ?? '').trim()).filter(Boolean)
    : []

export const draftValuesDiffer = (
    values: Readonly<Record<string, string>>,
    savedValues: Readonly<Record<string, string>>,
) => {
    const keys = new Set([...Object.keys(values), ...Object.keys(savedValues)])
    return [...keys].some((key) => values[key] !== savedValues[key])
}

export const savedDraftResult = (
    currentValues: Readonly<Record<string, string>>,
    requestValues: Readonly<Record<string, string>>,
) => ({
    changedDuringSave: draftValuesDiffer(currentValues, requestValues),
    savedValues: { ...requestValues },
})

export const quickReviewAiEligibility = (
    values: Readonly<Record<string, string>>,
    baselineValues: Readonly<Record<string, string>>,
    editableFields: ReadonlySet<string>,
) => {
    const changedFields = Object.keys(values).filter((field) => values[field] !== baselineValues[field])
    return {
        changedFields,
        canRelated: changedFields.length > 0,
        canEditedOnly: changedFields.some((field) => editableFields.has(field)),
    }
}

export const hydrateQuickReviewDraft = (payload: any): QuickReviewDraft => {
    const rawDraft = payload?.quick_setup_draft || payload?.quickReviewDraft || payload?.draft || payload || {}
    const sectionValues = Object.fromEntries(
        (Array.isArray(payload?.sections) ? payload.sections : [])
            .map((section: any) => [String(section?.key ?? '').trim(), String(section?.value ?? '')])
            .filter(([key]: [string, string]) => Boolean(key)),
    ) as Record<string, string>
    const values = asRecord(rawDraft.values)
    const activeValues = Object.keys(values).length ? values : sectionValues
    const baselineValues = asRecord(rawDraft.baseline_values || rawDraft.baselineValues)
    const savedValues = asRecord(rawDraft.saved_values || rawDraft.savedValues)
    return {
        values: activeValues,
        baselineValues: Object.keys(baselineValues).length ? baselineValues : { ...activeValues },
        savedValues: Object.keys(savedValues).length ? savedValues : { ...activeValues },
        editedFields: asFields(rawDraft.edited_fields || rawDraft.editedFields),
        aiAdjustedFields: asFields(rawDraft.ai_adjusted_fields || rawDraft.aiAdjustedFields),
        savedAt: String(rawDraft.saved_at || rawDraft.savedAt || ''),
        stale: Boolean(rawDraft.stale || rawDraft.read_only || payload?.draft_stale || payload?.quick_setup_draft_stale),
    }
}
