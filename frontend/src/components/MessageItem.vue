<template>
  <!-- ============================================================ -->
  <!-- 用户消息 - 右侧暖色气泡 -->
  <!-- ============================================================ -->
  <div v-if="message.role === 'user'" class="message-item message-user">
    <div class="message-content-wrapper">
      <div class="message-content">
        <div class="content-text">{{ message.content }}</div>
      </div>
    </div>
    <div class="message-avatar">
      <div class="avatar user-avatar">👤</div>
    </div>
  </div>

  <!-- ============================================================ -->
  <!-- AI 助手消息 - 左侧，带头部标签栏 -->
  <!-- ============================================================ -->
  <div v-else-if="message.role === 'assistant'" class="message-item message-assistant" :class="{ streaming: isStreaming }">
    <div class="message-avatar">
      <div class="avatar assistant-avatar" :class="getAvatarClass(message.source)">
        {{ getRoleIcon(message.source) }}
      </div>
    </div>
    <div class="message-content-wrapper">
      <!-- 角色标签栏 -->
      <div class="msg-label-bar" :class="getLabelBarClass(message.source)">
        <span class="msg-role-badge">芯链AI</span>
        <span v-if="message.source && message.source !== 'main'" class="msg-source-badge">
          {{ getSourceName(message.source) }}
        </span>
      </div>
      <details v-if="message.steps?.length" class="response-process">
        <summary>执行过程<span v-if="message.toolCount"> · {{ message.toolCount }} 次调用</span><span v-if="isStreaming"> · 处理中</span></summary>
        <template v-for="(step, index) in message.steps" :key="step.id || index">
          <ToolCallPanel v-if="step.role === 'tool-group'" :tools="step.tools" :streaming="isStreaming" hide-images />
          <div v-else-if="step.content" class="process-note">
            <span>{{ getSourceName(step.source) }}</span>
            <MarkdownRenderer :content="step.content" />
          </div>
        </template>
      </details>
      <!-- 消息正文 -->
      <div class="message-content">
        <MarkdownRenderer :content="message.content" />
        <p v-if="!message.content" class="reply-status" :class="{ active: isStreaming }" role="status">{{ isStreaming ? processingStatus : '本轮暂无文字答复，可展开查看执行过程。' }}</p>
        <span v-if="isStreaming && message.content" class="typing-cursor">▋</span>
        <ChartPreview v-for="image in message.images || []" :key="image" :source="image" title="分析图表" />
      </div>
    </div>
  </div>

</template>

<script setup>
import MarkdownRenderer from './MarkdownRenderer.vue'
import ToolCallPanel from './ToolCallPanel.vue'
import ChartPreview from './ChartPreview.vue'
import { getSourceName } from '../utils/messageSources.js'

defineProps({
  message: { type: Object, required: true },
  isStreaming: { type: Boolean, default: false },
  processingStatus: { type: String, default: '正在处理采购任务…' }
})

// ============================================================
// AI 消息标签
// ============================================================

/** AI 消息的头像 emoji */
function getRoleIcon(source) {
  if (!source || source === 'main') return '🤖'
  const name = (source || '').toLowerCase()
  if (name.includes('chart')) return '📊'
  if (name.includes('research')) return '📚'
  if (name.includes('model')) return '🧠'
  return '🤖'
}

/** AI 消息标签栏的 CSS class */
function getLabelBarClass(source) {
  if (!source || source === 'main') return 'label-bar-main'
  return 'label-bar-sub'
}

/** AI 消息头像的 CSS class */
function getAvatarClass(source) {
  if (!source || source === 'main') return 'avatar-main'
  return 'avatar-sub'
}

// ============================================================
// 消息来源
// ============================================================

</script>

