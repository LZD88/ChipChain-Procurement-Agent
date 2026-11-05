<template>
  <aside class="sidebar">
    <!-- 侧边栏头部 -->
    <div class="sidebar-header">
      <div class="logo">
        <span class="logo-icon" aria-hidden="true"><i></i><i></i><i></i><i></i></span>
        <div class="logo-copy">
          <strong>棱镜工作台</strong>
          <span>ERP INTELLIGENCE</span>
        </div>
      </div>
      <button class="new-chat-btn" @click="$emit('new-chat')">
        <span class="icon">+</span>
        新建对话
      </button>
    </div>

    <!-- 搜索框 -->
    <div class="search-box">
      <input
        type="text"
        v-model="searchKeyword"
        placeholder="搜索会话..."
        class="search-input"
      />
    </div>

    <!-- 会话列表 -->
    <div class="session-list">
      <div class="session-list-header">
        <span>历史记录</span>
      </div>

      <div class="session-items">
        <div
          v-for="session in filteredSessions"
          :key="session.thread_id"
          class="session-item"
          :class="{ active: session.thread_id === currentThreadId }"
          @click="$emit('select-session', session.thread_id)"
        >
          <div class="session-info">
            <span class="session-title" :title="session.title">{{ session.title }}</span>
            <span class="session-time">{{ formatTime(session.updated_at) }}</span>
          </div>
          <button
            class="delete-btn"
            @click.stop="handleDelete(session.thread_id)"
            title="删除会话"
          >
            ×
          </button>
        </div>

        <!-- 空状态 -->
        <div v-if="sessions.length === 0" class="empty-state">
          <p>暂无会话记录</p>
          <p class="hint">开始一个新对话吧</p>
        </div>
      </div>
    </div>

    <!-- 侧边栏底部 -->
    <div class="sidebar-footer">
      <div class="account-avatar" aria-hidden="true">{{ username.slice(0, 1).toUpperCase() }}</div>
      <div class="account-copy"><strong>{{ username }}</strong><span>我的采购工作台</span></div>
      <button class="logout-btn" type="button" :disabled="loggingOut" @click="$emit('logout')">
        {{ loggingOut ? '退出中…' : '退出' }}
      </button>
    </div>
  </aside>
</template>

<script setup>
import { ref, computed } from 'vue'

// Props 定义
const props = defineProps({
  username: { type: String, required: true },
  loggingOut: Boolean,
  sessions: {
    type: Array,
    default: () => []
  },
  currentThreadId: {
    type: String,
    default: null
  }
})

// Emits 定义
const emit = defineEmits(['select-session', 'new-chat', 'delete-session', 'logout'])

// 搜索关键词
const searchKeyword = ref('')

// 过滤后的会话列表
const filteredSessions = computed(() => {
  if (!searchKeyword.value) {
    return props.sessions
  }
  return props.sessions.filter(session =>
    session.title.toLowerCase().includes(searchKeyword.value.toLowerCase())
  )
})

/**
 * 格式化时间
 */
function formatTime(timestamp) {
  if (!timestamp) return ''

  const date = new Date(timestamp)
  const now = new Date()
  const diff = now - date

  // 一天内显示相对时间
  if (diff < 24 * 60 * 60 * 1000) {
    const hours = Math.floor(diff / (60 * 60 * 1000))
    if (hours < 1) {
      const minutes = Math.floor(diff / (60 * 1000))
      return minutes < 1 ? '刚刚' : `${minutes} 分钟前`
    }
    return `${hours} 小时前`
  }

  // 超过一天显示日期
  return `${date.getMonth() + 1}/${date.getDate()}`
}

/**
 * 处理删除会话
 */
function handleDelete(threadId) {
  if (confirm('确定要删除这个会话吗？')) {
    emit('delete-session', threadId)
  }
}
</script>

