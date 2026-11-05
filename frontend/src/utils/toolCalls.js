import { getToolImages, parseResult } from './images.js'

export function getCallId(tool) { return tool.tool_call_id || tool.call_id || tool.id }

/** 每个用户问题对应一个回复；中间说明及子助手输出保留在执行过程里。 */
export function groupReplyTurns(messages, showToolCalls = true, streaming = false) {
  const result = []
  let parts = []
  let replyId = 'reply-initial'
  const flush = (pending = false) => {
    if (!parts.length && !pending) return
    const grouped = groupChatMessages(parts)
    const answer = [...grouped].reverse().find(part =>
      part.role === 'assistant' && (!part.source || part.source === 'main') && part.content?.trim())
    const tools = grouped.flatMap(part => part.role === 'tool-group' ? part.tools : [])
    result.push({ id: replyId, role: 'assistant', source: 'main', content: answer?.content || '',
      steps: showToolCalls ? grouped.filter(part => part !== answer) : [],
      toolCount: tools.length, images: [...new Set(tools.flatMap(getToolImages))] })
    parts = []
  }
  for (const [index, message] of messages.entries()) {
    if (message.role === 'user') {
      flush()
      result.push(message)
      replyId = `reply-${message.id || index}`
    } else if (['assistant', 'tool'].includes(message.role)) {
      parts.push(message)
    }
  }
  flush(streaming && messages.length > 0)
  return result
}

/** 按首次请求顺序展示，历史记录的同一调用也合并到原位置。 */
export function groupChatMessages(messages, showToolCalls = true) {
  const groups = []
  const unique = []
  const byId = new Map()
  for (const message of messages) {
    const id = message.role === 'tool' && getCallId(message)
    if (id && byId.has(id)) {
      const existing = byId.get(id)
      Object.assign(existing, message, { args: message.args || existing.args, id: existing.id })
    } else {
      const copy = { ...message }
      unique.push(copy)
      if (id) byId.set(id, copy)
    }
  }
  for (const [index, message] of unique.entries()) {
    if (message.role !== 'tool') {
      groups.push(message)
      continue
    }
    if (!showToolCalls && !getToolImages(message).length) continue
    const previous = groups.at(-1)
    if (previous?.role === 'tool-group') {
      previous.tools.push(message)
    } else {
      groups.push({ role: 'tool-group', id: `tools-${getCallId(message) || index}`, imagesOnly: !showToolCalls, tools: [message] })
    }
  }
  return groups
}

export function summarizeToolCalls(tools) {
  const counts = new Map()
  for (const tool of tools) {
    const name = tool.tool_name || '未命名工具'
    counts.set(name, (counts.get(name) || 0) + 1)
  }
  return [...counts].map(([name, count]) => ({ name, count }))
}

export function parseToolArgs(args) {
  try {
    const value = typeof args === 'string' ? JSON.parse(args) : args
    return value && typeof value === 'object' && !Array.isArray(value) ? value : {}
  } catch {
    // 流式参数尚未拼接完成时，仍可展开查看原始片段。
    return {}
  }
}

export function getToolTarget(tool) {
  const args = parseToolArgs(tool.args)
  if (args.chart_config?.title) return args.chart_config.title
  return ['file_path', 'path', 'url', 'query', 'pattern', 'command']
    .map(key => args[key]).find(value => typeof value === 'string' && value.trim()) || ''
}

export function formatToolArgs(args) {
  try {
    return JSON.stringify(typeof args === 'string' ? JSON.parse(args) : args, null, 2)
  } catch {
    return String(args)
  }
}

export function getToolStatus(tool, streaming = false) {
  // 文件系统工具有时以普通文本返回 Error，不能仅凭流结束标成成功。
  if (tool.tool_status === 'error' || /^\s*(?:Error\b|错误[:：]|Traceback \(most recent call last\))/.test(tool.text || '')) {
    return 'error'
  }
  if (['waiting', 'stopped', 'ended'].includes(tool.tool_status)) return tool.tool_status
  if (tool.tool_status === 'calling' && streaming) return 'calling'
  if (tool.tool_status === 'done' || tool.text || tool.images?.length) return 'done'
  return 'ended'
}

// 旧名称用于显示已保存的历史会话。
const toolLabels = { generate_chart: '生成图表', generate_visualization: '生成图表', read_file: '读取文件', write_file: '写入文件',
  edit_file: '修改文件', ls: '查看目录', glob: '查找文件', grep: '搜索内容', task: '执行子任务',
  write_analysis_report: '保存分析报告', download_sandbox_file: '下载文件', web_search: '搜索资料' }
export function getToolLabel(tool) { return toolLabels[tool.tool_name] || '调用工具' }
export function getToolSummary(tool, streaming = false) {
  const status = getToolStatus(tool, streaming)
  if (status === 'calling') return '正在执行，等待返回结果…'
  if (status === 'waiting') return '等待你的确认或补充信息'
  if (status === 'stopped') return '已停止等待，尚未收到结果'
  if (status === 'ended') return '本次调用未收到结果'
  if (getToolImages(tool).length) return `已生成 ${getToolImages(tool).length} 张图表`
  const parsed = parseResult(tool.text)
  if (Array.isArray(parsed)) return `返回 ${parsed.length} 条记录`
  if (parsed && typeof parsed === 'object') return parsed.message || (status === 'error' ? '执行失败，可展开查看原因' : '已返回数据，可展开查看详情')
  const text = String(tool.text || (status === 'error' ? '执行失败' : '执行完成'))
  // 去掉 read_file 结果顶部的状态头（如 @@ lines 1-6 of 6 @@），预览只保留正文
  const body = text.replace(/^@@[^\n]*@@\s*\n?/, '')
  const oneLine = body.replace(/\s+/g, ' ').trim()
  return oneLine.length > 140 ? oneLine.slice(0, 137) + '…' : oneLine
}

export function settleToolCalls(messages, status = 'ended') {
  for (const message of messages) {
    if (message.role === 'tool' && message.tool_status === 'calling') message.tool_status = status
  }
}

/** 首次对话和中断恢复共用，完成一个工具时不改变其他工具的状态。 */
export function createToolCallbacks(getMessages) {
  const findTool = id => id && getMessages().find(message => message.role === 'tool' && getCallId(message) === id)
  const ensureTool = tool => {
    const id = getCallId(tool)
    if (!id) return null
    let message = findTool(id)
    if (!message) {
      message = { id, tool_call_id: id, role: 'tool', tool_name: tool.name || tool.tool_name,
        args: tool.args || '', text: '', images: [], source: tool.source || 'main', tool_status: 'calling' }
      getMessages().push(message)
      message = findTool(id) // 取 Vue 响应式代理，后续修改能够触发渲染。
    }
    if (tool.name || tool.tool_name) message.tool_name = tool.name || tool.tool_name
    if (tool.source) message.source = tool.source
    if (!message.args && tool.args) message.args = tool.args
    return message
  }
  return {
    onToolStart(tool) {
      const message = ensureTool(tool)
      if (message?.tool_status === 'waiting') message.tool_status = 'calling'
    },
    onToolArgs(args, source, id) {
      const message = ensureTool({ id, source })
      if (message) message.args += args
    },
    onToolResult(tool) {
      const message = ensureTool(tool)
      if (message) {
        message.text = tool.text || ''
        message.images = tool.images || []
        message.tool_status = tool.tool_status || 'done'
      }
    },
    onToolEnd(tool) {
      const message = ensureTool(tool)
      if (message?.tool_status === 'calling') message.tool_status = tool.tool_status || 'ended'
    }
  }
}
