import assert from 'node:assert/strict'
import test from 'node:test'
import {
    firstMissingQuickReviewLabel,
    formatApiErrorDetail,
    formatSetupFieldValue,
    normalizeTitleDisplay,
} from '../src/setupFieldPresentation.ts'

test('设定时长与计数展示保留规范值的小数和单位语义', () => {
    assert.equal(formatSetupFieldValue('movie_duration', '90.5'), '90.5 分钟')
    assert.equal(formatSetupFieldValue('episode_duration', '1.5mins'), '1.5 分钟')
    assert.equal(formatSetupFieldValue('video_duration_seconds', '120'), '120 秒')
    assert.equal(formatSetupFieldValue('scene_count_target', '12'), '12 场')
    assert.equal(formatSetupFieldValue('episode_count', '8'), '8 集')
    assert.equal(formatSetupFieldValue('episode_duration', '90minutes'), '90minutes')
})

test('快速确认允许补充说明为空或无，但仍阻止其他必填字段为空', () => {
    const sections = [
        { key: 'title', label: '故事题目' },
        { key: 'user_notes', label: '补充说明' },
    ]
    assert.equal(firstMissingQuickReviewLabel(sections, { title: '晨光', user_notes: '' }), '')
    assert.equal(firstMissingQuickReviewLabel(sections, { title: '晨光', user_notes: '无' }), '')
    assert.equal(firstMissingQuickReviewLabel(sections, { title: '', user_notes: '无' }), '故事题目')
})

test('结构化后端校验错误显示可读字段信息', () => {
    assert.equal(
        formatApiErrorDetail([{ loc: ['body', 'movie_duration'], msg: '时长必须在 30–300 分钟内' }], '提交失败'),
        'movie_duration：时长必须在 30–300 分钟内',
    )
})

test('标题展示只移除明确前缀和外围包装，保留内部标点', () => {
    assert.equal(normalizeTitleDisplay('标题： 《暮色: 第二章—归来》'), '暮色: 第二章—归来')
    assert.equal(normalizeTitleDisplay('片名：晨光—归来'), '晨光—归来')
    assert.equal(normalizeTitleDisplay('暮色: 第二章—归来'), '暮色: 第二章—归来')
    assert.equal(normalizeTitleDisplay('“标题：《A：B-C》”'), 'A：B-C')
    assert.equal(normalizeTitleDisplay("title: 'A-B:C'"), 'A-B:C')
    assert.equal(normalizeTitleDisplay('作品名称为「暮色—归来」'), '暮色—归来')
})