<style scoped>
.account-avatar { width: 34px; height: 34px; border-radius: 10px; background: #e3e9db; color: #3f6650; display: grid; place-items: center; flex-shrink: 0; font-weight: 600; }
.account-copy { display: flex; flex-direction: column; min-width: 0; flex: 1; }
.account-copy strong { font-size: 13px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.account-copy span { font-size: 10px; color: #b7cad5; }
.logout-btn { border: 1px solid #728995; border-radius: 7px; background: transparent; padding: 6px 9px; color: #edf2f4; cursor: pointer; font-size: 11px; }
.logout-btn:hover { background: #395363; }
.logout-btn:focus-visible { outline: 2px solid #a9c1c8; outline-offset: 3px; }
.logout-btn:disabled { opacity: .65; cursor: wait; }
.sidebar-footer { display: flex; align-items: center; gap: 10px; }
.sidebar {
  width: 286px;
  height: calc(100vh - 36px);
  background: #203a4b;
  color: #f8f4eb;
  display: flex;
  flex-direction: column;
  flex-shrink: 0;
  border-radius: 24px;
  box-shadow: 0 18px 40px rgba(31, 58, 77, .18);
  overflow: hidden;
  position: relative;
}

/* 侧边栏头部 */
.sidebar-header {
  padding: 26px 22px 20px;
  border-bottom: 1px solid rgba(255, 255, 255, .12);
}

.logo {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 28px;
}

.logo-icon {
  width: 42px;
  height: 42px;
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 5px;
  padding: 8px;
  border-radius: 13px;
  background: #ee6c4d;
  box-shadow: 0 8px 16px rgba(238, 108, 77, .25);
}

.logo-icon i {
  display: block;
  border: 2px solid #fff4e7;
  border-radius: 50%;
}

.logo-copy {
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.logo-copy strong {
  font-size: 17px;
  letter-spacing: .08em;
}

.logo-copy span {
  font-size: 9px;
  letter-spacing: .18em;
  color: #a9c1c8;
}

.new-chat-btn {
  width: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  padding: 13px 16px;
  background: #f4eee4;
  color: #203a4b;
  border: none;
  border-radius: 13px;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
  position: relative;
  overflow: hidden;
}

.new-chat-btn:hover {
  transform: translateY(-2px);
  background: #fffaf1;
  box-shadow: 0 8px 18px rgba(0, 0, 0, .12);
}

.new-chat-btn:active {
  transform: translateY(0);
}

.new-chat-btn .icon {
  font-size: 20px;
  font-weight: 300;
}

/* 搜索框 */
.search-box {
  padding: 18px 22px 12px;
}

.search-input {
  width: 100%;
  padding: 11px 13px;
  background: rgba(255, 255, 255, .08);
  border: 1px solid rgba(255, 255, 255, .14);
  border-radius: 11px;
  color: #fff;
  font-size: 14px;
  outline: none;
  transition: all 0.3s ease;
}

.search-input:focus {
  border-color: #a9c1c8;
  box-shadow: 0 0 0 3px rgba(169, 193, 200, .15);
}

.search-input::placeholder {
  color: #a9c1c8;
}

/* 会话列表 */
.session-list {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 0 12px;
}

.session-list::-webkit-scrollbar {
  width: 4px;
}

.session-list::-webkit-scrollbar-track {
  background: transparent;
}

.session-list::-webkit-scrollbar-thumb {
  background: #688592;
  border-radius: 2px;
}

.session-list::-webkit-scrollbar-thumb:hover {
  background: #a9c1c8;
}

.session-list-header {
  padding: 12px 10px 8px;
  font-size: 12px;
  color: #b7cad5;
  letter-spacing: 1px;
  font-weight: 600;
}

.session-items {
  padding-bottom: 8px;
}

.session-item {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 8px;
  padding: 12px;
  margin: 6px 0;
  border-radius: 12px;
  background: rgba(255, 255, 255, .035);
  cursor: pointer;
  transition: background-color 0.2s ease, border-color 0.2s ease;
  border: 1px solid rgba(255, 255, 255, .06);
}

.session-item:hover,
.session-item:focus-within {
  background: rgba(255, 255, 255, .08);
  border-color: rgba(255, 255, 255, .16);
}

.session-item.active {
  background: #315366;
  border-color: #7895a6;
  box-shadow: inset 3px 0 #ee6c4d;
}

.session-info {
  flex: 1;
  min-width: 0;
  overflow: hidden;
}

.session-title {
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
  font-size: 14px;
  line-height: 1.55;
  overflow-wrap: anywhere;
  overflow: hidden;
  text-overflow: ellipsis;
  color: #f3f6f8;
  font-weight: 400;
}

.session-item.active .session-title {
  color: #ffffff;
  font-weight: 500;
}

.session-time {
  display: block;
  font-size: 12px;
  line-height: 1.5;
  color: #b7cad5;
  margin-top: 6px;
}

.session-item.active .session-time {
  color: #d2e1e8;
}

.delete-btn {
  opacity: 0;
  flex-shrink: 0;
  width: 28px;
  height: 28px;
  padding: 4px;
  background: transparent;
  border: 1px solid transparent;
  border-radius: 6px;
  color: #c4d3de;
  font-size: 18px;
  line-height: 1;
  cursor: pointer;
  transition: all 0.2s ease;
}

.session-item:hover .delete-btn,
.session-item:focus-within .delete-btn,
.session-item.active .delete-btn {
  opacity: 1;
}

.delete-btn:hover {
  background: rgba(238, 108, 77, .18);
  border-color: rgba(238, 108, 77, .4);
  color: #ffe1d8;
}

.delete-btn:focus-visible {
  outline: 2px solid #a9c1c8;
  outline-offset: 2px;
}

/* 空状态 */
.empty-state {
  padding: 60px 24px;
  text-align: center;
  color: #d7e4eb;
}

.empty-state p {
  margin-bottom: 10px;
}

.empty-state .hint {
  font-size: 13px;
  color: #b7cad5;
}

/* 侧边栏底部 */
.sidebar-footer {
  padding: 20px 22px;
  border-top: 1px solid rgba(255, 255, 255, .12);
}

.footer-info {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.version {
  font-size: 12px;
  color: #a9c1c8;
  letter-spacing: 0.5px;
}
@media (hover: none) {
  .delete-btn {
    opacity: 1;
  }
}

@media (max-width: 760px) {
  .sidebar {
    width: 100%;
    height: auto;
    border-radius: 0;
  }
}
</style>
