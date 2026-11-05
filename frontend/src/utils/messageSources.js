const sourceNames = {
  main: '主助手',
  'procurement-analyst': '采购分析子智能体',
  'procurement-order': '采购订单子智能体',
  'chart-agent': '图表助手',
  researcher: '研究助手',
  'model-agent': '模型助手',
  general: '通用助手',
  'general-purpose': '通用助手',
  subagent: '子助手'
}

export function getSourceName(source) {
  if (!source) return sourceNames.main
  if (sourceNames[source]) return sourceNames[source]
  // 兼容数据库中旧的 tools:<运行 ID> / UUID 来源，不把内部 ID 当名称展示。
  if (source.startsWith('tools:') || /[a-f\d]{8}-[a-f\d]{4}-[a-f\d]{4}-[a-f\d]{4}-[a-f\d]{12}/i.test(source)) return '子助手'
  return source
}
