<template>
  <div class="app-container" :class="{ 'is-resizing': isResizing }" :style="sidebarStyle" @keydown="handleLayoutKeydown">
    <button v-if="isMobile && mobileSidebarOpen" class="sidebar-backdrop" aria-label="关闭会话列表" @click="closeMobileSidebar"></button>
    <!-- 侧边栏 -->
    <div
      v-show="isMobile || !sidebarCollapsed"
      id="session-sidebar"
      ref="sidebarShellRef"
      class="sidebar-shell"
      :class="{ 'mobile-open': mobileSidebarOpen }"
      :inert="isMobile && !mobileSidebarOpen"
    >
      <Sidebar
      :mobile="isMobile"
      :sessions="displaySessions"
      :projects="projects"
      :current-thread-id="currentThreadId"
      :pending-project-id="pendingProjectId"
      :current-project-id="activeProject?.project_id || null"
      :username="user.username"
      :logging-out="loggingOut"
      @toggle-sidebar="toggleSidebar"
      @logout="$emit('logout')"
      @select-session="handleSelectSession"
      @new-chat="handleNewChat"
      @open-project="handleOpenProject"
      @create-project="handleCreateProject"
      @update-project="handleUpdateProject"
      @delete-project="handleDeleteProject"
      @update-session="handleUpdateSession"
      @delete-session="handleDeleteSession"
      />
      <div
        v-if="!isMobile"
        class="sidebar-resizer"
        role="separator"
        tabindex="0"
        aria-label="调整会话侧栏宽度"
        aria-orientation="vertical"
        aria-controls="session-sidebar"
        :aria-valuemin="MIN_SIDEBAR_WIDTH"
        :aria-valuemax="maxSidebarWidth"
        :aria-valuenow="renderedSidebarWidth"
        title="拖动调整宽度，双击恢复默认"
        @pointerdown="startSidebarResize"
        @pointermove="resizeSidebar"
        @pointerup="finishSidebarResize"
        @pointercancel="finishSidebarResize"
        @lostpointercapture="finishSidebarResize"
        @keydown="resizeSidebarWithKeyboard"
        @dblclick="resetSidebarWidth"
      ></div>
    </div>

    <!-- 主内容区 -->
    <main class="main-content">
      <header class="workspace-header">
        <button
          v-if="isMobile || sidebarCollapsed"
          ref="sidebarToggleRef"
          class="workspace-icon-button"
          type="button"
          aria-label="展开会话侧栏"
          title="展开会话侧栏"
          :aria-expanded="isMobile ? mobileSidebarOpen : !sidebarCollapsed"
          aria-controls="session-sidebar"
          @click="toggleSidebar"
        ><svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="3"/><path d="M9 4v16M6 8h.01M6 11h.01"/></svg></button>
        <div class="workspace-title"><span v-if="activeProject" class="workspace-project">{{ activeProject.name }}<span aria-hidden="true">/</span></span><strong :title="activeSession?.title || '新会话'">{{ activeSession?.title || '新会话' }}</strong></div>
        <button class="workspace-icon-button header-new-chat" type="button" aria-label="新建会话" title="新建会话" @click="handleNewChat"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M13 5H6a2 2 0 0 0-2 2v11a2 2 0 0 0 2 2h11a2 2 0 0 0 2-2v-7M16 3h5v5M11 13 21 3"/></svg></button>
        <button class="evidence-button" type="button" @click="openEvidence">决策证据</button>
      </header>
      <!-- 对话区域 -->
      <ChatArea
        :key="currentConversation.key"
        :messages="messages"
        :streaming="isStreaming || isResuming"
        :processing-status="processingStatus"
        :show-tool-calls="showToolCalls"
        :project-name="activeProject?.name || ''"
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
        ref="inputAreaRef"
        v-if="!interruptData"
        @send="handleSend"
        @stop="handleStop"
        @toggle-tool-calls="showToolCalls = $event"
        :key="currentConversation.key"
        v-model="currentConversation.draft"
        :disabled="currentConversation.loading"
        :streaming="isStreaming || isResuming"
        :show-tool-calls="showToolCalls"
        :project-name="activeProject?.name || ''"
      />
    </main>
    <DecisionEvidencePanel v-if="evidenceDecisionId" :decision-id="evidenceDecisionId" @close="evidenceDecisionId = ''" />
  </div>
