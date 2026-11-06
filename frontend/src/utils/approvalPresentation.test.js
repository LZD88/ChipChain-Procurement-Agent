import test from 'node:test'
import assert from 'node:assert/strict'
import { buildApprovalPresentation, formatMoney } from './approvalPresentation.js'

test('审批展示将技术参数转换为业务摘要并自动计算小计', () => {
  const view = buildApprovalPresentation({
    name: 'order_create',
    args: {
      decision_id: 'DEC-40D6EC87612DD704',
      policy_version: 'supplier-score-v1',
      expected_delivery_date: '2026-11-19',
      remark: '需求：500片六轴惯性传感器，预算9000元，14天内交付。比价决策DEC-40D6EC87612DD704（supplier-score-v1）。推荐partId=24（晶穹电子，单价17.06，总额8530.00，交期7天，评分97.42）；硬约束：预算8530≤9000通过，交期7≤14通过，MOQ 200≤500通过，供应商合作中通过。申请人13800000001。',
      order_detail: [{ partId: 24, quantity: 500, unitPrice: 17.06 }]
    }
  })

  assert.equal(view.title, '采购订单待确认')
  assert.equal(view.supplier, '晶穹电子')
  assert.equal(view.partName, '六轴惯性传感器')
  assert.equal(view.totalAmount, 8530)
  assert.equal(view.budgetRemaining, 470)
  assert.equal(view.leadTimeDays, 7)
  assert.equal(view.items[0].subtotal, 8530)
  assert.equal(view.audit.decisionId, 'DEC-40D6EC87612DD704')
  assert.deepEqual(view.checks.map(item => item.label), ['预算内', '交期满足', '起订量满足', '供应商准入通过'])
})

test('金额统一显示为人民币业务格式', () => {
  assert.equal(formatMoney(8530), '¥8,530.00')
  assert.equal(formatMoney(null), '—')
})
