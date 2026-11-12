<template>
  <div v-if="checking" class="session-loading" role="status"><span class="session-spinner"></span>正在打开工作台…</div>
  <template v-else-if="user">
    <div v-if="error" class="account-error" role="alert">{{ error }} <button @click="error = ''" aria-label="关闭提示">×</button></div>
    <ChatWorkspace :key="user.username" :user="user" :logging-out="loggingOut" @logout="handleLogout" />
  </template>
  <AuthPage v-else :notice="error" @authenticated="handleAuthenticated" />
</template>

<script setup>
import { onMounted, onBeforeUnmount, ref } from 'vue'
import AuthPage from './components/AuthPage.vue'
import ChatWorkspace from './ChatWorkspace.vue'
import { currentUser, logout } from './api/auth.js'

const checking = ref(true)
const user = ref(null)
const error = ref('')
const loggingOut = ref(false)

function handleAuthenticated(account) {
  error.value = ''
  user.value = account
}
function sessionExpired() {
  user.value = null
  error.value = '登录已过期，请重新登录。'
}
async function handleLogout() {
  if (loggingOut.value) return
  loggingOut.value = true
  try {
    await logout()
    user.value = null
    error.value = ''
  } catch (cause) {
    error.value = `退出失败：${cause.message}`
  } finally {
    loggingOut.value = false
  }
}
onMounted(async () => {
  window.addEventListener('auth:expired', sessionExpired)
  try { user.value = await currentUser() }
  catch (cause) { if (cause.status !== 401) error.value = '暂时无法连接服务，请稍后重试。' }
  finally { checking.value = false }
})
onBeforeUnmount(() => window.removeEventListener('auth:expired', sessionExpired))
</script>

<style scoped>
.session-loading { min-height: 100dvh; display: flex; align-items: center; justify-content: center; gap: 14px; color: #647078; }
.session-spinner { width: 20px; height: 20px; border: 2px solid var(--color-border); border-top-color: var(--color-primary); border-radius: 50%; animation: spin 1s linear infinite; }
.account-error { position: fixed; top: 12px; left: 50%; transform: translateX(-50%); z-index: 100; padding: 12px 20px; border-radius: 12px; background: #fff1e8; color: #9b3928; box-shadow: 0 4px 20px #0001; }
.account-error button { background: none; border: 0; margin-left: 12px; cursor: pointer; }
@keyframes spin { to { transform: rotate(360deg); } }
</style>
