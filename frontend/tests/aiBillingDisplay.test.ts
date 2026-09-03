import assert from 'node:assert/strict'
import test from 'node:test'
import { getAiBillingDisplay } from '../src/aiBillingDisplay.ts'

test('新项目 AI 日志同时显示操作者和显式计费对象', () => {
    assert.deepEqual(getAiBillingDisplay({
        user_id: 8, user_name: '协作者', billed_user_id: 1, billed_username: '项目所有者',
    }), {
        actor: '协作者', billed: '项目所有者', legacy: false, billingNote: '按计费对象统计',
    })
})

test('个人调用按操作者计费时仍保留两个明确字段', () => {
    const display = getAiBillingDisplay({ user_id: 8, user_name: '协作者', billed_user_id: 8, billed_username: '协作者' })
    assert.equal(display.actor, '协作者')
    assert.equal(display.billed, '协作者')
    assert.equal(display.legacy, false)
})

test('旧日志 billed 为空使用历史操作者归属，缺少 billed 用户名安全回退到 ID', () => {
    assert.deepEqual(getAiBillingDisplay({ user_id: 8, user_name: '旧用户', billed_user_id: null, billed_username: null }), {
        actor: '旧用户', billed: '旧用户', legacy: true, billingNote: '历史：按操作者统计',
    })
    assert.equal(getAiBillingDisplay({ user_id: 8, billed_user_id: 1, billed_username: null }).billed, '用户 #1')
})
