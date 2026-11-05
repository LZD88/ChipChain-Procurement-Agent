import test from 'node:test'
import assert from 'node:assert/strict'
import { groupChatMessages, summarizeToolCalls, getToolTarget, getToolStatus, formatToolArgs } from './toolCalls.js'

const tool = (id, name, args, text = '') => ({ id, role: 'tool', tool_name: name, args, text, tool_status: 'done' })

test('真实重复尝试保留明细，汇总名称和次数，不跨越 AI 回复合并', () => {
  const user = { id: 'user', role: 'user', content: '你知道我的偏好吗' }
  const assistant = { id: 'reply', role: 'assistant', content: '再检查目录。' }
  const first = tool('read-1', 'read_file', '{"file_path":"/memories/nf/preferences.md"}', 'Error: File not found')
  const second = { ...first, id: 'read-2' }
  const third = tool('ls-1', 'ls', '{"path":"/memories"}', '[]')
  const fourth = tool('ls-2', 'ls', '{"path":"/memories/nf"}', '[]')
  const messages = [user, first, second, third, fourth, assistant, tool('write', 'write_file', '{}')]
  const result = groupChatMessages(messages)
  assert.deepEqual(result.map(item => item.role), ['user', 'tool-group', 'assistant', 'tool-group'])
  assert.deepEqual(result[1].tools, [first, second, third, fourth])
  assert.deepEqual(summarizeToolCalls(result[1].tools), [{ name: 'read_file', count: 2 }, { name: 'ls', count: 2 }])
  assert.equal(getToolTarget(third), '/memories')
  assert.equal(getToolTarget(fourth), '/memories/nf')
  assert.equal(messages.length, 7)
})

test('关闭工具开关只显示用户和 AI 消息', () => {
  const user = { role: 'user' }
  const reply = { role: 'assistant' }
  assert.deepEqual(groupChatMessages([user, tool('read', 'read_file', '{}'), reply], false), [user, reply])
})

test('流式参数未完成时可查看原文，完整参数同时兼容对象和字符串', () => {
  assert.equal(getToolTarget(tool('read', 'read_file', '{"file_path":')), '')
  assert.equal(formatToolArgs('{"file_path":'), '{"file_path":')
  assert.equal(getToolTarget(tool('read', 'read_file', { file_path: '/memories/nf/preferences.md' })), '/memories/nf/preferences.md')
  assert.equal(formatToolArgs({ path: '/memories' }), '{\n  "path": "/memories"\n}')
})

test('工具返回错误时不显示成功，空结果、图表和执行中分别识别', () => {
  assert.equal(getToolStatus(tool('read', 'read_file', '{}', 'Error: File not found')), 'error')
  assert.equal(getToolStatus({ tool_status: 'error', text: '权限不足' }), 'error')
  assert.equal(getToolStatus(tool('ls', 'ls', '{}', '[]')), 'done')
  assert.equal(getToolStatus({ tool_status: 'done', text: '' }), 'done')
  assert.equal(getToolStatus({ tool_status: 'calling' }, true), 'calling')
  assert.equal(getToolStatus({ tool_status: 'calling' }, false), 'ended')
  assert.equal(getToolStatus({ images: ['chart.png'] }), 'done')
  assert.equal(getToolStatus({ text: 'The word Error appears in this file.' }), 'done')
})

test('追加工具片段和调用后，分组 key 保持稳定且保留图片', () => {
  const first = tool('chart', 'generate_chart', '{}')
  first.images = ['chart.png']
  const before = groupChatMessages([first])
  const after = groupChatMessages([first, tool('ls', 'ls', '{}')])
  assert.equal(before[0].id, after[0].id)
  assert.deepEqual(after[0].tools[0].images, ['chart.png'])
  assert.equal(before[0].tools.length, 1)
})
