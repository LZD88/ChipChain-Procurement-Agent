<template>
  <div class="chat-area">
    <!-- 消息列表 -->
    <div class="message-list" ref="messageListRef" @scroll="handleScroll" @load.capture="scrollToBottom">
      <!-- 空状态 -->
      <div v-if="displayMessages.length === 0" class="empty-state">
        <div class="empty-icon" aria-hidden="true"><span class="node-mark"><i></i><i></i><i></i><i></i></span></div>
        <div class="eyebrow">{{ projectName ? 'PROJECT WORKSPACE' : 'ERP OPERATIONS DESK' }}</div>
        <h2>{{ projectName ? `我们在“${projectName}”中处理什么？` : '把复杂工作，交给一个清晰的工作台' }}</h2>
        <p>{{ projectName ? '接下来的新会话会自动归入这个项目，方便持续推进同一采购主题。' : '采购、库存与供应商信息，都可以在这里被整理成下一步行动。' }}</p>
        <div class="feature-list">
          <button type="button" class="feature-item featured" @click="emit('suggest', '帮我采购500片六轴惯性传感器，预算4000元，要求两周内交付。先核验库存和供应商，给出方案后创建采购单。')">
            <span class="feature-icon">01</span>
            <span>多 Agent 采购闭环</span>
          </button>
          <button type="button" class="feature-item" @click="emit('suggest', '对比六轴惯性传感器的供应商、采购价、交期和信用评级，给出推荐方案。')">
            <span class="feature-icon">02</span>
            <span>供应商比价与风险核验</span>
          </button>
          <button type="button" class="feature-item" @click="emit('suggest', '检查当前库存预警，并说明哪些物料需要优先补货。')">
            <span class="feature-icon">03</span>
            <span>库存预警与补货建议</span>
          </button>
          <button type="button" class="feature-item" @click="emit('suggest', '查询最近的采购订单并总结当前状态。')">
            <span class="feature-icon">04</span>
            <span>订单查询与审计追踪</span>
          </button>
        </div>
      </div>

      <!-- 每轮仅显示一个用户问题和一个 AI 回复区块。 -->
      <div v-else class="messages">
        <template v-for="(message, index) in displayMessages" :key="message.id || index">
          <MessageItem
            :message="message"
            :is-streaming="isStreamingForMessage(message)"
            :processing-status="processingStatus"
          />
        </template>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch, nextTick, onMounted, onBeforeUnmount } from 'vue'
import MessageItem from './MessageItem.vue'
import { groupReplyTurns } from '../utils/toolCalls.js'

const emit = defineEmits(['suggest'])

/**
 * 对话区域组件
 *
 * 每轮显示一个答复，过程说明和工具调用在答复区块内按时间排列。
 * 关闭 showToolCalls 时隐藏过程，仍保留正文和图表。
 */

const props = defineProps({
  messages: {
    type: Array,
    default: () => []
  },
  streaming: {
    type: Boolean,
    default: false
  },
  processingStatus: {
    type: String,
    default: '正在处理采购任务…'
  },
  showToolCalls: {
    type: Boolean,
    default: true
  },
  projectName: {
    type: String,
    default: ''
  }
})

const displayMessages = computed(() => groupReplyTurns(props.messages, props.showToolCalls, props.streaming))

// 消息列表引用
const messageListRef = ref(null)
const followLatest = ref(true)
let listResizeObserver
onMounted(() => {
  listResizeObserver = new ResizeObserver(() => scrollToBottom())
  if (messageListRef.value) listResizeObserver.observe(messageListRef.value)
})
onBeforeUnmount(() => listResizeObserver?.disconnect())
function handleScroll() {
  const list = messageListRef.value
  if (list) followLatest.value = list.scrollHeight - list.scrollTop - list.clientHeight < 100
}

/**
 * 判断当前消息是否处于流式输出状态
 * 只有最后一条 assistant 消息在 streaming 时才显示光标
 */
function isStreamingForMessage(msg) {
  if (!props.streaming || msg.role !== 'assistant') return false
  return displayMessages.value.at(-1)?.id === msg.id
}

// 监听消息变化，自动滚动到底部
watch(
  () => props.messages.length,
  () => {
    nextTick(() => {
      scrollToBottom()
    })
  }
)

// 深度监听消息内容变化（流式更新时也会触发滚动）
watch(
  () => props.messages,
  () => {
    nextTick(() => {
      scrollToBottom()
    })
  },
  { deep: true }
)

/**
 * 滚动到底部
 */
function scrollToBottom() {
  if (messageListRef.value && followLatest.value) {
    messageListRef.value.scrollTop = messageListRef.value.scrollHeight
  }
}
</script>

