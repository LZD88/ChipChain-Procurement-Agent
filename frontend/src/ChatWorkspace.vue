<template>
  <div class="app-container">
    <header class="mobile-toolbar">
      <button type="button" :aria-expanded="mobileSidebarOpen" aria-controls="session-sidebar" @click="mobileSidebarOpen = !mobileSidebarOpen">☰ 会话</button>
      <strong>棱镜工作台</strong>
      <button type="button" @click="handleNewChat">＋ 新建</button>
    </header>
    <button v-if="mobileSidebarOpen" class="sidebar-backdrop" aria-label="关闭会话列表" @click="mobileSidebarOpen = false"></button>
    <!-- 侧边栏 -->
    <Sidebar
      id="session-sidebar"
      :class="{ 'mobile-open': mobileSidebarOpen }"
      :sessions="sessions"
      :current-thread-id="currentThreadId"
      :username="user.username"
      :logging-out="loggingOut"
      @logout="$emit('logout')"
      @select-session="handleSelectSession"
      @new-chat="handleNewChat"
      @delete-session="handleDeleteSession"
    />

    <!-- 主内容区 -->
    <main class="main-content">
      <!-- 对话区域 -->
      <ChatArea
        :messages="messages"
        :streaming="isStreaming || isResuming"
        :show-tool-calls="showToolCalls"
        @suggest="handleSend"
      />

      <!-- ★ 人工介入中断横幅（数据补充 / HITL 审批） -->
      <InterruptBanner
        v-if="interruptData"
        :interrupt-data="interruptData"
        :submitting="isResuming"
        @resume="handleResume"
      />

      <!-- 输入区域：中断激活时隐藏，正常/流式时显示 -->
      <InputArea
        v-if="!interruptData"
        @send="handleSend"
        @stop="handleStop"
        @toggle-tool-calls="showToolCalls = $event"
        :disabled="false"
        :streaming="isStreaming || isResuming"
        :show-tool-calls="showToolCalls"
      />
    </main>
  </div>
</template>

<script setup>
import { createToolCallbacks, settleToolCalls } from './utils/toolCalls.js'
import { ref, onMounted, onBeforeUnmount } from 'vue'
import Sidebar from './components/Sidebar.vue'
import ChatArea from './components/ChatArea.vue'
import InputArea from './components/InputArea.vue'
import InterruptBanner from './components/InterruptBanner.vue'
import { streamChat, resumeChat } from './api/chat.js'
import { getSessions, getMessages, deleteSession } from './api/history.js'

defineProps({ user: { type: Object, required: true }, loggingOut: Boolean })
defineEmits(['logout'])

/**
 * DeepAgent 聊天应用主组件
 *
 * 消息流按时间顺序混合展示：
 *   [user] → [assistant 文本] → [tool 调用] → [assistant 文本] → ...
 * 支持 Human-in-the-Loop 中断：
 *   ... → [interrupt] → [用户决策/补充] → [继续]
 */

// ============================================================
// 状态定义
// ============================================================

// 会话列表
const sessions = ref([])
// 当前会话 ID
const currentThreadId = ref(null)
// 消息列表（混合 user/assistant/tool，按时间顺序）
const messages = ref([])
// 是否正在流式输出
const isStreaming = ref(false)
// 是否正在处理中断恢复
const isResuming = ref(false)
// 是否显示工具调用
const showToolCalls = ref(true)
const mobileSidebarOpen = ref(false)
// 中断数据（null = 无中断，object = 有中断等待处理）
const interruptData = ref(null)
// AbortController 用于停止流式请求
let abortController = null

// 切换会话后，旧请求的迟到事件不能写入新会话。
function activeCallbacks(controller, callbacks) {
  return Object.fromEntries(Object.entries(callbacks).map(([name, callback]) => [
    name, (...args) => { if (abortController === controller) return callback(...args) }
  ]))
}

// ============================================================
// 生命周期
// ============================================================

