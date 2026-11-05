import test from 'node:test'
import assert from 'node:assert/strict'
import { getSourceName } from './messageSources.js'

test('代理名称与旧历史运行 ID 显示为友好标签', () => {
  assert.equal(getSourceName('procurement-analyst'), '采购分析子智能体')
  assert.equal(getSourceName('procurement-order'), '采购订单子智能体')
  assert.equal(getSourceName('f7929a7c-9d3d-0eee-b565-bce633224c3f'), '子助手')
  assert.equal(getSourceName('tools:f7929a7c-9d3d-0eee-b565-bce633224c3f'), '子助手')
  assert.equal(getSourceName('subagent'), '子助手')
  assert.equal(getSourceName('main'), '主助手')
})
