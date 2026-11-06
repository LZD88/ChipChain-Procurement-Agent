<template>
  <div class="input-area">
    <div class="input-container">
      <!-- 输入卡片：编辑区与辅助操作收在同一个视觉容器中 -->
      <div class="input-wrapper" :class="{ focused: isFocused, streaming: streaming }">
        <textarea
          ref="textareaRef"
          v-model="inputText"
          @focus="isFocused = true"
          @blur="isFocused = false"
          @keydown.enter.exact.prevent="handleSend"
          @input="autoResize"
          placeholder="输入消息，Enter 发送..."
          rows="1"
          :disabled="streaming"
          class="input-textarea"
        ></textarea>

        <div class="composer-toolbar">
          <label class="toggle-label" title="控制是否在对话中展开 Agent 工具调用过程">
            <input
              type="checkbox"
              :checked="showToolCalls"
              @change="emit('toggle-tool-calls', $event.target.checked)"
              class="toggle-checkbox"
            />
            <span class="toggle-switch"></span>
            <span class="toggle-text">执行过程</span>
          </label>

          <div class="composer-actions">
            <span v-if="!streaming" class="input-hint">Enter 发送 · Shift + Enter 换行</span>
            <span v-else class="input-hint streaming-hint">正在生成回复...</span>

            <button
              v-if="!streaming"
              class="send-btn"
              type="button"
              aria-label="发送消息"
              @click="handleSend"
              :disabled="!inputText.trim()"
            >
              <span>发送</span>
              <svg viewBox="0 0 20 20" aria-hidden="true">
                <path d="M4 10h11M11 6l4 4-4 4" />
              </svg>
            </button>
            <button
              v-else
              class="stop-btn"
              type="button"
              @click="handleStop"
            >
              <span class="stop-icon">■</span>
              <span>停止</span>
            </button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, nextTick } from 'vue'

/**
 * 输入区域组件
 *
 * 提供消息输入功能：
 * - 正常状态：显示"发送"按钮
 * - 流式输出时：输入框禁用，按钮变为"停止"
 */

const props = defineProps({
  streaming: {
    type: Boolean,
    default: false
  },
  showToolCalls: {
    type: Boolean,
    default: true
  }
})

const emit = defineEmits(['send', 'stop', 'toggle-tool-calls'])

// 输入文本
const inputText = ref('')
// 是否获取焦点
const isFocused = ref(false)
// textarea 引用
const textareaRef = ref(null)

/**
 * 发送消息
 */
function handleSend() {
  const text = inputText.value.trim()
  if (!text) return

  emit('send', text)
  inputText.value = ''

  nextTick(() => {
    if (textareaRef.value) {
      textareaRef.value.style.height = 'auto'
    }
  })
}

/**
 * 停止对话
 */
function handleStop() {
  emit('stop')
}

/**
 * 自动调整 textarea 高度
 */
function autoResize() {
  const textarea = textareaRef.value
  if (!textarea) return

  textarea.style.height = 'auto'
  textarea.style.height = Math.min(textarea.scrollHeight, 200) + 'px'
}
</script>

