/** 统一流式对话和恢复：按 call_id 关联，按首次请求顺序保留工具。 */
import { getCallId } from '../utils/toolCalls.js'
import { apiFetch } from './http.js'

const STREAM_IDLE_TIMEOUT_MS = 100_000

export function streamChat(message, threadId = null, callbacks = {}, signal = null, projectId = null) {
  return requestStream('/api/chat/stream', {
    message, thread_id: threadId, ...(threadId ? {} : { project_id: projectId })
  }, threadId, callbacks, signal)
}

export function resumeChat(threadId, resumeData, callbacks = {}, signal = null) {
  return requestStream(`/api/chat/${encodeURIComponent(threadId)}/resume`, { resume: resumeData }, threadId, callbacks, signal)
}

async function requestStream(url, body, threadId, callbacks, signal) {
  const state = { thread_id: threadId, content: '', tools: new Map() }
  const snapshot = extra => ({ thread_id: state.thread_id, content: state.content,
    tool_calls: [...state.tools.values()].map(tool => ({ ...tool })), ...extra })
  let reader
  try {
    const response = await apiFetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal })
    reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''

    function ensureTool(data) {
      const id = getCallId(data)
      if (!id) return null // 无 ID 的事件不能覆盖其他调用。
      let tool = state.tools.get(id)
      if (!tool) {
        tool = { id, tool_call_id: id, name: data.tool_name, source: data.source, args: data.args && data.type !== 'tool_args' ? data.args : '', text: '', images: [], tool_status: 'calling' }
        state.tools.set(id, tool)
        callbacks.onToolStart?.({ ...tool })
      }
      if (data.tool_name) tool.name = data.tool_name
      if (data.source) tool.source = data.source
      return tool
    }

    function consume(line) {
      if (!line.startsWith('data:')) return null
      let data
      try { data = JSON.parse(line.slice(5).trim()) }
      catch { return null } // 仅跳过无效 JSON；服务端 error 事件必须交给调用方。
      if (data.thread_id) state.thread_id = data.thread_id
      switch (data.type) {
        case 'session':
          callbacks.onSession?.({ thread_id: state.thread_id })
          break
        case 'progress':
          callbacks.onProgress?.({ stage: data.stage, message: data.message || '正在处理…' })
          break
        case 'token':
          state.content += data.content || ''
          callbacks.onToken?.(data.content || '', data.source)
          break
        case 'tool_start': {
          const existed = state.tools.has(getCallId(data))
          const tool = ensureTool(data)
          if (existed && tool) callbacks.onToolStart?.({ ...tool })
          break
        }
        case 'tool_args': {
          const tool = ensureTool(data)
          if (tool) {
            tool.args += data.args || ''
            callbacks.onToolArgs?.(data.args || '', data.source, tool.id)
          }
          break
        }
        case 'tool_result': {
          const tool = ensureTool(data)
          if (tool) {
            Object.assign(tool, { text: data.text || '', images: data.images || [], tool_status: data.tool_status || 'done' })
            if (data.args) tool.args = data.args
            callbacks.onToolResult?.({ ...tool })
          }
          break
        }
        case 'tool_end': {
          const tool = ensureTool(data)
          if (tool) {
            if (tool.tool_status === 'calling') tool.tool_status = data.tool_status || 'ended'
            callbacks.onToolEnd?.({ ...tool })
          }
          break
        }
        case 'interrupt':
          callbacks.onInterrupt?.(data)
          return snapshot({ interrupted: true, interrupt_data: data })
        case 'done': {
          state.content = data.content || state.content
          const result = snapshot({ interrupted: data.interrupted || false })
          callbacks.onDone?.(result)
          return result
        }
        case 'error': throw new Error(data.message || '请求执行失败')
      }
      return null
    }

    while (true) {
      const { done, value } = await readWithIdleTimeout(reader, STREAM_IDLE_TIMEOUT_MS)
      buffer += decoder.decode(value, { stream: !done })
      const lines = buffer.split('\n')
      buffer = lines.pop()
      if (done && buffer) { lines.push(buffer); buffer = '' }
      for (const line of lines) {
        const result = consume(line.trimEnd())
        if (result) return result
      }
      if (done) throw new Error('连接已结束，未收到完成通知。可以重试或查看会话历史。')
    }
  } catch (error) {
    if (error.name === 'AbortError') {
      const result = snapshot({ aborted: true })
      callbacks.onDone?.(result)
      return result
    }
    callbacks.onError?.(error)
    throw error
  } finally {
    if (reader) {
      await reader.cancel().catch(() => {})
      reader.releaseLock()
    }
  }
}

async function readWithIdleTimeout(reader, timeoutMs) {
  let timer
  try {
    return await Promise.race([
      reader.read(),
      new Promise((_, reject) => {
        timer = setTimeout(() => reject(new Error('长时间未收到处理进度，请重试；已完成的结果仍会保存在会话历史中。')), timeoutMs)
      })
    ])
  } finally {
    clearTimeout(timer)
  }
}
