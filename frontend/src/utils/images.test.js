import test from 'node:test'
import assert from 'node:assert/strict'
import { normalizeImageSource, getToolImages } from './images.js'
import { renderMarkdown } from './markdown.js'
import { groupChatMessages, settleToolCalls, getToolStatus } from './toolCalls.js'

test('沙箱图表、历史相对路径与结构化结果指向同一浏览器地址', () => {
  const expected = '/api/artifacts/charts/chart_bar_abc.png'
  for (const path of ['/analysis/charts/chart_bar_abc.png', 'charts/chart_bar_abc.png', 'sandbox:/analysis/charts/chart_bar_abc.png', expected]) assert.equal(normalizeImageSource(path), expected)
  assert.deepEqual(getToolImages({ text: JSON.stringify({ sandbox_path: '/analysis/charts/chart_bar_abc.png', source_url: 'https://example.com/old.png' }), images: ['charts/chart_bar_abc.png'] }), [expected])
  assert.equal(normalizeImageSource('/analysis/charts/../secret.png'), '')
  assert.equal(normalizeImageSource('javascript:alert(1)'), '')
})

test('Markdown 支持没有扩展名的图片 URL，安全转义属性且不渲染脚本', () => {
  const html = renderMarkdown('![图表](https://example.com/render?id=1)\n\n<script>alert(1)</script>')
  assert.match(html, /<img src="https:\/\/example.com\/render\?id=1"/)
  assert.doesNotMatch(html, /<script>/)
  assert.match(renderMarkdown('![图表](charts/chart.png)'), /src="\/api\/artifacts\/charts\/chart.png"/)
  assert.doesNotMatch(renderMarkdown('![图表](charts/chart.png)', false), /<img/)
})

test('历史重复结果按 call_id 合并，隐藏执行过程仍能看到图表', () => {
  const messages = [
    { id: 'row1', tool_call_id: 'a', role: 'tool', args: '{"chart_type":"bar"}' },
    { id: 'reply', role: 'assistant', content: '完成' },
    { id: 'row2', tool_call_id: 'a', role: 'tool', args: '', text: '{"sandbox_path":"/analysis/charts/chart.png"}', tool_status: 'done' }
  ]
  const groups = groupChatMessages(messages, false)
  assert.equal(groups.length, 2)
  assert.equal(groups[0].imagesOnly, true)
  assert.equal(groups[0].tools.length, 1)
  assert.equal(groups[0].tools[0].args, '{"chart_type":"bar"}')
  assert.equal(messages[0].text, undefined)
})

test('审批与停止只影响未完成调用，不把未收到的结果标为成功', () => {
  const messages = [{ role: 'tool', tool_status: 'calling' }, { role: 'tool', tool_status: 'done' }]
  settleToolCalls(messages, 'waiting')
  assert.equal(getToolStatus(messages[0]), 'waiting')
  assert.equal(getToolStatus(messages[1]), 'done')
})
