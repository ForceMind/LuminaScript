export type ProjectRequestSnapshot = Readonly<{
    projectId: number | null
    contextRevision: string
    epoch: number
    sequence: number
    channel: string
}>

const normalizeRevision = (value: unknown) => typeof value === 'string' ? value : ''

/**
 * Keeps asynchronous project work tied to the project and revision that started it.
 * The Vue component owns state mutation; this class only makes stale-response
 * decisions deterministic and independently testable.
 */
export class ProjectRequestGuard {
    private epoch = 0
    private sequence = 0
    private activeLoadingSequence = 0
    private latestSequenceByChannel = new Map<string, number>()

    begin(projectId: number | null | undefined, contextRevision?: unknown, channel = 'default'): ProjectRequestSnapshot {
        this.sequence += 1
        this.latestSequenceByChannel.set(channel, this.sequence)
        return {
            projectId: typeof projectId === 'number' ? projectId : null,
            contextRevision: normalizeRevision(contextRevision),
            epoch: this.epoch,
            sequence: this.sequence,
            channel,
        }
    }

    invalidate() {
        this.epoch += 1
        this.activeLoadingSequence = 0
        this.latestSequenceByChannel.clear()
    }

    isCurrent(
        request: ProjectRequestSnapshot,
        projectId: number | null | undefined,
        contextRevision?: unknown,
    ) {
        return request.epoch === this.epoch
            && this.latestSequenceByChannel.get(request.channel) === request.sequence
            && request.projectId === (typeof projectId === 'number' ? projectId : null)
            && request.contextRevision === normalizeRevision(contextRevision)
    }

    isSameProjectEpoch(request: ProjectRequestSnapshot, projectId: number | null | undefined) {
        return request.epoch === this.epoch
            && request.projectId === (typeof projectId === 'number' ? projectId : null)
    }

    startLoading(request: ProjectRequestSnapshot) {
        this.activeLoadingSequence = request.sequence
    }

    mayFinishLoading(
        request: ProjectRequestSnapshot,
        projectId: number | null | undefined,
        _contextRevision?: unknown,
    ) {
        return this.activeLoadingSequence === request.sequence
            && request.epoch === this.epoch
            && request.projectId === (typeof projectId === 'number' ? projectId : null)
    }
}

export const applyQuickReviewCandidateAtomically = (
    currentValues: Readonly<Record<string, string>>,
    baseValues: Readonly<Record<string, string>>,
    changes: ReadonlyArray<{ field?: unknown, after?: unknown }>,
) => {
    const candidateFields = changes.map((change) => String(change?.field || '').trim())
    const fieldsAreValid = candidateFields.every(
        (field) => field && Object.prototype.hasOwnProperty.call(baseValues, field),
    )
    const hasLocalConflict = Object.entries(baseValues).some(([field, value]) => currentValues[field] !== value)
    if (!fieldsAreValid || hasLocalConflict) return null

    const nextValues = { ...currentValues }
    for (let index = 0; index < changes.length; index += 1) {
        nextValues[candidateFields[index]] = String(changes[index]?.after ?? '')
    }
    return nextValues
}

export const submitQuickReviewRequest = async ({
    request,
    isCurrent,
    values,
    editedFields,
    baselineValues = {},
    aiAdjustedFields = [],
    reviewInput,
    getLabel,
    post,
}: {
    request: ProjectRequestSnapshot
    isCurrent: () => boolean
    values: Record<string, string>
    editedFields: string[]
    baselineValues?: Record<string, string>
    aiAdjustedFields?: string[]
    reviewInput: (value: string, label: string) => Promise<string>
    getLabel: (field: string) => string
    post: (url: string, payload: Record<string, unknown>) => Promise<{ data: unknown }>
}) => {
    const reviewedValues = { ...values }
    for (const field of editedFields) {
        reviewedValues[field] = await reviewInput(reviewedValues[field], getLabel(field))
        if (!isCurrent()) return { status: 'stale' as const }
    }
    const response = await post(`/projects/${request.projectId}/setup/quick-review`, {
        action: 'confirm',
        values: reviewedValues,
        baseline_values: baselineValues,
        edited_fields: editedFields,
        ai_adjusted_fields: aiAdjustedFields,
        context_revision: request.contextRevision,
    })
    return isCurrent()
        ? { status: 'success' as const, data: response.data, values: reviewedValues }
        : { status: 'stale' as const }
}

export const analyzeProjectRequest = async ({
    request,
    isCurrent,
    post,
}: {
    request: ProjectRequestSnapshot
    isCurrent: () => boolean
    post: (url: string) => Promise<{ data: unknown }>
}) => {
    const response = await post(`/projects/${request.projectId}/analyze`)
    return isCurrent()
        ? { status: 'success' as const, data: response.data }
        : { status: 'stale' as const }
}
