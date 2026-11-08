export function panelMessage({ loading, error, detail }) {
  if (loading) return '正在加载决策证据…'
  if (error?.status === 404) return '无权访问或决策证据不存在。'
  if (error) return error.message || '决策证据加载失败。'
  if (!detail) return '请选择一条决策记录。'
  return ''
}

export function candidateRows(detail) {
  return (detail?.decision?.result?.candidates || []).map((candidate, index) => ({
    rank: index + 1,
    partId: candidate.partId,
    partName: candidate.partName || candidate.partCode || `物料 ${candidate.partId}`,
    supplierName: candidate.supplierName || '供应商未知',
    eligible: candidate.eligible === true,
    reasons: candidate.rejectedReasons || [],
    explanation: candidate.explanation || [],
    totalScore: candidate.totalScore,
  }))
}

export function drillDowns(detail) {
  const decision = detail?.decision || {}
  const events = detail?.lifecycle || []
  const payloads = events.map(event => event.payload || {})
  const last = key => [...payloads].reverse().find(payload => payload[key] != null)?.[key]
  const traceUnavailable = events.some(event => event.eventType === 'TRACE_UNAVAILABLE' || event.payload?.traceAvailable === false)
  const traceId = last('traceId') || decision.traceId
  const orderId = last('orderId')
  const auditRef = last('erpAuditRef')
  const auditStatus = last('erpAuditRefStatus')
  return [
    { key: 'trace', label: '分布式 Trace', value: traceId, status: traceId ? (traceUnavailable ? 'expired' : 'available') : 'missing' },
    { key: 'order', label: 'ERP 订单', value: orderId, status: orderId ? 'available' : 'missing' },
    { key: 'audit', label: 'ERP 审计', value: auditRef, status: auditRef ? 'available' : (auditStatus === 'PENDING' ? 'pending' : 'missing') },
    { key: 'evaluation', label: '关联评测', value: last('evaluationRunId'), status: last('evaluationRunId') ? 'available' : 'missing' },
  ]
}

export function replayPresentation(replay) {
  if (!replay) return null
  return {
    label: '模拟回放 · 不会触发审批或下单',
    policyVersion: replay.manifest?.selectedPolicyVersion,
    recommendation: replay.diff?.recommendation,
    candidates: replay.diff?.candidates || [],
  }
}