<style scoped>
.chat-area {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background: #ffffff;
}

/* 消息列表 */
.message-list {
  flex: 1;
  overflow-y: auto;
  padding: 24px 0;
}

.message-list::-webkit-scrollbar {
  width: 6px;
}

.message-list::-webkit-scrollbar-track {
  background: transparent;
}

.message-list::-webkit-scrollbar-thumb {
  background: #cbd5e1;
  border-radius: 3px;
}

.message-list::-webkit-scrollbar-thumb:hover {
  background: #94a3b8;
}

/* 空状态 */
.empty-state {
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 40px 24px;
  text-align: center;
  background: linear-gradient(180deg, #f8fafc 0%, #ffffff 100%);
}

.empty-icon {
  margin-bottom: 24px;
  animation: float 3s ease-in-out infinite;
}

.empty-icon img {
  width: 240px;
  height: 240px;
}

@keyframes float {
  0%, 100% { transform: translateY(0); }
  50% { transform: translateY(-8px); }
}

.empty-state h2 {
  font-size: 28px;
  font-weight: 700;
  background: linear-gradient(135deg, #0ea5e9 0%, #6366f1 100%);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
  margin-bottom: 12px;
}

.empty-state p {
  font-size: 15px;
  color: #64748b;
  margin-bottom: 40px;
}

.feature-list {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 16px;
  max-width: 540px;
}

.feature-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 18px 20px;
  background: #ffffff;
  border: 1px solid #e2e8f0;
  border-radius: 14px;
  transition: all 0.2s ease;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.04);
  cursor: pointer;
  text-align: left;
  font: inherit;
}

.feature-item:hover {
  transform: translateY(-2px);
  border-color: #0ea5e9;
  box-shadow: 0 4px 12px rgba(14, 165, 233, 0.1);
}
.feature-item.featured { border-color: var(--color-primary); background: var(--color-primary-soft); }

.feature-icon {
  font-size: 24px;
}

.feature-item span:last-child {
  font-size: 14px;
  color: #1e293b;
  font-weight: 500;
}

/* 消息列表 */
.messages {
  max-width: 1200px;
  margin: 0 auto;
}
</style>

<style scoped>
.chat-area { min-height: 0; background: var(--workspace-surface); }
.message-list { min-height: 0; padding: 24px calc(var(--conversation-gutter) - 6px) 20px; scrollbar-gutter: stable both-edges; }
.messages { width: 100%; max-width: var(--conversation-width); margin: 0 auto; }
.empty-state { height: auto; min-height: 100%; padding: 28px 0; background: transparent; }
.empty-icon { width: 94px; height: 94px; margin-bottom: 22px; display: grid; place-items: center; border-radius: 28px; background: var(--color-text); box-shadow: 10px 12px 0 var(--color-primary-line); }
.empty-icon img { display: none; }
.node-mark { width: 46px; height: 46px; display: grid; grid-template-columns: repeat(2, 1fr); gap: 7px; padding: 8px; transform: rotate(45deg); }
.node-mark i { border: 3px solid var(--nav-accent); border-radius: 50%; }
.eyebrow { color: var(--color-primary); font-size: 11px; font-weight: 700; letter-spacing: .2em; }
.empty-state h2 { width: 100%; max-width: 900px; margin: 12px 0 10px; color: var(--color-text); background: none; -webkit-text-fill-color: initial; font-size: clamp(25px, 3vw, 42px); line-height: 1.25; letter-spacing: -.04em; text-wrap: balance; }
.empty-state p { max-width: 500px; margin-bottom: 34px; color: var(--color-text-muted); }
.feature-list { width: 100%; gap: 12px; max-width: 492px; grid-template-columns: repeat(2, minmax(0, 1fr)); }
.feature-item { min-width: 0; padding: 15px 16px; background: rgba(255,255,255,.72); border: 1px solid var(--color-border-light); border-radius: 14px; box-shadow: none; }
.feature-item:hover { border-color: var(--color-primary); box-shadow: 0 10px 24px rgba(22,134,109,.1); }
.feature-icon { color: var(--color-primary); font-size: 11px; font-weight: 800; letter-spacing: .1em; }
.feature-item span:last-child { color: var(--color-text-secondary); }
@media (max-width: 540px) {
  .feature-list { grid-template-columns: minmax(0, 1fr); max-width: 320px; }
}
@media (max-width: 760px) {
  .message-list { padding: 16px 12px 20px; scrollbar-gutter: auto; }
  .empty-state h2 { font-size: 28px; }
}
</style>
