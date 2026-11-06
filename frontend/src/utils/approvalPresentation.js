function firstValue(source, ...keys) {
  for (const key of keys) {
    if (source?.[key] !== undefined && source?.[key] !== null) return source[key]
  }
  return null
}

function numberFrom(text, pattern) {
  const match = String(text || '').match(pattern)
  if (!match) return null
  const value = Number(match[1])
  return Number.isFinite(value) ? value : null
}

function textFrom(text, pattern) {
  const match = String(text || '').match(pattern)
  return match?.[1]?.trim() || ''
}

export function formatMoney(value) {
  if (value === null || value === undefined || value === '') return '—'
  const number = Number(value)
  if (!Number.isFinite(number)) return '—'
  return new Intl.NumberFormat('zh-CN', {
    style: 'currency', currency: 'CNY', minimumFractionDigits: 2
  }).format(number)
}

export function buildApprovalPresentation(action = {}) {
  const args = action.args || {}
  const remark = String(firstValue(args, 'remark') || '')
  const rawItems = firstValue(args, 'orderDetail', 'order_detail')
  const itemList = Array.isArray(rawItems) ? rawItems : []

  const supplier = textFrom(remark, /推荐\s*partId\s*=\s*\d+\s*[（(]\s*([^，,)]+)/i)
    || String(firstValue(args, 'supplierName', 'supplier_name') || '待确认供应商')
  const partName = textFrom(remark, /需求[：:]\s*\d+\s*片\s*([^，,。]+)/)
    || String(firstValue(args, 'partName', 'part_name') || '采购物料')
  const budget = numberFrom(remark, /预算\s*([\d.]+)\s*元/i)
  const leadTimeDays = numberFrom(remark, /推荐\s*partId[\s\S]*?交期\s*([\d.]+)\s*天/i)
  const score = numberFrom(remark, /推荐\s*partId[\s\S]*?评分\s*([\d.]+)/i)
  const quantityFromDemand = numberFrom(remark, /需求[：:]\s*([\d.]+)\s*片/i)
  const decisionId = firstValue(args, 'decision_id', 'decisionId')
    || textFrom(remark, /比价决策\s*(DEC-[A-Z0-9-]+)/i)
  const policyVersion = firstValue(args, 'policy_version', 'policyVersion')
    || textFrom(remark, /[（(](supplier-score-[^)）]+)[)）]/i)
  const applicant = textFrom(remark, /申请人\s*([^。；;\s]+)/)

  const items = itemList.map((item, index) => {
    const quantity = Number(firstValue(item, 'quantity'))
    const unitPrice = Number(firstValue(item, 'unitPrice', 'unit_price'))
    const rawSubtotal = firstValue(item, 'subtotal')
    const explicitSubtotal = rawSubtotal === null ? null : Number(rawSubtotal)
    const subtotal = explicitSubtotal !== null && Number.isFinite(explicitSubtotal)
      ? explicitSubtotal
      : (Number.isFinite(quantity) && Number.isFinite(unitPrice) ? quantity * unitPrice : null)
    return {
      index: index + 1,
      partId: firstValue(item, 'partId', 'part_id'),
      name: itemList.length === 1 ? partName : `采购物料 ${index + 1}`,
      quantity: Number.isFinite(quantity) ? quantity : null,
      unitPrice: Number.isFinite(unitPrice) ? unitPrice : null,
      subtotal
    }
  })

  const explicitTotal = firstValue(args, 'total_amount', 'totalAmount')
  const totalAmount = items.length
    ? items.reduce((sum, item) => sum + (item.subtotal || 0), 0)
    : (explicitTotal === null ? null : Number(explicitTotal))
  const quantity = items.reduce((sum, item) => sum + (item.quantity || 0), 0) || quantityFromDemand
  const budgetRemaining = budget !== null ? budget - totalAmount : null
  const expectedDate = firstValue(args, 'expected_delivery_date', 'expectedDeliveryDate')
  const isCreate = action.name === 'order_create'

  const checks = []
  if (/预算[\s\S]*?通过/.test(remark)) checks.push({ label: '预算内', tone: 'success' })
  if (/交期[\s\S]*?通过/.test(remark)) checks.push({ label: '交期满足', tone: 'success' })
  if (/MOQ[\s\S]*?通过/i.test(remark)) checks.push({ label: '起订量满足', tone: 'success' })
  if (/供应商[\s\S]*?通过/.test(remark)) checks.push({ label: '供应商准入通过', tone: 'success' })

  return {
    isCreate,
    title: isCreate ? '采购订单待确认' : '订单变更待确认',
    subtitle: isCreate
      ? '请核对关键业务信息。确认后仅创建一张待审核订单，不会直接采购或扣减库存。'
      : '请核对本次变更。确认后只更新待审核订单中的允许字段。',
    supplier,
    partName,
    quantity,
    unitPrice: items.length === 1 ? items[0].unitPrice : null,
    totalAmount,
    budget,
    budgetRemaining,
    leadTimeDays,
    expectedDate,
    score,
    checks,
    items,
    audit: {
      decisionId: decisionId || '—',
      policyVersion: policyVersion || '—',
      applicant: applicant || '—',
      orderNumber: firstValue(args, 'order_number', 'orderNumber') || '由系统生成',
      partIds: items.map(item => item.partId).filter(value => value !== null).join('、') || '—'
    }
  }
}