</template>

<script setup>
import { createConversations } from './utils/conversations.js'
import { computed, nextTick, ref, onMounted, onBeforeUnmount } from 'vue'
import Sidebar from './components/Sidebar.vue'
import ChatArea from './components/ChatArea.vue'
import InputArea from './components/InputArea.vue'
import InterruptBanner from './components/InterruptBanner.vue'
import DecisionEvidencePanel from './components/DecisionEvidencePanel.vue'
import { streamChat, resumeChat } from './api/chat.js'
import {
  getSessions, getMessages, deleteSession, updateSession, getProjects, createProject, updateProject, deleteProject
} from './api/history.js'

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
const projects = ref([])
const pendingProjectId = ref(null)
const inputAreaRef = ref(null)
const evidenceDecisionId = ref('')
function openEvidence() {
  const decisionId = window.prompt('输入要查看的决策 ID')?.trim()
  if (decisionId) evidenceDecisionId.value = decisionId
}
// 每个会话持有自己的消息、请求、审批和输入草稿。
const conversationStore = createConversations({ streamChat, resumeChat, getMessages, onSessionsChanged: loadSessions })
const currentConversation = conversationStore.current
const currentThreadId = computed(() => currentConversation.value.threadId || (currentConversation.value.messages.length ? currentConversation.value.key : null))
const messages = computed(() => currentConversation.value.messages)
const isStreaming = computed(() => currentConversation.value.isStreaming)
const isResuming = computed(() => currentConversation.value.isResuming)
const processingStatus = computed(() => currentConversation.value.processingStatus)
const interruptData = computed(() => currentConversation.value.interruptData)
const activeSession = computed(() => displaySessions.value.find(session => session.thread_id === currentThreadId.value))
const activeProject = computed(() => projects.value.find(project => project.project_id === (
  currentConversation.value.projectId || activeSession.value?.project_id
)) || null)
const displaySessions = computed(() => {
  const rows = sessions.value.map(session => ({ ...session }))
  for (const state of conversationStore.conversations.values()) {
    if (!state.messages.length) continue
    const threadId = state.threadId || state.key
    let row = rows.find(session => session.thread_id === threadId)
    if (!row) {
      row = { thread_id: threadId, title: state.messages.find(message => message.role === 'user')?.content.slice(0, 50) || '新会话',
        project_id: state.projectId, updated_at: state.updatedAt, pinned: false }
      rows.unshift(row)
    }
    row.runStatus = state.status
    row.running = state.isStreaming || state.isResuming
    row.unread = state.unread
  }
  return rows
})
// 是否显示工具调用
const showToolCalls = ref(true)
const mobileSidebarOpen = ref(false)
const sidebarShellRef = ref(null)
const sidebarToggleRef = ref(null)
const MIN_SIDEBAR_WIDTH = 240
const DEFAULT_SIDEBAR_WIDTH = 288
const layoutStorageKey = 'prism.workspace.layout'
const viewportWidth = ref(window.innerWidth)
const isMobile = computed(() => viewportWidth.value <= 760)
const maxSidebarWidth = computed(() => Math.max(MIN_SIDEBAR_WIDTH, Math.min(440, viewportWidth.value - 440)))
const sidebarWidth = ref(DEFAULT_SIDEBAR_WIDTH)
const sidebarCollapsed = ref(false)
const isResizing = ref(false)
const renderedSidebarWidth = computed(() => Math.min(maxSidebarWidth.value, Math.max(MIN_SIDEBAR_WIDTH, sidebarWidth.value)))
const sidebarStyle = computed(() => ({ '--sidebar-width': `${renderedSidebarWidth.value}px` }))
let resizeGesture = null