<style scoped>
.input-area {
  padding: 12px clamp(18px, 5vw, 72px) 16px;
  background: linear-gradient(180deg, rgba(251, 250, 247, 0.78), #fbfaf7 32%);
  border-top: 1px solid #e7dfd4;
}

.input-container {
  max-width: 920px;
  margin: 0 auto;
}

.toggle-label {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
  user-select: none;
  min-height: 32px;
  padding: 0 2px;
}

.toggle-checkbox {
  position: absolute;
  width: 1px;
  height: 1px;
  opacity: 0;
  pointer-events: none;
}

.toggle-switch {
  position: relative;
  width: 34px;
  height: 19px;
  background: #d8d3ca;
  border-radius: 999px;
  transition: all 0.2s ease;
}

.toggle-switch::after {
  content: '';
  position: absolute;
  top: 2px;
  left: 2px;
  width: 15px;
  height: 15px;
  background: #fff;
  border-radius: 50%;
  transition: all 0.2s ease;
  box-shadow: 0 1px 4px rgba(32, 58, 75, 0.22);
}

.toggle-checkbox:checked + .toggle-switch {
  background: #ee6c4d;
}

.toggle-checkbox:checked + .toggle-switch::after {
  transform: translateX(15px);
}

.toggle-checkbox:focus-visible + .toggle-switch {
  box-shadow: 0 0 0 3px rgba(238, 108, 77, 0.18);
}

.toggle-text {
  font-size: 12px;
  color: #718087;
  font-weight: 600;
  letter-spacing: 0.01em;
}

.input-wrapper {
  padding: 12px 12px 10px 16px;
  background: #fff;
  border: 1px solid #d9d0c4;
  border-radius: 20px;
  box-shadow: 0 10px 28px rgba(38, 42, 39, 0.07), 0 2px 7px rgba(38, 42, 39, 0.035);
  transition: border-color 0.2s ease, box-shadow 0.2s ease, background 0.2s ease;
}

.input-wrapper.focused {
  border-color: #ee6c4d;
  box-shadow: 0 0 0 3px rgba(238, 108, 77, 0.11), 0 13px 30px rgba(38, 42, 39, 0.08);
}

.input-wrapper.streaming {
  background: #fffafa;
  border-color: #efc9bf;
}

.input-textarea {
  display: block;
  width: 100%;
  box-sizing: border-box;
  border: none;
  background: transparent;
  font-size: 15px;
  line-height: 1.55;
  resize: none;
  outline: none;
  min-height: 30px;
  max-height: 200px;
  padding: 4px 4px 6px 0;
  color: #203a4b;
  font-family: inherit;
  font-weight: 400;
}

.input-textarea::placeholder {
  color: #9babb3;
}

.input-textarea:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.composer-toolbar,
.composer-actions {
  display: flex;
  align-items: center;
}

.composer-toolbar {
  justify-content: space-between;
  gap: 16px;
  min-height: 36px;
  margin-top: 3px;
}

.composer-actions {
  justify-content: flex-end;
  gap: 14px;
}

.input-hint {
  color: #a09d95;
  font-size: 11px;
  white-space: nowrap;
}

.send-btn {
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  min-height: 36px;
  padding: 0 15px;
  background: #ee6c4d;
  color: #fff;
  border: none;
  border-radius: 11px;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.2s ease;
  box-shadow: 0 7px 14px rgba(238, 108, 77, 0.22);
}

.send-btn svg {
  width: 16px;
  height: 16px;
  fill: none;
  stroke: currentColor;
  stroke-width: 1.8;
  stroke-linecap: round;
  stroke-linejoin: round;
}

.send-btn:hover:not(:disabled) {
  transform: translateY(-1px);
  background: #d9573b;
  box-shadow: 0 10px 18px rgba(238, 108, 77, 0.3);
}

.send-btn:active:not(:disabled) {
  transform: translateY(0);
}

.send-btn:disabled {
  background: #e9e4dc;
  color: #a9a59e;
  cursor: not-allowed;
  box-shadow: none;
}

.stop-btn {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 6px;
  min-height: 36px;
  padding: 0 15px;
  background: linear-gradient(135deg, #ef4444 0%, #dc2626 100%);
  color: #fff;
  border: none;
  border-radius: 11px;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.2s ease;
  box-shadow: 0 2px 8px rgba(239, 68, 68, 0.3);
  animation: stopPulse 2s ease-in-out infinite;
}

.stop-btn:hover {
  transform: translateY(-1px);
  box-shadow: 0 4px 12px rgba(239, 68, 68, 0.5);
}

.stop-btn:active {
  transform: translateY(0);
}

.stop-icon {
  font-size: 12px;
}

.streaming-hint {
  color: #dc5d45;
  animation: blink 1.5s ease-in-out infinite;
}

@media (max-width: 720px) {
  .input-area {
    padding: 10px 12px 12px;
  }

  .input-wrapper {
    padding: 10px 10px 8px 13px;
    border-radius: 17px;
  }

  .input-hint {
    display: none;
  }

  .composer-toolbar {
    gap: 10px;
  }
}

@keyframes stopPulse {
  0%, 100% { box-shadow: 0 2px 8px rgba(239, 68, 68, 0.3); }
  50% { box-shadow: 0 2px 16px rgba(239, 68, 68, 0.5); }
}

@keyframes blink {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.4; }
}
</style>
