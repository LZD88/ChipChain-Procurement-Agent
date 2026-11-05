import test from 'node:test'
import assert from 'node:assert/strict'
import { streamChat, resumeChat } from './chat.js'

test('输入拦截保留错误码和提示，不消费 SSE 响应', async t => {
  t.mock.method(globalThis, 'fetch', async () => new Response(JSON.stringify({
    detail: { code: 'input_blocked', message: '该输入未通过安全检查，请调整内容后重新提交。' },
  }), { status: 403 }))
  const errors = []
  await assert.rejects(streamChat('测试输入', null, { onError: error => errors.push(error) }), error => {
    assert.equal(error.status, 403)
    assert.equal(error.code, 'input_blocked')
    assert.match(error.message, /安全检查/)
    return true
  })
  assert.equal(errors.length, 1)
})

test('补充信息检查失败可识别为可重试错误，原始审批操作仍正常提交', async t => {
  const requests = []
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    requests.push(JSON.parse(options.body))
    return new Response(JSON.stringify({
      detail: { code: 'input_check_unavailable', message: '输入安全检查暂时不可用，请稍后重试。' },
    }), { status: 503 })
  })
  await assert.rejects(resumeChat('order-thread', { supplement: '物料ID=100' }), error => {
    assert.equal(error.status, 503)
    assert.equal(error.code, 'input_check_unavailable')
    return true
  })
  assert.deepEqual(requests[0], { resume: { supplement: '物料ID=100' } })
})
