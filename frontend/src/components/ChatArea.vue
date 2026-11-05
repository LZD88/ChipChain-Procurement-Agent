<template>
  <div class="chat-area">
    <!-- 消息列表 -->
    <div class="message-list" ref="messageListRef" @scroll="handleScroll" @load.capture="scrollToBottom">
      <!-- 空状态 -->
      <div v-if="displayMessages.length === 0" class="empty-state">
        <div class="empty-icon" aria-hidden="true"><span class="node-mark"><i></i><i></i><i></i><i></i></span></div>
        <div class="eyebrow">ERP OPERATIONS DESK</div>
        <h2>把复杂工作，交给一个清晰的工作台</h2>
        <p>采购、库存与供应商信息，都可以在这里被整理成下一步行动。</p>
        <div class="feature-list">
          <div class="feature-item">
            <span class="feature-icon">01</span>
            <span>数据分析和图表生成</span>
          </div>
          <div class="feature-item">
            <span class="feature-icon">02</span>
            <span>网络搜索和信息查询</span>
          </div>
          <div class="feature-item">
            <span class="feature-icon">03</span>
            <span>技能skill安装与扩展</span>
          </div>
          <div class="feature-item">
            <span class="feature-icon">04</span>
            <span>文档处理和写作</span>
          </div>
        </div>
      </div>

      <!-- 每轮仅显示一个用户问题和一个 AI 回复区块。 -->
      <div v-else class="messages">
        <template v-for="(message, index) in displayMessages" :key="message.id || index">
          <MessageItem
            :message="message"
            :is-streaming="isStreamingForMessage(message)"
          />
        </template>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch, nextTick } from 'vue'
import MessageItem from './MessageItem.vue'
import { groupReplyTurns } from '../utils/toolCalls.js'

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
  showToolCalls: {
    type: Boolean,
    default: true
  }
})

const displayMessages = computed(() => groupReplyTurns(props.messages, props.showToolCalls, props.streaming))

// 消息列表引用
const messageListRef = ref(null)
const followLatest = ref(true)
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
}

.feature-item:hover {
  transform: translateY(-2px);
  border-color: #0ea5e9;
  box-shadow: 0 4px 12px rgba(14, 165, 233, 0.1);
}

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
.chat-area { background: #fbfaf7; }
.message-list { padding: 34px clamp(18px, 5vw, 72px); }
.empty-state { background: radial-gradient(circle at 50% 12%, #fffaf0 0, #fbfaf7 42%, #f6f2eb 100%); }
.empty-icon { width: 94px; height: 94px; margin-bottom: 22px; display: grid; place-items: center; border-radius: 28px; background: #203a4b; box-shadow: 10px 12px 0 #e8d6bb; }
.empty-icon img { display: none; }
.node-mark { width: 46px; height: 46px; display: grid; grid-template-columns: repeat(2, 1fr); gap: 7px; padding: 8px; transform: rotate(45deg); }
.node-mark i { border: 3px solid #f5c76b; border-radius: 50%; }
.eyebrow { color: #ee6c4d; font-size: 11px; font-weight: 700; letter-spacing: .2em; }
.empty-state h2 { max-width: 620px; margin: 12px 0 10px; color: #203a4b; background: none; -webkit-text-fill-color: initial; font-size: clamp(25px, 4vw, 42px); line-height: 1.18; letter-spacing: -.04em; }
.empty-state p { max-width: 500px; margin-bottom: 34px; color: #718087; }
.feature-list { gap: 12px; max-width: 640px; }
.feature-item { min-width: 240px; padding: 15px 16px; background: rgba(255,255,255,.72); border: 1px solid #e1d9cd; border-radius: 14px; box-shadow: none; }
.feature-item:hover { border-color: #ee6c4d; box-shadow: 0 10px 24px rgba(238,108,77,.1); }
.feature-icon { color: #ee6c4d; font-size: 11px; font-weight: 800; letter-spacing: .1em; }
.feature-item span:last-child { color: #334850; }
</style>