try {
  const saved = JSON.parse(localStorage.getItem(layoutStorageKey) || '{}')
  if (Number.isFinite(saved?.width)) sidebarWidth.value = Math.min(440, Math.max(MIN_SIDEBAR_WIDTH, saved.width))
  sidebarCollapsed.value = saved?.collapsed === true
} catch { /* 浏览器不允许存储时，侧栏仍然可用。 */ }

function saveSidebarLayout() {
  try { localStorage.setItem(layoutStorageKey, JSON.stringify({ width: sidebarWidth.value, collapsed: sidebarCollapsed.value })) } catch { /* 存储不可用不影响布局。 */ }
}
function toggleSidebar() {
  if (isMobile.value) {
    if (mobileSidebarOpen.value) { closeMobileSidebar(); return }
    mobileSidebarOpen.value = true
    nextTick(() => sidebarShellRef.value?.querySelector('.sidebar-toggle')?.focus())
    return
  }
  sidebarCollapsed.value = !sidebarCollapsed.value
  saveSidebarLayout()
  nextTick(() => {
    if (sidebarCollapsed.value) sidebarToggleRef.value?.focus()
    else sidebarShellRef.value?.querySelector('.sidebar-toggle')?.focus()
  })
}
function closeMobileSidebar() {
  mobileSidebarOpen.value = false
  nextTick(() => sidebarToggleRef.value?.focus())
}
function handleLayoutKeydown(event) {
  if (!isMobile.value || !mobileSidebarOpen.value) return
  if (event.key === 'Escape') { event.preventDefault(); closeMobileSidebar(); return }
  if (event.key !== 'Tab') return
  const controls = [...sidebarShellRef.value.querySelectorAll('button:not(:disabled), input, [tabindex="0"], summary')].filter(element => element.getClientRects().length)
  const first = controls[0], last = controls.at(-1)
  if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus() }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus() }
}
function startSidebarResize(event) {
  if (event.button !== 0 || isMobile.value) return
  event.preventDefault()
  event.currentTarget.focus()
  resizeGesture = { x: event.clientX, width: renderedSidebarWidth.value, target: event.currentTarget, pointerId: event.pointerId }
  event.currentTarget.setPointerCapture(event.pointerId)
  isResizing.value = true
  document.documentElement.classList.add('resizing-sidebar')
}
function resizeSidebar(event) {
  if (!resizeGesture || event.pointerId !== resizeGesture.pointerId) return
  sidebarWidth.value = Math.round(Math.min(maxSidebarWidth.value, Math.max(MIN_SIDEBAR_WIDTH, resizeGesture.width + event.clientX - resizeGesture.x)))
}
function finishSidebarResize() {
  if (!resizeGesture) return
  const { target, pointerId } = resizeGesture
  resizeGesture = null
  isResizing.value = false
  document.documentElement.classList.remove('resizing-sidebar')
  if (target.hasPointerCapture(pointerId)) target.releasePointerCapture(pointerId)
  saveSidebarLayout()
}
function resizeSidebarWithKeyboard(event) {
  const widths = { ArrowLeft: renderedSidebarWidth.value - 16, ArrowRight: renderedSidebarWidth.value + 16, Home: MIN_SIDEBAR_WIDTH, End: maxSidebarWidth.value }
  if (!(event.key in widths)) return
  event.preventDefault()
  sidebarWidth.value = Math.min(maxSidebarWidth.value, Math.max(MIN_SIDEBAR_WIDTH, widths[event.key]))
  saveSidebarLayout()
}
function resetSidebarWidth() {
  sidebarWidth.value = Math.min(DEFAULT_SIDEBAR_WIDTH, maxSidebarWidth.value)
  saveSidebarLayout()
}
function handleViewportResize() {
  finishSidebarResize()
  viewportWidth.value = window.innerWidth
  mobileSidebarOpen.value = false
}
// ============================================================
// 生命周期
// ============================================================

