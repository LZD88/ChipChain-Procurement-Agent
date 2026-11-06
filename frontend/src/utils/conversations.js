import { computed, markRaw, reactive, ref } from 'vue'
import { createToolCallbacks, settleToolCalls } from './toolCalls.js'

/** 页面选择与请求生命周期分开；回调始终写入发起请求的会话。 */
export function createConversations({ streamChat, resumeChat, getMessages, onSessionsChanged = () => {} }) {
  const conversations = reactive(new Map())
  const currentKey = ref(null)
  let disposed = false
  let serial = 0
  const id = prefix => `${prefix}-${Date.now()}-${++serial}`

  function create(threadId = null, projectId = null) {
    const key = id('conversation')
    conversations.set(key, {
      key, threadId, projectId, messages: [], draft: '', interruptData: null,
      isStreaming: false, isResuming: false, processingStatus: '', status: 'idle',
      unread: false, loaded: !threadId, loading: false, revision: 0, run: null,
      updatedAt: new Date().toISOString(),
    })
    return conversations.get(key)
  }
  const current = computed(() => conversations.get(currentKey.value))
  const find = threadId => [...conversations.values()].find(state => state.threadId === threadId)

  function newChat(projectId = null) {
    const previous = current.value
    if (previous && !previous.threadId && !previous.run && !previous.messages.length && !previous.draft) {
      conversations.delete(previous.key)
    }
    const state = create(null, projectId)
    currentKey.value = state.key
    return state
  }

  async function select(threadId) {
    const state = find(threadId) || conversations.get(threadId) || create(threadId)
    currentKey.value = state.key
    state.unread = false
    if (state.loaded || state.loading) return
    state.loading = true
    const revision = state.revision
    try {
      const response = await getMessages(threadId)
      // 历史请求可能晚于新一轮回复返回，不能覆盖已经更新的消息。
      if (!disposed && conversations.has(state.key) && state.revision === revision && !state.run) {
        state.messages = response.messages || []
        state.loaded = true
      }
    } catch (error) {
      if (state.revision === revision && !disposed) state.processingStatus = error.message || '加载会话失败，请重试'
    } finally {
      state.loading = false
    }
  }

  function bindThread(state, threadId) {
    if (!threadId || state.threadId === threadId) return
    state.threadId = threadId
    onSessionsChanged()
  }

  function stop(state = current.value) {
    if (!state?.run) return
    const run = state.run
    state.run = null // 先失效回调，停止后的迟到事件不能修改下一轮。
    state.isStreaming = false
    state.isResuming = false
    state.status = 'stopped'
    state.processingStatus = ''
    settleToolCalls(state.messages, 'stopped')
    run.controller.abort()
  }

  async function execute(state, payload, resuming) {
    if (disposed || state.run || state.loading) return
    if (!resuming && state.interruptData) return
    if (resuming && !state.threadId) return
    state.revision++
    state.loaded = true
    state.unread = false
    state.status = 'running'
    state.isStreaming = !resuming
    state.isResuming = resuming
    state.processingStatus = resuming ? '正在继续执行采购流程…' : '正在理解采购需求…'
    state.updatedAt = new Date().toISOString()
    if (!resuming) {
      state.messages.push({ id: id('user'), role: 'user', content: payload })
      state.interruptData = null
    }
    const run = markRaw({ controller: new AbortController() })
    state.run = run
    const active = () => !disposed && state.run === run && conversations.has(state.key)
    const notify = () => { if (currentKey.value !== state.key) state.unread = true }
    const callbacks = {
      onSession: data => bindThread(state, data.thread_id),
      onProgress: data => { state.processingStatus = data.message || '正在处理采购任务…' },
      onToken(content, source = 'main') {
        state.processingStatus = '正在生成采购建议…'
        const last = state.messages.at(-1)
        if (last?.role === 'assistant' && last.source === source) last.content += content
        else state.messages.push({ id: id('assistant'), role: 'assistant', content, source })
      },
      ...createToolCallbacks(() => state.messages),
      onInterrupt(data) {
        bindThread(state, data.thread_id)
        settleToolCalls(state.messages, 'waiting')
        state.messages = state.messages.filter(message => message.role !== 'assistant' || message.content)
        state.interruptData = data
        state.status = 'waiting'
        notify()
      },
      onDone(data) {
        bindThread(state, data.thread_id)
        state.processingStatus = ''
        settleToolCalls(state.messages, data.aborted ? 'stopped' : data.interrupted ? 'waiting' : 'ended')
        state.messages = state.messages.filter(message => message.role !== 'assistant' || message.content)
        if (!data.interrupted) state.interruptData = null
        state.status = data.aborted ? 'stopped' : data.interrupted ? 'waiting' : 'complete'
        state.updatedAt = new Date().toISOString()
        if (!data.aborted) notify()
        onSessionsChanged()
      },
      onError(error) {
        const inputRejected = resuming && error.code?.startsWith('input_')
        settleToolCalls(state.messages, inputRejected ? 'waiting' : 'ended')
        if (!inputRejected) state.interruptData = null
        state.status = inputRejected ? 'waiting' : 'error'
        state.processingStatus = ''
        state.messages.push({ id: id('assistant'), role: 'assistant', source: 'main', content: `发生了错误：${error.message}` })
        notify()
      },
    }
    const guarded = Object.fromEntries(Object.entries(callbacks).map(([name, callback]) => [
      name, (...args) => { if (active()) callback(...args) },
    ]))
    try {
      if (resuming) await resumeChat(state.threadId, payload, guarded, run.controller.signal)
      else await streamChat(payload, state.threadId, guarded, run.controller.signal, state.projectId)
    } catch { /* API 层已通过 onError 展示错误，避免重复插入消息。 */ }
    finally {
      if (active()) {
        state.isStreaming = false
        state.isResuming = false
        state.run = null
      }
    }
  }

  function remove(threadId) {
    const state = find(threadId)
    if (!state) return
    stop(state)
    conversations.delete(state.key)
    if (currentKey.value === state.key) newChat()
  }

  function dispose() {
    disposed = true
    for (const state of conversations.values()) stop(state)
  }

  newChat()
  return { conversations, current, find, newChat, select, stop, remove, dispose,
    send: message => execute(current.value, message, false),
    resume: data => execute(current.value, data, true) }
}
