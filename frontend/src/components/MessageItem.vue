<template>
  <!-- ============================================================ -->
  <!-- 用户消息 - 右侧蓝色气泡 -->
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
        <span class="msg-role-badge">AI</span>
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
        <p v-if="!message.content" class="reply-status" role="status">{{ isStreaming ? '正在处理，请稍候…' : '本轮暂无文字答复，可展开查看执行过程。' }}</p>
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
  isStreaming: { type: Boolean, default: false }
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
.response-process { margin: 0 0 14px; border: 1px solid #e0e5df; border-radius: 10px; background: #f5f7f3; }
.response-process > summary { cursor: pointer; padding: 11px 14px; color: #667a72; font-size: 12px; }
.response-process > summary:focus-visible { outline: 2px solid #597c6b; outline-offset: 2px; }
.response-process :deep(.tool-call-panel) { margin: 10px 14px 16px; }
.process-note { padding: 10px 14px; font-size: 13px; color: #6a7972; border-top: 1px solid #e0e5df; }
.process-note > span { font-size: 11px; }
.reply-status { color: #6a7972; font-size: 13px; }
/* ============================================================ */
/* 基础布局 */
/* ============================================================ */
.message-item {
  display: flex;
  gap: 12px;
  padding: 12px 24px;
  max-width: 100%;
}

.message-item.streaming {
  background: linear-gradient(to right, transparent, rgba(14, 165, 233, 0.03), transparent);
}

/* ============================================================ */
/* AI 角色标签栏 */
/* ============================================================ */
.msg-label-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
  padding: 6px 0;
  flex-wrap: wrap;
}

.msg-role-badge {
  display: inline-flex;
  align-items: center;
  padding: 3px 12px;
  border-radius: 6px;
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.3px;
  text-transform: uppercase;
}

/* 主 Agent AI 标签 */
.label-bar-main .msg-role-badge {
  background: #dbeafe;
  color: #1d4ed8;
}

/* 子 Agent AI 标签 */
.label-bar-sub .msg-role-badge {
  background: #ede9fe;
  color: #7c3aed;
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
  background: #ddd6fe;
  color: #6d28d9;
}

/* ============================================================ */
/* 用户消息（右侧） */
/* ============================================================ */
.message-user {
  justify-content: flex-end;
}

.message-user .message-content-wrapper {
  max-width: 65%;
  text-align: right;
}

.message-user .message-content {
  background: linear-gradient(135deg, #0ea5e9 0%, #6366f1 100%);
  border-radius: 18px 18px 6px 18px;
  padding: 14px 18px;
  box-shadow: 0 2px 8px rgba(14, 165, 233, 0.2);
}

.message-user .content-text {
  color: #fff;
  font-size: 15px;
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-word;
}

.message-user .avatar { background: linear-gradient(135deg, #6366f1 0%, #8b5cf6 100%); }

/* ============================================================ */
/* AI 助手消息（左侧） */
/* ============================================================ */
.message-assistant { justify-content: flex-start; }

.message-assistant .message-content-wrapper {
  max-width: 80%;
  overflow: hidden;
}

.message-assistant .message-content {
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 6px 14px 14px 14px;
  padding: 14px 18px;
  line-height: 1.7;
  overflow: hidden;
}

.avatar-main { background: linear-gradient(135deg, #0ea5e9 0%, #0284c7 100%); }
.avatar-sub { background: linear-gradient(135deg, #7c3aed 0%, #6d28d9 100%); }

/* ============================================================ */
/* 通用样式 */
/* ============================================================ */
.message-avatar { flex-shrink: 0; }

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
  color: #0ea5e9;
  margin-left: 3px;
  font-weight: bold;
}

/* ============================================================ */
/* 动画 */
/* ============================================================ */
@keyframes blink {
  0%, 50% { opacity: 1; }
  51%, 100% { opacity: 0; }
}

</style>
