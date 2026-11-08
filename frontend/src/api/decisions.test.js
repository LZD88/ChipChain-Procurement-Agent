import test from 'node:test'
import assert from 'node:assert/strict'
import { getDecision, getDecisions, getReplayPolicies, replayDecision } from './decisions.js'

test('decision API uses authenticated same-origin paths and replay body', async () => {
  const calls = []
  global.fetch = async (url, options) => {
    calls.push([url, options])
    return { ok: true, json: async () => ({}) }
  }
  await getDecisions(2, 10)
  await getDecision('DEC/一')
  await getReplayPolicies()
  await replayDecision('DEC/一', 'supplier-score-v1')
  assert.deepEqual(calls.map(call => call[0]), [
    '/api/decisions?page=2&size=10',
    '/api/decisions/DEC%2F%E4%B8%80',
    '/api/decisions/policies',
    '/api/decisions/DEC%2F%E4%B8%80/replay',
  ])
  assert.equal(calls[3][1].credentials, 'same-origin')
  assert.deepEqual(JSON.parse(calls[3][1].body), { policyVersion: 'supplier-score-v1' })
})
