import assert from 'node:assert/strict'
import test from 'node:test'
import { draftValuesDiffer, hydrateQuickReviewDraft, quickReviewAiEligibility, savedDraftResult } from '../src/quickReviewDraft.ts'

test('恢复草案保留生成基线、已保存值与手动/AI字段来源', () => {
    const draft = hydrateQuickReviewDraft({
        sections: [{ key: 'title', value: '新生成' }],
        quick_setup_draft: {
            values: { title: '手改后' },
            baseline_values: { title: '新生成' },
            saved_values: { title: '已保存' },
            edited_fields: ['title'],
            ai_adjusted_fields: ['theme'],
            saved_at: '2026-09-03T12:00:00Z',
        },
    })
    assert.deepEqual(draft.values, { title: '手改后' })
    assert.deepEqual(draft.baselineValues, { title: '新生成' })
    assert.deepEqual(draft.savedValues, { title: '已保存' })
    assert.deepEqual(draft.editedFields, ['title'])
    assert.deepEqual(draft.aiAdjustedFields, ['theme'])
})

test('草案脏检查和过期标识不会把保存快照当作未保存修改', () => {
    assert.equal(draftValuesDiffer({ title: '相同' }, { title: '相同' }), false)
    assert.equal(draftValuesDiffer({ title: '手改' }, { title: '已保存' }), true)
    assert.equal(hydrateQuickReviewDraft({ sections: [], draft_stale: true }).stale, true)
})

test('延迟保存完成时保留保存期间的新编辑，并仅更新已保存基准', () => {
    const result = savedDraftResult({ title: '保存后继续编辑' }, { title: '请求时内容' })
    assert.equal(result.changedDuringSave, true)
    assert.deepEqual(result.savedValues, { title: '请求时内容' })
})

test('仅 AI 调整的时长/规模变更可以联动整改，但不能触发仅整改可编辑内容项', () => {
    const result = quickReviewAiEligibility(
        { episode_count: '8', title: '原题' },
        { episode_count: '6', title: '原题' },
        new Set(['title', 'theme']),
    )
    assert.equal(result.canRelated, true)
    assert.equal(result.canEditedOnly, false)
})
