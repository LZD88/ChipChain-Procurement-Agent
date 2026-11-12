<template>
  <main class="auth-page">
    <header class="auth-header">
      <a href="/" class="auth-brand" aria-label="棱镜工作台首页"><span class="prism-mark" aria-hidden="true">◈</span><span>棱镜工作台<small>ERP INTELLIGENCE</small></span></a>
      <span class="header-caption">从需求到决策，有条不紊。</span>
    </header>
    <div class="auth-layout">
      <section class="auth-intro" aria-labelledby="intro-title">
        <div class="intro-eyebrow"><span></span>你的企业采购助手</div>
        <h1 id="intro-title">让每一次采购<br>都有据可依<span class="title-dot">。</span></h1>
        <p class="intro-description">把供应商、物料与订单放在同一段对话里。<br>让繁复的信息清晰起来，把时间留给重要的决策。</p>
        <div class="workflow-preview" aria-label="采购工作流程">
          <div class="preview-top"><span>PROCUREMENT WORKSPACE</span><span class="preview-tag">井然有序</span></div>
          <div class="workflow-row"><span class="step-number">01</span><div><strong>理解采购需求</strong><p>从物料规格到预算，明确每一个条件</p></div><span class="step-symbol">↗</span></div>
          <div class="workflow-row"><span class="step-number">02</span><div><strong>对比与分析</strong><p>连接供应商信息，辅助做出采购判断</p></div><span class="step-symbol">↗</span></div>
          <div class="workflow-row"><span class="step-number">03</span><div><strong>确认后，再执行</strong><p>关键订单操作，由你掌握最后的决定</p></div><span class="step-symbol">✓</span></div>
        </div>
        <p class="intro-footnote">专属会话记录 · 连续的工作上下文</p>
      </section>

      <section class="auth-card" aria-labelledby="auth-title">
        <div class="auth-tabs" aria-label="账号操作">
          <button type="button" :class="{ active: !isRegister }" :aria-pressed="!isRegister" :disabled="submitting" @click="changeMode(false)">登录</button>
          <button type="button" :class="{ active: isRegister }" :aria-pressed="isRegister" :disabled="submitting" @click="changeMode(true)">注册账号</button>
        </div>
        <h2 id="auth-title">{{ isRegister ? '开启你的采购工作台' : '欢迎回来' }}</h2>
        <p class="auth-subtitle">{{ isRegister ? '创建账号，开始你的第一段采购对话。' : '登录账号，继续上次的工作。' }}</p>
        <form @submit.prevent="submit">
          <label for="auth-username">用户名</label>
          <input id="auth-username" v-model="username" name="username" autocomplete="username" autocapitalize="none" :spellcheck="false" required minlength="2" maxlength="32" pattern="[A-Za-z0-9_-]{2,32}" placeholder="输入你的用户名" :disabled="submitting" aria-describedby="username-help">
          <p id="username-help" class="field-hint">2–32 位字母、数字、下划线或短横线，不区分大小写</p>
          <label for="auth-password">密码</label>
          <div class="password-field">
            <input id="auth-password" v-model="password" name="password" :type="showPassword ? 'text' : 'password'" :autocomplete="isRegister ? 'new-password' : 'current-password'" required minlength="8" maxlength="128" placeholder="输入 8–128 位密码" :disabled="submitting">
            <button type="button" :aria-label="showPassword ? '隐藏密码' : '显示密码'" :aria-pressed="showPassword" @click="showPassword = !showPassword">{{ showPassword ? '隐藏' : '显示' }}</button>
          </div>
          <template v-if="isRegister">
            <label for="auth-confirm">确认密码</label>
            <input id="auth-confirm" v-model="confirmation" name="confirm-password" type="password" autocomplete="new-password" required minlength="8" maxlength="128" placeholder="再次输入密码" :disabled="submitting">
          </template>
          <p v-if="error || notice" class="auth-error" role="alert">{{ error || notice }}</p>
          <button class="auth-submit" type="submit" :disabled="submitting">{{ submitting ? (isRegister ? '正在创建账号…' : '正在登录…') : (isRegister ? '创建账号并进入' : '进入工作台') }}<span v-if="!submitting" aria-hidden="true">→</span></button>
        </form>
        <div class="auth-card-footer"><span aria-hidden="true">◇</span> 每次登录，回到属于你的工作空间。</div>
      </section>
    </div>
    <footer class="auth-footer"><span>棱镜 · 企业采购智能助手</span><span>信息更清晰，决策更从容。</span></footer>
  </main>
