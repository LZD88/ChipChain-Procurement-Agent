import test from 'node:test'
import assert from 'node:assert/strict'
import { streamChat, resumeChat } from './chat.js'
import { createToolCallbacks } from '../utils/toolCalls.js'

function streamResponse(events) {
  const bytes = new TextEncoder().encode(events.map(event => `data: ${JSON.stringify(event)}\n\n`).join(''))
  return new Response(new ReadableStream({
    start(controller) {
      for (let offset = 0; offset < bytes.length; offset += 7) controller.enqueue(bytes.slice(offset, offset + 7))
      controller.close()
    }
  }))
}

const start = id => ({ type: 'tool_start', tool_call_id: id, tool_name: 'ls', source: 'main' })
const args = (id, value) => ({ type: 'tool_args', tool_call_id: id, args: value, source: 'main' })
const result = (id, text, status = 'done') => ({ type: 'tool_result', tool_call_id: id, tool_name: 'ls', text, tool_status: status })
const end = id => ({ type: 'tool_end', tool_call_id: id })
const done = { type: 'done', thread_id: 'test-thread', content: '完成' }

test('服务端创建会话后立即回传 ID，不必等待模型完成', async t => {
  t.mock.method(globalThis, 'fetch', async () => streamResponse([
    { type: 'session', thread_id: 'project-thread' },
    { type: 'progress', stage: 'reasoning', message: '正在核验库存与供应商…' },
    { type: 'token', content: '处理中' },
    { type: 'done', thread_id: 'project-thread', content: '处理中' }
  ]))
  const received = []
  const progress = []
  const result = await streamChat('采购', null, {
    onSession: data => received.push(data.thread_id),
    onProgress: data => progress.push(data.message)
  })
  assert.deepEqual(received, ['project-thread'])
  assert.deepEqual(progress, ['正在核验库存与供应商…'])
  assert.equal(result.thread_id, 'project-thread')
})

test('并行 ls 的分片参数、乱序结果和界面状态按 ID 匹配', async t => {
  t.mock.method(globalThis, 'fetch', async () => streamResponse([
    start('main'), start('proc'), start('order'),
    args('main', '{"path":'), args('proc', '{"path":'), args('order', '{"path":'),
    args('proc', '"/skills/procurement"}'), args('main', '"/skills/main"}'), args('order', '"/skills/order"}'),
    result('proc', '["采购技能"]'), end('proc'),
    result('main', '["skill-management"]'), end('main'),
    result('order', '[]'), end('order'), done
  ]))
  const messages = []
  const callbacks = createToolCallbacks(() => messages)
  let firstCompletion = null
  const response = await streamChat('列出技能', null, {
    ...callbacks,
    onToolEnd(tool) {
      callbacks.onToolEnd(tool)
      firstCompletion ||= messages.map(message => message.tool_status)
    }
  })
  assert.deepEqual(firstCompletion, ['calling', 'done', 'calling'])
  assert.deepEqual(messages.map(message => JSON.parse(message.args).path), ['/skills/main', '/skills/procurement', '/skills/order'])
  assert.deepEqual(messages.map(message => message.text), ['["skill-management"]', '["采购技能"]', '[]'])
  assert.deepEqual(response.tool_calls.map(tool => [tool.id, JSON.parse(tool.args).path]), [
    ['main', '/skills/main'], ['proc', '/skills/procurement'], ['order', '/skills/order']
  ])
})

test('父子工具和重复开始事件不会重复展示，也不会提前结束父调用', async t => {
  t.mock.method(globalThis, 'fetch', async () => streamResponse([
    { ...start('parent'), tool_name: 'task' }, args('parent', '{}'),
    start('child-a'), start('child-b'), start('child-a'),
    args('child-a', '{"path":"/a"}'), args('child-b', '{"path":"/b"}'),
    result('child-a', 'A'), end('child-a'), result('child-b', 'B'), end('child-b'),
    result('parent', 'task result'), end('parent'), done
  ]))
  const messages = []
  const callbacks = createToolCallbacks(() => messages)
  const response = await streamChat('子任务', null, {
    ...callbacks,
    onToolEnd(tool) {
      callbacks.onToolEnd(tool)
      if (tool.id !== 'parent') assert.equal(messages[0].tool_status, 'calling')
    }
  })
  assert.equal(messages.length, 3)
  assert.equal(response.tool_calls.length, 3)
  assert.deepEqual(messages.map(message => message.text), ['task result', 'A', 'B'])
})