/**
 * 组件挂载时加载会话列表
 */
onMounted(async () => {
  await loadSessions()
})
onBeforeUnmount(handleStop)

// ============================================================
// 会话管理
// ============================================================

/**
 * 加载会话列表
 */
async function loadSessions() {
  try {
    const response = await getSessions(1, 100)
    sessions.value = response.sessions || []
  } catch (error) {
    console.error('[App] 加载会话列表失败:', error)
    sessions.value = []
  }
}

/**
 * 选择会话
 */
async function handleSelectSession(threadId) {
  mobileSidebarOpen.value = false
  if (threadId === currentThreadId.value) return

  handleStop()
  currentThreadId.value = threadId
  interruptData.value = null  // 切换会话时清除中断状态
  await loadMessages(threadId)
}

/**
 * 新建对话
 */
function handleNewChat() {
  mobileSidebarOpen.value = false
  // 如果有正在进行的请求，取消它
  if (abortController) {
    abortController.abort()
    abortController = null
  }
  isStreaming.value = false
  isResuming.value = false
  interruptData.value = null
  currentThreadId.value = null
  messages.value = []
}

/**
 * 删除会话
 */
async function handleDeleteSession(threadId) {
  try {
    await deleteSession(threadId)
    await loadSessions()

    if (currentThreadId.value === threadId) {
      handleNewChat()
    }
  } catch (error) {
    console.error('[App] 删除会话失败:', error)
    alert('删除会话失败，请重试')
  }
}

/**
 * 加载会话消息
 */
async function loadMessages(threadId) {
  try {
    const response = await getMessages(threadId)
    messages.value = response.messages || []
  } catch (error) {
    console.error('[App] 加载会话消息失败:', error)
    messages.value = []
  }
}

// ============================================================
// 停止对话
// ============================================================

/**
 * 停止当前对话
 */
function handleStop() {
  settleToolCalls(messages.value, 'stopped')
  if (abortController) {
    abortController.abort()
    abortController = null
    isStreaming.value = false
    isResuming.value = false
    console.log('[App] 用户停止了对话')
  }
}

// ============================================================
// 消息发送
// ============================================================

/**
 * 发送消息
 */
