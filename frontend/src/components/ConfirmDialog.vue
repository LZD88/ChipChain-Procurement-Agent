<template>
  <Teleport to="body">
    <dialog
      ref="dialogRef"
      class="confirm-dialog"
      role="alertdialog"
      aria-modal="true"
      :aria-labelledby="`${dialogId}-title`"
      :aria-describedby="`${dialogId}-description`"
      @cancel.prevent="$emit('cancel')"
      @keydown.stop="handleKeydown"
      @pointerdown="startedOnBackdrop = isBackdropEvent($event)"
      @click="handleBackdropClick"
    >
      <button class="confirm-close" type="button" aria-label="关闭删除确认" @click="$emit('cancel')">
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m7 7 10 10M17 7 7 17" /></svg>
      </button>
      <h2 :id="`${dialogId}-title`">{{ title }}</h2>
      <p :id="`${dialogId}-description`">{{ description }}</p>
      <div class="confirm-actions">
        <button class="confirm-cancel" type="button" autofocus @click="$emit('cancel')">取消</button>
        <button class="confirm-delete" type="button" @click="$emit('confirm')">{{ confirmLabel }}</button>
      </div>
    </dialog>
  </Teleport>
</template>

<script setup>
import { getCurrentInstance, onBeforeUnmount, onMounted, ref } from 'vue'

defineProps({
  title: { type: String, required: true },
  description: { type: String, required: true },
  confirmLabel: { type: String, default: '删除' }
})
const emit = defineEmits(['cancel', 'confirm'])
const dialogRef = ref(null)
const dialogId = `delete-confirmation-${getCurrentInstance().uid}`
let startedOnBackdrop = false
let returnFocus = null

function isBackdropEvent(event) {
  if (event.target !== dialogRef.value) return false
  const rect = dialogRef.value.getBoundingClientRect()
  return event.clientX < rect.left || event.clientX > rect.right ||
    event.clientY < rect.top || event.clientY > rect.bottom
}

function handleBackdropClick(event) {
  if (startedOnBackdrop && isBackdropEvent(event)) emit('cancel')
  startedOnBackdrop = false
}

function handleKeydown(event) {
  if (event.key !== 'Tab') return
  const buttons = dialogRef.value.querySelectorAll('button:not(:disabled)')
  const first = buttons[0]
  const last = buttons[buttons.length - 1]
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault()
    last.focus()
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault()
    first.focus()
  }
}

onMounted(() => {
  const activeElement = document.activeElement
  returnFocus = activeElement?.closest('details')?.querySelector('summary') || activeElement
  dialogRef.value.showModal()
})

onBeforeUnmount(() => {
  dialogRef.value?.close()
  if (returnFocus?.isConnected) returnFocus.focus({ preventScroll: true })
})
</script>

<style scoped>
.confirm-dialog {
  width: 420px;
  max-width: calc(100vw - 32px);
  max-height: calc(100dvh - 32px);
  margin: auto;
  padding: 20px;
  overflow: auto;
  border: 1px solid #e7e7e9;
  border-radius: 22px;
  background: #fbfbfc;
  color: #202124;
  font: inherit;
  box-shadow: 0 14px 44px rgb(0 0 0 / 12%);
}
.confirm-dialog::backdrop { background: rgb(0 0 0 / 15%); }
.confirm-dialog h2 { margin: 0 30px 6px 0; font-size: 20px; font-weight: 600; line-height: 1.4; }
.confirm-dialog p { margin: 0; color: #74757b; font-size: 14px; line-height: 1.6; overflow-wrap: anywhere; }
.confirm-close { position: absolute; top: 14px; right: 14px; display: grid; width: 28px; height: 28px; place-items: center; padding: 0; border: 0; border-radius: 8px; background: transparent; color: #62636a; cursor: pointer; }
.confirm-close:hover { background: #ededf0; color: #242529; }
.confirm-close svg { width: 16px; height: 16px; fill: none; stroke: currentColor; stroke-width: 1.5; stroke-linecap: round; }
.confirm-actions { display: flex; justify-content: flex-end; gap: 12px; margin-top: 14px; }
.confirm-actions button { min-width: 74px; padding: 6px 15px; border: 1px solid transparent; border-radius: 11px; font: inherit; font-size: 14px; line-height: 1.4; cursor: pointer; transition: background-color .15s ease; }
.confirm-actions .confirm-cancel { border-color: #e5e5e8; background: #fff; color: #292a2e; }
.confirm-cancel:hover { background: #f0f0f2; }
.confirm-delete { background: #fae5e5; color: #d92323; }
.confirm-delete:hover { background: #f6d4d4; }
.confirm-dialog button:focus-visible { outline: 2px solid #888b93; outline-offset: 3px; }
@media (prefers-reduced-motion: reduce) { .confirm-actions button { transition: none; } }
</style>
