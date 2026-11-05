import test from 'node:test'
import assert from 'node:assert/strict'
import { groupReplyTurns } from './toolCalls.js'

const user = (id, content) => ({ id, role: 'user', content })
const ai = (id, content, source = 'main') => ({ id, role: 'assistant', content, source })
const tool = (id, text = '') => ({ id, role: 'tool', tool_name: 'read_file', text, tool_status: 'done' })

test('查询偏好经过读取和创建后只有一个回复，过渡语放入过程，历史与实时分组相同', () => {
  const messages = [user('q1', '我有哪些偏好'), ai('a1', '让我查看一下'), tool('read', 'not found'),
    ai('a2', '我这就创建默认偏好'), tool('write', 'updated'), ai('a3', '您的默认偏好如下：图表、人民币、中文。')]
  const before = JSON.stringify(messages)
  const [question, answer] = groupReplyTurns(messages)
  assert.equal(question.content, '我有哪些偏好')
  assert.equal(answer.content, messages.at(-1).content)
  assert.equal(answer.toolCount, 2)
  assert.equal(answer.steps.filter(step => step.role === 'assistant').length, 2)
  assert.equal(groupReplyTurns(messages).length, 2)
  assert.deepEqual(groupReplyTurns(messages, true, true), groupReplyTurns(messages))
  assert.equal(JSON.stringify(messages), before)
})

test('流式片段、工具和最终回复沿用同一个回复 ID，不跨越下一条用户问题', () => {
  const question = user('q1', '问题一')
  const placeholder = groupReplyTurns([question], true, true)[1]
  const running = groupReplyTurns([question, ai('a1', '处理中'), tool('read')], true, true)[1]
  const done = groupReplyTurns([question, ai('a1', '处理中'), tool('read'), ai('a2', '最终回复')])[1]
  assert.equal(placeholder.id, running.id)
  assert.equal(running.id, done.id)
  const turns = groupReplyTurns([question, ai('a1', '回复一'), user('q2', '问题二'), ai('a2', '回复二')])
  assert.deepEqual(turns.map(item => item.role), ['user', 'assistant', 'user', 'assistant'])
  assert.equal(turns[1].content, '回复一')
  assert.equal(turns[3].content, '回复二')
  assert.notEqual(turns[1].id, turns[3].id)
})

test('子助手报告保留在过程，关闭过程时图表和最终答案仍可见', () => {
  const chart = { ...tool('chart'), images: ['https://example.com/chart.png'] }
  const messages = [user('q', '分析'), ai('sub', '子助手报告', 'procurement-analyst'), chart, ai('final', '最终结论')]
  const reply = groupReplyTurns(messages)[1]
  assert.equal(reply.content, '最终结论')
  assert.equal(reply.steps[0].content, '子助手报告')
  const hidden = groupReplyTurns(messages, false)[1]
  assert.equal(hidden.content, reply.content)
  assert.deepEqual(hidden.steps, [])
  assert.deepEqual(hidden.images, ['https://example.com/chart.png'])
})

test('审批恢复、停止或报错仍在同一回复中；不同轮次的重复调用 ID 不互相覆盖', () => {
  const messages = [user('q1', '下单'), tool('call', '等待确认'), ai('a1', '等待您的批准'),
    { ...tool('call', '执行失败'), tool_status: 'error' }, ai('a2', '订单执行失败，请重试')]
  const result = groupReplyTurns(messages)[1]
  assert.equal(result.content, '订单执行失败，请重试')
  assert.equal(result.toolCount, 1)
  assert.equal(result.steps[0].tools[0].tool_status, 'error')
  const second = groupReplyTurns([...messages, user('q2', '重试'), tool('call', '成功'), ai('a3', '已完成')])
  assert.equal(second[1].steps[0].tools[0].text, '执行失败')
  assert.equal(second[3].steps[0].tools[0].text, '成功')
})
