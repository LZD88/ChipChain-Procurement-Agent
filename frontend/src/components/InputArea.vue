<template>
  <div class="input-area">
    <div class="input-container">
      <!-- 输入卡片：编辑区与辅助操作收在同一个视觉容器中 -->
      <div class="input-wrapper" :class="{ focused: isFocused, streaming: streaming }">
        <div v-if="projectName" class="composer-context">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 7.5h7l2-2h9v13H3z"/></svg>
          <strong>{{ projectName }}</strong>
          <span>项目会话</span>
        </div>
        <textarea
          ref="textareaRef"
          v-model="inputText"
          @focus="isFocused = true"
          @blur="isFocused = false"
          @keydown.enter.exact="handleEnter"
          @input="autoResize"
          placeholder="继续描述你的采购需求…"
          aria-label="消息内容"
          rows="1"
          :disabled="streaming || disabled"
          class="input-textarea"
        ></textarea>

        <div class="composer-toolbar">
          <button class="toggle-label" :class="{ 'is-on': showToolCalls }" type="button" role="switch" :aria-checked="showToolCalls" aria-label="显示执行过程" :title="showToolCalls ? '执行过程已显示，点击隐藏工具调用和中间步骤' : '执行过程已隐藏，点击显示工具调用和中间步骤'" @click="emit('toggle-tool-calls', !showToolCalls)">
            <svg class="process-icon" viewBox="0 0 20 20" aria-hidden="true"><path d="m4 5 4 5-4 5M11 15h5"/></svg>
            <span class="toggle-text">执行过程</span>
            <span class="toggle-state">{{ showToolCalls ? '已开启' : '已关闭' }}</span>
            <span class="toggle-switch" aria-hidden="true"><i></i></span>
          </button>

          <div class="composer-actions">
            <span v-if="streaming" class="streaming-hint" role="status">正在生成回复…</span>

            <button
              v-if="!streaming"
              class="send-btn"
              type="button"
              aria-label="发送消息"
              @click="handleSend"
              :disabled="disabled || !inputText.trim()"
              title="发送消息"
            >
              <svg viewBox="0 0 20 20" aria-hidden="true">
                <path d="M10 15V5M5.5 9.5 10 5l4.5 4.5" />
              </svg>
            </button>
            <button
              v-else
              class="stop-btn"
              type="button"
              aria-label="停止生成"
              title="停止生成"
              @click="handleStop"
            >
              <span class="stop-icon" aria-hidden="true"></span>
            </button>
          </div>
        </div>
      </div>
      <p class="composer-footnote">Enter 发送<span aria-hidden="true"> · </span>Shift + Enter 换行</p>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, nextTick } from 'vue'

/**
 * 输入区域组件
 *
 * 提供消息输入功能：
 * - 正常状态：显示"发送"按钮
 * - 流式输出时：输入框禁用，按钮变为"停止"
 */