async function handleSend(message) {
  if (isStreaming.value || isResuming.value) return

  // 添加用户消息
  messages.value.push({
    id: `user-${Date.now()}`,
    role: 'user',
    content: message
  })

  // 清除中断状态（如果有）
  interruptData.value = null

  // 设置流式状态
  isStreaming.value = true

  // 创建新的 AbortController
  const controller = new AbortController()
  abortController = controller

  try {
    const result = await streamChat(
      message,
      currentThreadId.value,
      activeCallbacks(controller, {
        // 接收到 token 时的回调
        onToken: (content, source) => {
          const lastMsg = messages.value[messages.value.length - 1]
          if (lastMsg && lastMsg.role === 'assistant' && lastMsg.source === (source || 'main')) {
            // 追加到已有的 assistant 消息
            lastMsg.content += content
            lastMsg.source = source || 'main'
          } else {
            // 前一条是 tool 或没有消息，创建新的 assistant 消息
            messages.value.push({
              id: `assistant-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
              role: 'assistant',
              content: content,
              source: source || 'main'
            })
          }
        },

        ...createToolCallbacks(() => messages.value),

        // ★ 中断检测回调
        onInterrupt: (data) => {
          console.log('[App] 检测到中断:', data.interrupt_type)
          // 审批尚未完成，保留待确认状态。
          settleToolCalls(messages.value, 'waiting')
          // 清理空的 assistant 消息
          messages.value = messages.value.filter(m => {
            if (m.role === 'assistant' && !m.content) return false
            return true
          })
          // 设置中断数据，触发 InterruptBanner 显示
          interruptData.value = data
          if (data.thread_id) currentThreadId.value = data.thread_id
        },

        // 流结束时的回调
        onDone: (data) => {
          // 未收到结果的调用不能标记为成功。
          settleToolCalls(messages.value, data.aborted ? 'stopped' : 'ended')
          // 清理空内容的 assistant 消息（流式过程中产生的残留）
          messages.value = messages.value.filter(m => {
            if (m.role === 'assistant' && !m.content && !data.aborted) {
              return false
            }
            return true
          })
          if (data.aborted) {
            const lastMsg = messages.value[messages.value.length - 1]
            if (lastMsg && lastMsg.role === 'assistant' && !lastMsg.content) {
              lastMsg.content = '（对话已停止）'
            }
          }
          // 仅在非中断完成时更新 thread_id
          if (data.thread_id && !currentThreadId.value && !data.interrupted) {
            currentThreadId.value = data.thread_id
            loadSessions()
          }
        },

        // 发生错误时的回调
        onError: (error) => {
          settleToolCalls(messages.value, 'ended')
          console.error('[App] 对话错误:', error)
          const lastMsg = messages.value[messages.value.length - 1]
          if (lastMsg && lastMsg.role === 'assistant' && !lastMsg.content) {
            lastMsg.content = `抱歉，发生了错误：${error.message}`
          } else {
            messages.value.push({
              id: `assistant-${Date.now()}`,
              role: 'assistant',
              content: `抱歉，发生了错误：${error.message}`,
              source: 'main'
            })
          }
        }
      }),
      controller.signal
    )
  } catch (error) {
    if (error.name === 'AbortError') {
      console.log('[App] 请求已被取消')
    } else {
      console.error('[App] 发送消息失败:', error)
    }
  } finally {
    if (abortController === controller) {
      isStreaming.value = false
      abortController = null
    }
  }
}

// ============================================================
// 中断恢复
// ============================================================

/**
 * 处理中断恢复（由 InterruptBanner 触发）
 *
 * @param {Object} resumeData - 恢复数据：
 *   - 数据补充: { supplement: "..." }
 *   - HITL 审批: { decisions: [{ type: "approve" }] }
 */
async function handleResume(resumeData) {
  if (isResuming.value || !currentThreadId.value) return

  isResuming.value = true

  // 创建新的 AbortController
  const controller = new AbortController()
  abortController = controller

  try {
    const result = await resumeChat(
      currentThreadId.value,
      resumeData,
      activeCallbacks(controller, {
        onToken: (content, source) => {
          const lastMsg = messages.value[messages.value.length - 1]
          if (lastMsg && lastMsg.role === 'assistant' && lastMsg.source === (source || 'main')) {
            lastMsg.content += content
            lastMsg.source = source || 'main'
          } else {
            messages.value.push({
              id: `assistant-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
              role: 'assistant',
              content: content,
              source: source || 'main'
            })
          }
        },

        ...createToolCallbacks(() => messages.value),

        // ★ 恢复后仍可能再次中断（如：先数据补充 → 再 HITL 审批）
        onInterrupt: (data) => {
          console.log('[App] 恢复后再次中断:', data.interrupt_type)
          settleToolCalls(messages.value, 'waiting')
          messages.value = messages.value.filter(m => {
            if (m.role === 'assistant' && !m.content) return false
            return true
          })
          interruptData.value = data
          if (data.thread_id) currentThreadId.value = data.thread_id
        },

        onDone: (data) => {
          settleToolCalls(messages.value, data.aborted ? 'stopped' : 'ended')
          messages.value = messages.value.filter(m => {
            if (m.role === 'assistant' && !m.content) return false
            return true
          })
          // 非中断完成 → 清除中断状态
          if (!data.interrupted) {
            interruptData.value = null
            // 更新 thread_id
            if (data.thread_id && !currentThreadId.value) {
              currentThreadId.value = data.thread_id
              loadSessions()
            }
          }
        },

        onError: (error) => {
          const inputRejected = error.code?.startsWith('input_')
          settleToolCalls(messages.value, inputRejected ? 'waiting' : 'ended')
          console.error('[App] 恢复对话错误:', error)
          // 输入检查失败时后端还未恢复执行，保留表单以便修改后重试。
          if (!inputRejected) interruptData.value = null
          const lastMsg = messages.value[messages.value.length - 1]
          if (lastMsg && lastMsg.role === 'assistant' && !lastMsg.content) {
            lastMsg.content = `抱歉，发生了错误：${error.message}`
          } else {
            messages.value.push({
              id: `assistant-${Date.now()}`,
              role: 'assistant',
              content: `抱歉，发生了错误：${error.message}`,
              source: 'main'
            })
          }
        }
      }),
      controller.signal
    )
  } catch (error) {
    if (error.name === 'AbortError') {
      console.log('[App] 恢复请求已被取消')
    } else {
      console.error('[App] 恢复请求失败:', error)
    }
  } finally {
    if (abortController === controller) {
      isResuming.value = false
      abortController = null
    }
  }
}
</script>