/**
 * 组件挂载时加载会话列表
 */
onMounted(async () => {
  window.addEventListener('resize', handleViewportResize)
  await loadSessions()
})
onBeforeUnmount(conversationStore.dispose)
onBeforeUnmount(() => {
  window.removeEventListener('resize', handleViewportResize)
  finishSidebarResize()
})

// ============================================================
// 会话管理
// ============================================================

/**
 * 加载会话列表
 */
let sessionLoadVersion = 0
async function loadSessions() {
  const version = ++sessionLoadVersion
  try {
    const [sessionResponse, projectResponse] = await Promise.all([
      getSessions(1, 100), getProjects()
    ])
    if (version !== sessionLoadVersion) return
    sessions.value = sessionResponse.sessions || []
    projects.value = projectResponse.projects || []
  } catch (error) {
    console.error('[App] 加载会话列表失败:', error)
  }
}

/**
 * 选择会话
 */
async function handleSelectSession(threadId) {
  mobileSidebarOpen.value = false
  pendingProjectId.value = null
  const row = displaySessions.value.find(session => session.thread_id === threadId)
  const selecting = conversationStore.select(threadId)
  if (row) currentConversation.value.projectId = row.project_id
  await selecting
}

function handleNewChat(projectId = null) {
  mobileSidebarOpen.value = false
  pendingProjectId.value = typeof projectId === 'string' ? projectId : null
  conversationStore.newChat(pendingProjectId.value)
  nextTick(() => inputAreaRef.value?.focus())
}

function handleOpenProject(projectId) {
  handleNewChat(projectId)
}

async function handleCreateProject(name) {
  try {
    const project = await createProject(name)
    await loadSessions()
    handleNewChat(project.project_id)
  } catch (error) {
    console.error('[App] 创建项目失败:', error)
    alert(error.message || '创建项目失败，请重试')
  }
}

async function handleUpdateProject({ projectId, changes }) {
  try {
    await updateProject(projectId, changes)
    await loadSessions()
  } catch (error) {
    console.error('[App] 更新项目失败:', error)
    alert(error.message || '更新项目失败，请重试')
  }
}

async function handleDeleteProject(projectId) {
  try {
    await deleteProject(projectId)
    for (const state of conversationStore.conversations.values()) {
      if (state.projectId === projectId) state.projectId = null
    }
    if (pendingProjectId.value === projectId) pendingProjectId.value = null
    await loadSessions()
  } catch (error) {
    console.error('[App] 删除项目失败:', error)
    alert(error.message || '删除项目失败，请重试')
  }
}

async function handleUpdateSession({ threadId, changes }) {
  try {
    await updateSession(threadId, changes)
    const state = conversationStore.find(threadId)
    if (state && 'project_id' in changes) state.projectId = changes.project_id
    await loadSessions()
  } catch (error) {
    console.error('[App] 更新会话失败:', error)
    alert(error.message || '更新会话失败，请重试')
  }
}

/**
 * 删除会话
 */
async function handleDeleteSession(threadId) {
  try {
    await deleteSession(threadId)
    conversationStore.remove(threadId)
    await loadSessions()

    if (currentThreadId.value === threadId) {
      handleNewChat()
    }
  } catch (error) {
    console.error('[App] 删除会话失败:', error)
    alert('删除会话失败，请重试')
  }
}

// 切换页面只更改选择；停止按钮只取消当前会话。
function handleStop() { conversationStore.stop() }
function handleSend(message) { return conversationStore.send(message) }
function handleResume(data) { return conversationStore.resume(data) }
</script>