const props = defineProps({
  modelValue: { type: String, default: '' },
  disabled: Boolean,
  streaming: {
    type: Boolean,
    default: false
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

const emit = defineEmits(['send', 'stop', 'toggle-tool-calls', 'update:modelValue'])

// 输入文本
const inputText = computed({ get: () => props.modelValue, set: value => emit('update:modelValue', value) })
// 是否获取焦点
const isFocused = ref(false)
// textarea 引用
const textareaRef = ref(null)

/**
 * 发送消息
 */
function handleSend() {
  if (props.streaming || props.disabled) return
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

function handleEnter(event) {
  if (event.isComposing || event.keyCode === 229) return
  event.preventDefault()
  handleSend()
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

function focus() {
  nextTick(() => textareaRef.value?.focus())
}

defineExpose({ focus })
</script>

<style scoped>
.input-area {
  flex-shrink: 0;
  padding: 12px var(--conversation-gutter) max(14px, env(safe-area-inset-bottom));
  background: var(--workspace-surface);
}
.input-container { width: 100%; max-width: var(--conversation-width); margin: 0 auto; }
.input-wrapper {
  padding: 16px 16px 12px;
  border: 1px solid var(--color-border);
  border-radius: 22px;
  background: #fff;
  box-shadow: 0 2px 9px #26302606;
  transition: border-color .16s, box-shadow .16s;
}
.input-wrapper.focused { border-color: var(--color-primary); box-shadow: 0 0 0 2px var(--color-focus), 0 3px 12px #26302608; }
.input-wrapper.streaming { background: var(--color-bg-secondary); }
.input-textarea {
  display: block;
  width: 100%;
  min-height: 38px;
  max-height: 200px;
  padding: 0 3px 8px;
  border: 0;
  outline: none;
  background: transparent;
  color: var(--color-text);
  font: inherit;
  font-size: 15px;
  line-height: 1.65;
  resize: none;
}
.input-textarea::placeholder { color: var(--color-text-muted); }
.input-textarea:disabled { color: var(--color-text-secondary); cursor: default; opacity: 1; }
.composer-toolbar, .composer-actions { display: flex; align-items: center; gap: 12px; }
.composer-toolbar { justify-content: space-between; gap: 10px; }
.composer-actions { margin-left: auto; }
.toggle-label { position: relative; display: inline-flex; align-items: center; gap: 7px; min-height: 34px; padding: 5px 9px; border: 1px solid var(--color-border); border-radius: 8px; background:var(--color-bg-tertiary); color: var(--color-text-secondary); font:inherit; cursor: pointer; user-select: none; }
.toggle-label.is-on { color: var(--color-primary); background: var(--color-primary-soft); border-color:var(--color-primary-line); }
.toggle-label:hover { background: var(--color-bg-hover); }
.toggle-label:focus-visible { outline: 2px solid var(--color-primary); outline-offset: 2px; }
.toggle-state{font-size:12px;font-weight:600}
.toggle-switch{box-sizing:content-box;flex-shrink:0;display:inline-flex;align-items:center;width:23px;height:12px;margin-left:2px;padding:2px;border-radius:20px;background:var(--color-text-muted);transition:background .15s}
.toggle-switch i{width:12px;height:12px;display:block;border-radius:50%;background:#fff;transition:transform .15s}
.is-on .toggle-switch{background:var(--color-primary)}
.is-on .toggle-switch i{transform:translateX(11px)}
.process-icon { width: 16px; height: 16px; fill: none; stroke: currentColor; stroke-width: 1.6; stroke-linecap: round; stroke-linejoin: round; }
.toggle-text { font-size: 12px; font-weight: 550; }
.toggle-label { flex-shrink: 0; white-space: nowrap; }
.send-btn, .stop-btn {
  display: grid;
  place-items: center;
  flex: 0 0 34px;
  width: 34px;
  height: 34px;
  padding: 7px;
  border: 0;
  border-radius: 50%;
  background: var(--color-primary);
  color: #fff;
  cursor: pointer;
  transition: background .15s;
}
.send-btn svg { width: 20px; height: 20px; fill: none; stroke: currentColor; stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; }
.send-btn:hover:not(:disabled), .stop-btn:hover { background: var(--color-primary-hover); }
.send-btn:disabled { background: var(--color-bg-tertiary); color: var(--color-text-muted); cursor: default; }
.send-btn:focus-visible, .stop-btn:focus-visible { outline: 2px solid var(--color-primary); outline-offset: 3px; }
.stop-icon { width: 11px; height: 11px; border-radius: 2px; background: currentColor; }
.streaming-hint { font-size: 12px; color: var(--color-text-secondary); }
.composer-footnote { margin: 9px 0 0; text-align: center; color: var(--color-text-muted); font-size: 11px; line-height: 1.5; }
.composer-context { display: flex; align-items: center; gap: 7px; margin-bottom: 12px; padding: 0 3px; color: var(--color-text-secondary); }
.composer-context svg { width: 16px; height: 16px; flex-shrink: 0; fill: none; stroke: currentColor; stroke-width: 1.6; stroke-linejoin: round; }
.composer-context strong { overflow: hidden; font-size: 12px; font-weight: 550; text-overflow: ellipsis; white-space: nowrap; }
.composer-context > span { margin-left: auto; flex-shrink: 0; color: var(--color-text-muted); font-size: 11px; }
@media (max-width: 760px) {
  .input-area { padding: 8px 12px max(10px, env(safe-area-inset-bottom)); }
  .input-wrapper { padding: 14px 13px 10px; border-radius: 18px; }
  .input-textarea { font-size: 16px; }
  .composer-footnote { font-size: 11px; margin-top: 7px; }
}
@media (max-width: 460px) { .streaming-hint { display: none; } }
@media (prefers-reduced-motion: reduce) { .input-wrapper, .send-btn, .stop-btn { transition: none; } }
</style>