test('恢复时仅有结果的工具保留已有参数，未知 ID 不占用其他调用', async t => {
  t.mock.method(globalThis, 'fetch', async () => streamResponse([
    start('active'), args('active', '{"path":"/active"}'),
    result('resumed', '恢复结果'), end('resumed'),
    result('unknown', '权限不足', 'error'), end('unknown'),
    result('active', 'active result'), end('active'), done
  ]))
  const messages = [{ id: 'resumed', role: 'tool', tool_name: 'ls', args: '{"path":"/old"}', tool_status: 'calling' }]
  await resumeChat('test-thread', { decisions: [] }, createToolCallbacks(() => messages))
  assert.equal(messages.length, 3)
  assert.equal(messages[0].args, '{"path":"/old"}')
  assert.equal(messages[0].text, '恢复结果')
  assert.equal(messages[1].text, 'active result')
  assert.equal(messages[2].text, '权限不足')
  assert.equal(messages[2].tool_status, 'error')
})

test('历史消息 ID 与 call_id 不同，恢复结果仍只更新对应卡片', async t => {
  t.mock.method(globalThis, 'fetch', async () => streamResponse([
    { type: 'tool_result', call_id: 'real-call', tool_name: 'generate_visualization', text: '{"sandbox_path":"/analysis/charts/a.png"}', images: ['/api/artifacts/charts/a.png'] }, done
  ]))
  const messages = [{ id: 'db-row-1', tool_call_id: 'real-call', role: 'tool', args: '{"chart_type":"bar"}', tool_status: 'waiting' }]
  await resumeChat('test-thread', {}, createToolCallbacks(() => messages))
  assert.equal(messages.length, 1)
  assert.equal(messages[0].args, '{"chart_type":"bar"}')
  assert.equal(messages[0].tool_status, 'done')
  assert.deepEqual(messages[0].images, ['/api/artifacts/charts/a.png'])
})

test('服务端错误与提前断流不能当成正常完成', async t => {
  for (const events of [[start('a'), { type: 'error', message: '模型连接失败' }], [start('a')]]) {
    t.mock.method(globalThis, 'fetch', async () => streamResponse(events))
    let errors = 0, completions = 0
    await assert.rejects(streamChat('test', null, { onError: () => errors++, onDone: () => completions++ }))
    assert.equal(errors, 1)
    assert.equal(completions, 0)
  }
})

test('无 ID 的结果不会覆盖正在执行的工具，迟到的参数有独立占位', async t => {
  t.mock.method(globalThis, 'fetch', async () => streamResponse([
    start('a'), { type: 'tool_result', text: 'missing id' }, args('b', '{"path":"/b"}'),
    start('b'), result('b', 'B'), result('a', 'A'), done
  ]))
  const messages = []
  await streamChat('test', null, createToolCallbacks(() => messages))
  assert.deepEqual(messages.map(m => [m.tool_call_id, m.text]), [['a', 'A'], ['b', 'B']])
  assert.equal(messages[1].args, '{"path":"/b"}')
})

test('停止时保留已收到的文字和工具参数', async t => {
  let count = 0
  const bytes = new TextEncoder().encode([start('a'), args('a', '{"path":"/a"}'), { type: 'token', content: '处理中' }].map(e => `data: ${JSON.stringify(e)}\n\n`).join(''))
  t.mock.method(globalThis, 'fetch', async () => ({ ok: true, body: { getReader: () => ({
    async read() { if (count++ === 0) return { value: bytes, done: false }; throw new DOMException('Stopped', 'AbortError') },
    async cancel() {}, releaseLock() {}
  }) } }))
  const result = await streamChat('test')
  assert.equal(result.aborted, true)
  assert.equal(result.content, '处理中')
  assert.equal(result.tool_calls[0].args, '{"path":"/a"}')
})