<style scoped>
.app-container {
  --workspace-surface: var(--color-bg);
  --conversation-width: 960px;
  --conversation-gutter: clamp(20px, 4vw, 56px);
  display: flex;
  height: 100vh;
  height: 100dvh;
  min-width: 0;
  position: relative;
  overflow: hidden;
  background: var(--workspace-surface);
}
.sidebar-shell {
  position: relative;
  z-index: 20;
  flex: 0 0 var(--sidebar-width);
  width: var(--sidebar-width);
  min-width: 0;
  height: 100%;
  border-right: 1px solid var(--nav-border);
}
.sidebar-resizer {
  position: absolute;
  z-index: 25;
  top: 0;
  right: -5px;
  bottom: 0;
  width: 9px;
  cursor: col-resize;
  touch-action: none;
}
.sidebar-resizer::after {
  content: '';
  position: absolute;
  inset: 0 3px;
  background: transparent;
  transition: background .15s;
}
.sidebar-resizer:hover::after,
.sidebar-resizer:focus-visible::after,
.is-resizing .sidebar-resizer::after { background: var(--color-primary); }
.sidebar-resizer:focus-visible { outline: none; }
.main-content {
  flex: 1;
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  position: relative;
  overflow: hidden;
  background: var(--workspace-surface);
}
.workspace-header {
  display: flex;
  flex-shrink: 0;
  align-items: center;
  gap: 14px;
  height: 60px;
  padding: 0 24px;
  color: var(--color-text);
  background: var(--color-bg-secondary);
  border-bottom: 1px solid var(--color-border-light);
}
.workspace-title { display: flex; align-items: center; gap: 10px; min-width: 0; }
.workspace-title > strong { overflow: hidden; font-size: 14px; font-weight: 550; text-overflow: ellipsis; white-space: nowrap; }
.workspace-project { display: flex; align-items: center; gap: 10px; color: var(--color-text-secondary); font-size: 13px; white-space: nowrap; max-width: 180px; overflow: hidden; text-overflow: ellipsis; }
.workspace-project > span { color: var(--color-text-muted); }
.workspace-icon-button { display: grid; place-items: center; flex: 0 0 34px; width: 34px; height: 34px; padding: 7px; background: transparent; color: var(--color-text-secondary); border: 0; border-radius: 8px; cursor: pointer; }
.workspace-icon-button:hover { color: var(--color-primary); background: var(--color-primary-soft); }
.workspace-icon-button:focus-visible { outline: 2px solid var(--color-primary); outline-offset: 2px; }
.workspace-icon-button svg { width: 20px; height: 20px; fill: none; stroke: currentColor; stroke-width: 1.7; stroke-linecap: round; stroke-linejoin: round; }
.header-new-chat { margin-left: auto; }
.evidence-button { border: 1px solid var(--color-primary-line); border-radius: 8px; background: var(--color-bg-secondary); color: var(--color-primary); padding: 7px 10px; cursor: pointer; font-size: 12px; }
.sidebar-backdrop { display: none; }
:global(html.resizing-sidebar), :global(html.resizing-sidebar *) { cursor: col-resize !important; user-select: none !important; }
@media (max-width: 760px) {
  .app-container { --conversation-gutter: 18px; }
  .sidebar-shell { position: fixed; inset: 0 auto 0 0; width: min(310px, 88vw); height: 100dvh; z-index: 51; transform: translateX(-105%); visibility: hidden; transition: transform .2s, visibility .2s; box-shadow: 8px 0 40px #20251e26; }
  .sidebar-shell.mobile-open { transform: translateX(0); visibility: visible; }
  .sidebar-backdrop { display: block; position: fixed; inset: 0; z-index: 50; background: #20251e55; border: 0; }
  .workspace-header { height: 56px; padding: 0 12px; gap: 9px; }
  .workspace-project { display: none; }
  .workspace-title > strong { font-size: 13px; }
}
@media (prefers-reduced-motion: reduce) {
  .sidebar-shell, .sidebar-resizer::after { transition: none; }
}
</style>
