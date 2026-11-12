<template>
  <div
    class="session-row"
    :class="{ active }"
    role="button"
    tabindex="0"
    @click="selectSession"
    @keydown.enter="selectSession"
    @keydown.space.prevent="selectSession"
  >
    <span class="session-icon" aria-hidden="true">
      <svg viewBox="0 0 24 24"><path d="M7 8h10M7 12h7m-5 8-4 1 1-4a8 8 0 1 1 3 3Z"/></svg>
    </span>
    <span class="session-copy">
      <span class="session-title" :title="session.title">{{ session.title }}</span>
      <span class="session-meta">
        <span v-if="session.running" class="session-run-status"><i aria-hidden="true"></i>运行中</span>
        <span v-else-if="session.runStatus === 'waiting'" class="session-attention">待确认</span>
        <span v-else-if="session.unread" class="session-attention">{{ session.runStatus === 'error' ? '执行失败' : '新回复' }}</span>
        <span v-if="session.pinned" class="pinned-label">置顶</span>
        <span>{{ formatTime(session.updated_at) }}</span>
      </span>
    </span>

    <details class="session-menu" :open="menuOpen" @click.stop @keydown.stop="handleMenuKeydown">
      <summary aria-label="会话操作" title="会话操作" :aria-expanded="menuOpen" @click.prevent="$emit('toggle-menu')">•••</summary>
      <div class="menu-popover">
        <button type="button" @click="runAction('pin')">
          <span aria-hidden="true">{{ session.pinned ? '↘' : '↑' }}</span>
          {{ session.pinned ? '取消置顶' : '置顶会话' }}
        </button>
        <button type="button" @click="runAction('rename')"><span aria-hidden="true">✎</span>修改名称</button>
        <button type="button" class="danger" @click="runAction('delete')"><span aria-hidden="true">⌫</span>删除会话</button>
      </div>
    </details>
  </div>
</template>

<script setup>
const props = defineProps({
  session: { type: Object, required: true },
  active: Boolean,
  menuOpen: Boolean
})

const emit = defineEmits(['select', 'pin', 'rename', 'delete', 'toggle-menu', 'close-menu'])

function selectSession() {
  emit('close-menu')
  emit('select')
}

function handleMenuKeydown(event) {
  if (event.key !== 'Escape') return
  event.preventDefault()
  emit('close-menu')
  event.currentTarget.querySelector('summary')?.focus()
}

function runAction(action) {
  emit('close-menu')
  emit(action, props.session)
}

function formatTime(timestamp) {
  if (!timestamp) return ''
  const date = new Date(timestamp)
  const now = new Date()
  const diff = Math.max(0, now - date)
  if (diff < 60 * 60 * 1000) {
    const minutes = Math.floor(diff / (60 * 1000))
    return minutes < 1 ? '刚刚' : `${minutes} 分钟前`
  }
  if (diff < 24 * 60 * 60 * 1000) return `${Math.floor(diff / (60 * 60 * 1000))} 小时前`
  return `${date.getMonth() + 1}/${date.getDate()}`
}
</script>

<style scoped>
.session-row {
  position: relative;
  display: flex;
  align-items: center;
  min-width: 0;
  gap: 9px;
  margin: 3px 0;
  padding: 9px 8px 9px 10px;
  border: 1px solid transparent;
  border-radius: 10px;
  color: var(--nav-text);
  cursor: pointer;
  transition: background-color .16s ease, border-color .16s ease, box-shadow .16s ease;
}
.session-row:hover,
.session-row:focus-visible,
.session-row:focus-within { background: var(--nav-hover); border-color: var(--nav-border); outline: none; }
.session-row.active { background: var(--nav-active); border-color: var(--color-primary); box-shadow: inset 3px 0 var(--nav-accent); }
.session-icon { width: 19px; height: 19px; flex: 0 0 19px; color: var(--nav-muted); }
.session-icon svg { width: 100%; height: 100%; fill: none; stroke: currentColor; stroke-width: 1.7; stroke-linecap: round; stroke-linejoin: round; }
.session-copy { display: flex; flex: 1; min-width: 0; flex-direction: column; gap: 3px; }
.session-title { overflow: hidden; color: var(--nav-text); font-size: 13px; line-height: 1.35; text-overflow: ellipsis; white-space: nowrap; }
.session-row.active .session-title { color: var(--nav-text); font-weight: 650; }
.session-meta { display: flex; align-items: center; gap: 6px; color: var(--nav-muted); font-size: 10px; line-height: 1.2; }
.pinned-label { color: var(--nav-accent); font-weight: 650; }
.session-run-status { display: inline-flex; align-items: center; gap: 5px; color: var(--nav-accent); font-size: 11px; font-weight: 600; }
.session-run-status i { width: 7px; height: 7px; border-radius: 50%; background: var(--nav-accent); animation: running-pulse 1.6s ease-in-out infinite; }
.session-attention { color: #f0c58c; font-size: 11px; font-weight: 600; }
@keyframes running-pulse { 50% { opacity: .35; } }
@media (prefers-reduced-motion: reduce) { .session-run-status i { animation: none; } }
.session-menu { position: relative; flex: 0 0 auto; }
.session-menu summary { display: grid; width: 27px; height: 27px; place-items: center; border-radius: 7px; color: var(--nav-muted); font-size: 14px; letter-spacing: 1px; list-style: none; opacity: 0; }
.session-menu summary::-webkit-details-marker { display: none; }
.session-row:hover .session-menu summary,
.session-row:focus-within .session-menu summary,
.session-row.active .session-menu summary,
.session-menu[open] summary { opacity: 1; }
.session-menu summary:hover { background: var(--nav-hover); color: var(--nav-text); }
.menu-popover { position: absolute; z-index: 20; top: 31px; right: 0; width: 138px; padding: 6px; border: 1px solid var(--color-border); border-radius: 11px; background: var(--color-bg-secondary); box-shadow: 0 12px 28px rgba(15, 23, 42, .14); }
.menu-popover button { display: flex; width: 100%; align-items: center; gap: 9px; padding: 8px 9px; border: 0; border-radius: 7px; background: transparent; color: var(--color-text); cursor: pointer; font-size: 12px; text-align: left; }
.menu-popover button:hover { background: var(--color-bg-tertiary); }
.menu-popover button.danger { color: #a44f3e; }
.menu-popover button.danger:hover { background: #f9e7e2; }
@media (hover: none) { .session-menu summary { opacity: 1; } }
</style>
