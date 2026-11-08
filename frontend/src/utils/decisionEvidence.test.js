import test from 'node:test'
import assert from 'node:assert/strict'
import { candidateRows, drillDowns, panelMessage, replayPresentation } from './decisionEvidence.js'

test('panel states cover loading and access errors', () => {
  assert.match(panelMessage({ loading: true }), /加载/)
  assert.match(panelMessage({ error: { status: 404 } }), /无权访问/)
})

test('missing links and expired traces degrade independently', () => {
  const links = drillDowns({
    decision: { traceId: 'TRACE-1' },
    lifecycle: [{ eventType: 'TRACE_UNAVAILABLE', payload: { traceAvailable: false, erpAuditRefStatus: 'PENDING' } }],
  })
  assert.equal(links.find(link => link.key === 'trace').status, 'expired')
  assert.equal(links.find(link => link.key === 'order').status, 'missing')
  assert.equal(links.find(link => link.key === 'audit').status, 'pending')
})

test('candidate explanations and replay are visibly simulated', () => {
  const rows = candidateRows({ decision: { result: { candidates: [{
    partId: 7, partName: '传感器', eligible: false,
    rejectedReasons: ['超过预算'], explanation: ['价格竞争力 35/35'], totalScore: 80,
  }] } } })
  assert.deepEqual(rows[0].reasons, ['超过预算'])
  const replay = replayPresentation({
    manifest: { selectedPolicyVersion: 'supplier-score-v1' },
    diff: { recommendation: { changed: false }, candidates: [{ partId: 7 }] },
  })
  assert.match(replay.label, /模拟回放/)
  assert.match(replay.label, /不会触发/)
  assert.equal(replay.candidates.length, 1)
})