<style scoped>
.response-process { margin: 0 0 14px; border: 1px solid var(--color-border); border-radius: 10px; background: var(--color-bg-secondary); }
.response-process > summary { cursor: pointer; padding: 11px 14px; color: var(--color-text-secondary); font-size: 12px; }
.response-process > summary:focus-visible { outline: 2px solid var(--color-primary); outline-offset: 2px; }
.response-process :deep(.tool-call-panel) { margin: 10px 14px 16px; }
.process-note { padding: 10px 14px; font-size: 13px; color: var(--color-text-secondary); border-top: 1px solid var(--color-border); }
.process-note > span { font-size: 11px; }
.reply-status { color: var(--color-text-secondary); font-size: 13px; }
.reply-status.active::before { content: ''; display: inline-block; width: 7px; height: 7px; margin-right: 9px; border-radius: 50%; background: var(--color-primary); box-shadow: 0 0 0 0 rgba(22,134,109,.26); animation: status-pulse 1.5s ease-out infinite; }
@keyframes status-pulse { 70% { box-shadow: 0 0 0 7px rgba(22,134,109,0); } 100% { box-shadow: 0 0 0 0 rgba(22,134,109,0); } }
/* ============================================================ */
/* 基础布局 */
/* ============================================================ */
.message-item {
  display: flex;
  gap: 12px;
  padding: 18px 0;
  max-width: 100%;
}

.message-item.streaming {
  background: transparent;
}

/* ============================================================ */
/* AI 角色标签栏 */
/* ============================================================ */
.msg-label-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
  padding: 0;
  flex-wrap: wrap;
}

.msg-role-badge {
  display: inline-flex;
  align-items: center;
  padding: 0;
  font-size: 13px;
  font-weight: 600;
  letter-spacing: 0;
}

/* 主 Agent AI 标签 */
.label-bar-main .msg-role-badge {
  color: var(--color-primary);
}

/* 子 Agent AI 标签 */
.label-bar-sub .msg-role-badge {
  color: var(--color-primary);
}

.msg-source-badge {
  display: inline-flex;
  align-items: center;
  padding: 3px 10px;
  border-radius: 6px;
  font-size: 12px;
  font-weight: 600;
}

.label-bar-sub .msg-source-badge {
  background: var(--color-bg-tertiary);
  color: var(--color-text-secondary);
}

/* ============================================================ */
/* 用户消息（右侧） */
/* ============================================================ */
.message-user {
  justify-content: flex-end;
}

.message-user .message-content-wrapper {
  max-width: min(72%, 760px);
  min-width: 0;
}

.message-user .message-content {
  background: var(--color-primary-soft);
  border: 0;
  border-radius: 18px;
  padding: 14px 18px;
}

.message-user .content-text {
  color: var(--color-text);
  font-size: 15px;
  line-height: 1.7;
  text-align: left;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  word-break: normal;
}

.message-user .avatar { background: linear-gradient(135deg, #c48767 0%, #9e6047 100%); }

/* ============================================================ */
/* AI 助手消息（左侧） */
/* ============================================================ */
.message-assistant { justify-content: flex-start; }

.message-assistant .message-content-wrapper {
  width: 100%;
  max-width: 100%;
  min-width: 0;
}

.message-assistant .message-content {
  background: transparent;
  border: 0;
  padding: 0;
  line-height: 1.7;
  overflow: hidden;
}

.avatar-main { background: linear-gradient(135deg, #b67858 0%, #8f543f 100%); }
.avatar-sub { background: linear-gradient(135deg, #987664 0%, #6f5549 100%); }

/* ============================================================ */
/* 通用样式 */
/* ============================================================ */
.message-avatar { display: none; }

.avatar {
  width: 38px;
  height: 38px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 19px;
  box-shadow: 0 2px 4px rgba(0, 0, 0, 0.08);
}

.typing-cursor {
  display: inline-block;
  animation: blink 1s infinite;
  color: var(--color-primary);
  margin-left: 3px;
  font-weight: bold;
}

@media (max-width: 760px) {
  .message-item { padding: 14px 0; gap: 0; }
  .message-user .message-content-wrapper { max-width: 90%; }
  .message-assistant .message-content-wrapper { max-width: 100%; }
}

/* ============================================================ */
/* 动画 */
/* ============================================================ */
@keyframes blink {
  0%, 50% { opacity: 1; }
  51%, 100% { opacity: 0; }
}

</style>