</template>

<script setup>
import { ref } from 'vue'
import { login, register } from '../api/auth.js'
defineProps({ notice: { type: String, default: '' } })
const emit = defineEmits(['authenticated'])
const isRegister = ref(false)
const username = ref('')
const password = ref('')
const confirmation = ref('')
const showPassword = ref(false)
const submitting = ref(false)
const error = ref('')
function changeMode(registering) {
  isRegister.value = registering
  error.value = ''
  password.value = ''
  confirmation.value = ''
  showPassword.value = false
}
async function submit() {
  if (submitting.value) return
  error.value = ''
  if (isRegister.value && password.value !== confirmation.value) {
    error.value = '两次输入的密码不一致，请重新确认。'
    return
  }
  submitting.value = true
  try {
    const user = await (isRegister.value ? register : login)(username.value, password.value)
    password.value = ''
    confirmation.value = ''
    emit('authenticated', user)
  } catch (cause) {
    error.value = cause.message || '连接失败，请稍后重试。'
  } finally {
    submitting.value = false
  }
}
</script>

<style scoped>
.auth-page { min-height: 100dvh; padding: 32px clamp(24px, 6vw, 96px) 24px; background: radial-gradient(ellipse at 5% 60%, #e4f0ef 0, transparent 50%), var(--color-bg); display: flex; flex-direction: column; color: var(--color-text); overflow-y: auto; }
.auth-header, .auth-footer { display: flex; align-items: center; justify-content: space-between; gap: 20px; }
.auth-brand { display: flex; gap: 13px; align-items: center; text-decoration: none; color: inherit; font-size: 20px; font-weight: 700; letter-spacing: 1px; }
.auth-brand small { display: block; font-size: 9px; font-weight: 500; letter-spacing: 2.6px; color: var(--color-text-muted); margin-top: 1px; }
.prism-mark { color: var(--color-primary); font-size: 42px; line-height: 1; }
.header-caption { color: var(--color-text-secondary); font-size: 13px; }
.auth-layout { width: 100%; max-width: 1160px; margin: auto; padding: 58px 0 48px; display: grid; grid-template-columns: 1.15fr 1fr; gap: clamp(48px, 7vw, 112px); align-items: center; }
.intro-eyebrow { display: flex; align-items: center; gap: 9px; color: var(--color-primary); font-size: 13px; letter-spacing: 2px; margin-bottom: 24px; }
.intro-eyebrow span { width: 7px; height: 7px; border-radius: 50%; background: var(--color-primary); }
h1 { font-size: clamp(34px, 4.1vw, 57px); font-weight: 600; line-height: 1.35; letter-spacing: -2px; }
.title-dot { color: var(--color-primary); }
.intro-description { color: var(--color-text-secondary); font-size: 15px; line-height: 1.9; margin-top: 24px; }
.workflow-preview { border: 1px solid var(--color-border); border-radius: 18px; background: #ffffff60; margin-top: 34px; padding: 18px 24px 9px; }
.preview-top { display: flex; justify-content: space-between; align-items: center; color: var(--color-text-muted); font-size: 9px; letter-spacing: 1.4px; padding-bottom: 10px; }
.preview-tag { background: var(--color-primary-soft); color: var(--color-primary); font-size: 10px; padding: 3px 8px; border-radius: 5px; letter-spacing: 0; }
.workflow-row { display: flex; align-items: center; gap: 18px; padding: 16px 0; border-top: 1px solid var(--color-border-light); }
.step-number { font-size: 12px; color: var(--color-text-muted); font-variant-numeric: tabular-nums; }
.workflow-row strong { font-size: 14px; font-weight: 600; }
.workflow-row p { font-size: 11px; color: var(--color-text-secondary); margin-top: 4px; }
.step-symbol { margin-left: auto; color: var(--color-primary); }
.intro-footnote { font-size: 11px; letter-spacing: 1px; color: var(--color-text-muted); margin: 18px 0 0; }
.auth-card { background: var(--color-bg-secondary); padding: clamp(28px, 3.4vw, 48px); border: 1px solid var(--color-border-light); border-radius: 24px; box-shadow: 0 24px 60px #37452e0b; }
.auth-tabs { display: flex; padding: 4px; background: var(--color-bg-tertiary); border-radius: 10px; margin-bottom: 32px; }
.auth-tabs button { width: 50%; border: 0; border-radius: 7px; padding: 9px; cursor: pointer; background: transparent; color: var(--color-text-secondary); font-size: 13px; }
.auth-tabs button.active { background: var(--color-bg-secondary); color: var(--color-primary); font-weight: 600; box-shadow: 0 2px 5px #0000000a; }
h2 { font-size: 25px; font-weight: 600; letter-spacing: -.5px; }
.auth-subtitle { color: var(--color-text-muted); font-size: 13px; margin-top: 8px; margin-bottom: 28px; }
label { display: block; font-size: 13px; font-weight: 500; margin: 20px 0 8px; }
input { width: 100%; padding: 13px 14px; border: 1px solid var(--color-border); border-radius: 9px; background: var(--color-bg); color: var(--color-text); font: inherit; font-size: 14px; outline: none; transition: border-color .15s, box-shadow .15s; }
input:focus { border-color: var(--color-primary); box-shadow: 0 0 0 3px var(--color-focus); }
input::placeholder { color: var(--color-text-muted); }
.field-hint { color: var(--color-text-muted); font-size: 10px; margin-top: 7px; }
.password-field { position: relative; }
.password-field input { padding-right: 65px; }
.password-field button { position: absolute; right: 13px; top: 50%; transform: translateY(-50%); border: 0; background: transparent; color: var(--color-text-secondary); font-size: 11px; cursor: pointer; padding: 6px; }
.auth-error { margin-top: 18px; padding: 10px 12px; border: 1px solid #efd4c9; background: #fff3ed; border-radius: 8px; color: #a84e34; font-size: 12px; }
.auth-submit { width: 100%; margin-top: 26px; padding: 14px 18px; border: 0; border-radius: 9px; background: var(--color-primary); color: white; font-size: 14px; font-weight: 500; cursor: pointer; display: flex; align-items: center; justify-content: center; gap: 14px; transition: background .2s; }
.auth-submit:hover { background: var(--color-primary-hover); }
button:disabled { opacity: .65; cursor: wait; }
button:focus-visible, a:focus-visible { outline: 2px solid var(--color-primary); outline-offset: 4px; }
.auth-card-footer { margin-top: 27px; padding-top: 22px; border-top: 1px solid var(--color-border-light); display: flex; gap: 9px; justify-content: center; color: var(--color-text-muted); font-size: 11px; }
.auth-footer { font-size: 11px; color: var(--color-text-muted); border-top: 1px solid var(--color-border); padding-top: 18px; }
@media (max-width: 820px) { .auth-layout { grid-template-columns: 1fr; max-width: 440px; gap: 30px; padding: 36px 0; } .auth-intro h1 { font-size: 34px; letter-spacing: -1px; } .workflow-preview, .intro-footnote { display: none; } .intro-description { font-size: 13px; margin-top: 14px; } .intro-eyebrow { margin-bottom: 13px; } .header-caption { display: none; } .auth-header { max-width: 440px; width: 100%; margin: 0 auto; } .auth-card { padding: 28px; } .auth-footer { max-width: 440px; width: 100%; margin: 0 auto; } .auth-footer span:last-child { display: none; } }
@media (prefers-reduced-motion: reduce) { * { transition: none !important; } }
</style>