<style>
.app-container {
  background: #f6f2eb;
  gap: 18px;
  padding: 18px;
}
.main-content {
  background: #fbfaf7;
  border: 1px solid #ddd6ca;
  border-radius: 24px;
  overflow: hidden;
  box-shadow: 0 18px 40px rgba(38, 42, 39, .08);
}
@media (max-width: 760px) {
  .app-container { padding: 0; gap: 0; }
  .main-content { border-radius: 0; border: 0; }
}
</style>

<style>
/* 主题覆盖放在组件默认布局之后，确保自有工作台配色优先。 */
.app-container { background: #f6f2eb; gap: 18px; padding: 18px; }
.main-content { background: #fbfaf7; border: 1px solid #ddd6ca; border-radius: 24px; overflow: hidden; box-shadow: 0 18px 40px rgba(38,42,39,.08); }
.mobile-toolbar, .sidebar-backdrop { display: none; }
@media (max-width: 760px) { .app-container { padding: 0; gap: 0; } .main-content { border-radius: 0; border: 0; } }
@media (max-width: 760px) {
  .app-container { height: 100dvh; min-height: 0; flex-direction: column; }
  .mobile-toolbar { display: flex; flex-shrink: 0; align-items: center; justify-content: space-between; gap: 12px; padding: 12px; background: #203a4b; color: #f8f4eb; }
  .mobile-toolbar strong { font-size: 14px; }
  .mobile-toolbar button { border: 1px solid #587080; border-radius: 7px; padding: 7px 9px; color: #f8f4eb; background: transparent; font-size: 12px; cursor: pointer; }
  .app-container > .sidebar { position: fixed; inset: 0 auto 0 0; width: min(310px, 86vw); height: 100dvh; z-index: 51; border-radius: 0; transform: translateX(-110%); transition: transform .2s; visibility: hidden; }
  .app-container > .sidebar.mobile-open { transform: translateX(0); visibility: visible; }
  .sidebar-backdrop { display: block; position: fixed; inset: 0; z-index: 50; background: #10283380; border: 0; }
  .app-container > .main-content { flex: 1; width: 100%; min-width: 0; min-height: 0; }
}
</style>

<style scoped>
.app-container {
  display: flex;
  height: 100vh;
  background: #f8fafc;
  position: relative;
  overflow: hidden;
}

.main-content {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  width: 100%;
  background: #ffffff;
  position: relative;
  border-left: 1px solid #e2e8f0;
  box-shadow: 0 0 20px rgba(0, 0, 0, 0.05);
}
</style>

<style>
.app-container { background: #f6f2eb; gap: 18px; padding: 18px; }
.main-content { background: #fbfaf7; border: 1px solid #ddd6ca; border-radius: 24px; overflow: hidden; box-shadow: 0 18px 40px rgba(38,42,39,.08); }
@media (max-width: 760px) { .app-container { padding: 0; gap: 0; } .main-content { border-radius: 0; border: 0; } }
</style>
