<template>
  <main class="release-page">
    <header class="release-header">
      <div class="header-location">
        <a class="back-link" href="#"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m12 5-7 7 7 7M5 12h14" /></svg>返回采购助手</a>
        <nav class="release-breadcrumb" aria-label="当前位置"><span>采购助手</span><span aria-hidden="true">/</span><strong aria-current="page">提示词发布</strong></nav>
      </div>
      <button class="refresh-button" :disabled="busy" @click="load"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 7v5h-5M4 17v-5h5M6.1 7a7 7 0 0 1 11.6-1L20 9M4 15l2.3 3A7 7 0 0 0 18 17" /></svg>刷新数据</button>
    </header>
    <section class="hero"><div><p class="eyebrow">提示词发布</p><h1>让助手按你的要求回答</h1><p>写下改进要求，测试效果，再决定是否启用。</p></div><button class="help-button" :aria-expanded="showHelp" aria-controls="release-help" @click="showHelp = !showHelp"><span class="help-icon" aria-hidden="true">?</span><span><strong>{{ showHelp ? '收起使用说明' : '使用说明' }}</strong><small>操作步骤与填写示例</small></span><svg viewBox="0 0 24 24" aria-hidden="true" :class="{ expanded: showHelp }"><path d="m9 5 7 7-7 7" /></svg></button></section>
    <div v-if="showHelp" id="release-help" class="notice"><strong>例如，让简单问题回答得更简洁</strong><p>选择“日常问答”，写下“先给结论，通常三句话内回答”，保存后测试。测试通过再让部分用户试用，满意后全面启用。</p><p>保存不会立即改变聊天；工具权限和人工审批仍由系统控制。</p></div>
    <p v-if="error" class="notice error" role="alert">{{ error }}</p><p v-if="notice" class="notice" role="status">{{ notice }}</p>
    <p v-if="loading" role="status">正在读取版本与测试记录…</p>
    <template v-else-if="capabilities.view && state">
      <p v-if="!state.enabled" class="notice">当前可以保存和测试版本，启用新版功能暂未开启。</p>
      <section class="summary" aria-label="当前使用情况">
        <article><small>大家正在使用</small><strong>{{ versionName(state.stable) }}</strong><span>当前正式版本</span></article>
        <article><small>小范围试用</small><strong>{{ state.canary ? `${state.percentage}% 用户试用中` : '尚未开启' }}</strong><span>{{ state.canary ? versionName(state.canary) : '聊天继续使用正式版本' }}</span></article>
        <article><small>发布状态</small><strong>{{ statusText }}</strong><span>{{ state.status === 'ROLLED_BACK' ? '上一次试用已结束，当前使用原有版本' : '先测试，再逐步启用' }}</span></article>
      </section>
      <nav class="steps" aria-label="发布步骤"><a v-for="(step, i) in steps" :key="step.id" href="#" @click.prevent="goTo(step.id)"><b>{{ i + 1 }}</b>{{ step.text }}</a></nav>
      <div class="columns">
        <section id="edit-step" class="panel">
          <div class="section-title"><b>1</b><div><h2>你想让助手怎么改进？</h2><p class="hint">从已有版本开始，保存一份新的回答规则。</p></div></div>
          <label>从哪个版本开始<select v-model="selected" :disabled="busy" @change="changeVersion"><option v-for="v in versions" :key="v.version" :value="v.version">{{ versionOption(v) }}</option></select></label>
          <p v-if="isRevoked" class="warning">这个版本已停止使用，可以修改后保存为新版本，再重新测试。</p>
          <label>想调整哪位助手<select v-model="role"><option v-for="r in roles" :key="r.key" :value="r.key">{{ r.name }}</option></select></label><p class="hint small">{{ roleInfo.description }}</p>
          <label>补充回答要求<textarea v-model="additions[role]" :readonly="!capabilities.maintain" :placeholder="roleInfo.example" aria-label="补充回答要求"></textarea></label>
          <button v-if="capabilities.maintain" class="text-button" @click="additions[role] = roleInfo.example">填入一个示例</button>
          <label>给新版本起个名字<input v-model="note" :readonly="!capabilities.maintain" maxlength="1000" placeholder="例如：简单问题先给结论" /></label>
          <button v-if="capabilities.maintain" class="primary full-button" :disabled="busy || !note.trim() || !dirty" @click="save">保存新版本</button>
          <p class="hint small">{{ dirty ? '保存后才能测试这些改动。' : '先填写改进要求，再保存新版本。' }} 保存不会立即影响聊天。</p>
          <details class="advanced"><summary>查看原有规则与详细修改</summary><p class="hint small">这里是完整规则；熟悉后可直接编辑。</p><textarea v-if="draft" v-model="draft[role]" :readonly="!capabilities.maintain" class="source-editor" aria-label="完整回答规则" spellcheck="false"></textarea><pre>{{ detail?.diff?.[role]?.join('\n') || '与正式版本的原有规则相同' }}</pre></details>
          <div v-if="offset || versions.length >= 20" class="actions"><button :disabled="busy || !offset" @click="page(-20)">上一页</button><button :disabled="busy || versions.length < 20" @click="page(20)">下一页</button></div>
        </section>
        <div>
          <section id="test-step" class="panel">
            <div class="section-title"><b>2</b><div><h2>先看看新版表现</h2><p class="hint">用同一组测试题比较正式版与新版，不会创建真实采购订单。</p></div></div>
            <div class="chosen-version"><small>准备测试</small><strong>{{ versionName(selected) }}</strong></div>
            <button v-if="capabilities.maintain" class="primary" :disabled="!canTest" @click="pair">{{ pairRunning ? '正在测试…' : hasPair ? '重新测试' : '开始测试' }}</button><p class="hint small">{{ testHint }}</p>
            <div v-if="hasPair" class="test-progress" aria-live="polite">
              <div v-for="(run, arm) in runs" :key="arm" class="run-row"><div><strong>{{ arm === 'stable' ? '正式版本' : '准备启用的新版' }}</strong><span>{{ runText(run) }}</span></div><progress :value="run?.completed || 0" :max="run?.total || 1" :aria-label="`${arm === 'stable' ? '正式版' : '新版'}测试进度`"></progress><small v-if="run">已完成 {{ run.completed }}{{ run.total ? ` / ${run.total}` : '' }} 项 · {{ run.passed }} 项通过<span v-if="run.failed"> · {{ run.failed }} 项未通过</span></small></div>
              <p v-if="progressError" class="warning">{{ progressError }} <button class="text-button" @click="retryProgress">重试读取</button></p><p v-else class="hint small">{{ pairRunning ? '进度自动更新，可以离开页面，稍后回来查看。' : '测试记录已保存。点击下方按钮查看是否达到试用要求。' }}</p>
            </div>
            <button v-if="capabilities.maintain && hasPair" :disabled="busy || !pairFinished || !!progressError" @click="checkGate">查看测试结论</button>
            <div v-if="gate" class="gate" :class="{ passed: gate.passed }" role="status"><strong>{{ gate.passed ? '测试达标，可以小范围试用' : '暂时不能试用' }}</strong><p v-if="gate.passed">质量、安全和响应速度已达到系统要求。{{ gateExpired ? '结论已过期，请重新检查。' : '接下来可以在第 3 步开启试用。' }}</p><p v-for="reason in gate.reasons" :key="reason">{{ friendlyReason(reason) }}</p><small>结论有效至 {{ formatTime(gate.expiresAt) }}</small></div>
          </section>
          <section id="trial-step" class="panel">
            <div class="section-title"><b>3</b><div><h2>先试用，再决定</h2><p class="hint">先让一小部分用户体验新版，其余用户继续使用正式版。</p></div></div>
            <template v-if="capabilities.maintain">
              <label>让多少用户试用<select v-model.number="percentage"><option :value="5">5% · 更小范围</option><option :value="10">10% · 推荐开始比例</option><option :value="25">25% · 扩大试用范围</option><option v-if="![5,10,25].includes(percentage)" :value="percentage">{{ percentage }}%</option></select></label>
              <details class="advanced"><summary>只邀请指定账号试用（可选）</summary><label>试用账号<input v-model="allowlist" placeholder="多个账号用英文逗号分隔" /></label><p class="hint small">只在这些账号中按上述比例选择用户，并非每个受邀账号都会命中。留空则面向所有用户。</p></details>
              <button class="primary" :disabled="!canTrial" @click="publish('canary')">{{ state.canary === selected ? '调整试用范围' : '开启小范围试用' }}</button><p class="hint small">{{ trialHint }}</p>
            </template>
            <div v-if="state.canary" class="trial-observation"><strong>试用效果还需要多少样本？</strong><div v-for="arm in ['stable','canary']" :key="arm" class="sample-row"><span>{{ arm === 'stable' ? '正式版本' : '试用版本' }}</span><b>{{ observations?.cohorts?.[arm]?.completedCount || 0 }} / 20 条</b></div><p class="hint small">{{ enoughSamples ? '两组都已达到最低样本数，可结合下方表现决定是否全面启用。' : '两组各需至少 20 条完整请求。现在可以回到聊天页继续使用，稍后再来查看。' }}</p></div>
            <div v-if="capabilities.maintain" class="actions"><button v-if="state.canary" :disabled="busy || !state.enabled || !enoughSamples" @click="publish('promote')">全面启用试用版</button><button v-if="state.canary || state.previousStable" class="danger" :disabled="busy || !state.enabled" @click="publish('rollback')">恢复原有版本</button></div><p v-if="state.canary || state.previousStable" class="hint small">恢复后，新请求使用原有版本；正在执行或等待确认的任务仍按原规则完成。</p>
          </section>
        </div>
      </div>
      <section class="panel"><h2>试用后的实际表现</h2><p class="hint">过去 24 小时。请求完成率表示是否正常结束，不代表回答准确率。</p><div class="table-scroll"><table><thead><tr><th>版本</th><th>完整请求</th><th>请求完成率</th><th>通常响应时间</th><th>较慢请求耗时</th></tr></thead><tbody><tr v-for="arm in ['stable','canary']" :key="arm"><td>{{ arm === 'stable' ? '正式版本' : '试用版本' }}</td><td>{{ observations?.cohorts?.[arm]?.completedCount || 0 }} <small v-if="!observations?.cohorts?.[arm]?.sufficient">还需积累样本</small></td><td>{{ percent(observations?.cohorts?.[arm]?.successRate) }}</td><td>{{ seconds(observations?.cohorts?.[arm]?.p50Seconds) }}</td><td>{{ seconds(observations?.cohorts?.[arm]?.p95Seconds) }}</td></tr></tbody></table></div><p class="hint small">通常响应时间为 P50，较慢请求耗时为 P95；均不包含输入安全检查。</p><p v-for="item in observations?.pendingApprovals" :key="item.version" class="warning">{{ versionName(item.version) }}还有 {{ item.count }} 个会话等待确认，恢复时继续使用原有规则。</p></section>
      <section class="panel history-panel"><h2>最近的发布记录</h2><p v-if="!history.events?.length" class="hint">还没有发布操作。保存并测试一个新版本后，就可以开始试用。</p><ol><li v-for="event in history.events" :key="event.event_id"><strong>{{ eventName(event.action) }}</strong><span>{{ event.actor }} · {{ formatTime(event.created_at) }}</span></li></ol>
        <details class="advanced"><summary>高级详情：测试编号、版本信息与审计</summary><dl><dt>当前正式版编号</dt><dd>{{ state.stable }}</dd><dt>所选版本编号</dt><dd>{{ selected }}</dd></dl><label>正式版测试编号<input v-model="pairIds.stableRunId" @input="manualPairChanged" /></label><label>新版测试编号<input v-model="pairIds.candidateRunId" @input="manualPairChanged" /></label><button :disabled="busy || !hasPair" @click="retryProgress">读取指定测试进度</button><pre>{{ JSON.stringify({ state, gate, runs, history, observations }, null, 2) }}</pre></details>
      </section>
    </template>
    <p v-else-if="!loading && !error" class="notice">当前账号没有提示词查看权限，请联系管理员开通。</p>
  </main>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { apiFetch } from '../api/http.js'
const steps = [{ id: 'edit-step', text: '写下改进要求' }, { id: 'test-step', text: '测试新版效果' }, { id: 'trial-step', text: '试用并决定启用' }]
const roles = [
  { key: 'lightweight', name: '日常问答', description: '负责简单问题、概念解释和只读查询。', example: '简单问题先给结论，通常控制在三句话以内；用户要求详细解释时再展开。' },
  { key: 'main', name: '任务总协调', description: '负责理解需求，并把任务交给合适的专业助手。', example: '理解需求后直接处理，最后给出清晰结论，避免重复播报执行步骤。' },
  { key: 'procurement-analyst', name: '采购分析', description: '负责供应商比价、推荐理由和风险分析。', example: '先给出推荐方案，再用表格列出价格、交期和主要风险；数据不足时明确说明。' },
  { key: 'procurement-order', name: '订单处理', description: '负责采购订单操作，仍须遵守人工审批。', example: '提交审批前清楚展示物料、数量和总价；缺少必要信息时先询问。' },
  { key: 'procurement-orchestrator', name: '采购全流程', description: '负责从需求分析到受控下单的完整采购任务。', example: '最终汇总推荐方案、审批结果和订单状态，不重复展示工具原始数据。' },
]
const state = ref(null), capabilities = ref({}), versions = ref([]), selected = ref(''), role = ref('lightweight'), detail = ref(null), draft = ref(null), additions = ref({}), note = ref(''), gate = ref(null), history = ref({}), observations = ref(null)
const busy = ref(false), loading = ref(true), error = ref(''), notice = ref(''), showHelp = ref(false), offset = ref(0), percentage = ref(10), allowlist = ref(''), pairIds = ref({ stableRunId: '', candidateRunId: '' }), runs = ref({ stable: null, candidate: null }), progressError = ref('')
let timer, disposed = false, selectionEpoch = 0, progressEpoch = 0
const roleInfo = computed(() => roles.find(r => r.key === role.value))
const dirty = computed(() => !!draft.value && (Object.values(additions.value).some(v => v?.trim()) || Object.keys(draft.value).some(r => draft.value[r] !== detail.value?.roles?.[r])))
const isRevoked = computed(() => state.value?.revoked?.includes(selected.value))
const hasPair = computed(() => !!pairIds.value.stableRunId && !!pairIds.value.candidateRunId)
const terminal = run => !!run && ['COMPLETED', 'FAILED', 'CANCELLED'].includes(run.status)
const pairFinished = computed(() => terminal(runs.value.stable) && terminal(runs.value.candidate))
const pairRunning = computed(() => hasPair.value && !pairFinished.value)
const gateExpired = computed(() => gate.value?.expiresAt && new Date(utc(gate.value.expiresAt)).getTime() <= Date.now())
const matchingRuns = computed(() => runs.value.candidate?.promptVersion === selected.value && runs.value.stable?.promptVersion === state.value?.stable)
const canTest = computed(() => capabilities.value.maintain && !busy.value && selected.value && selected.value !== state.value?.stable && !dirty.value && !isRevoked.value && !pairRunning.value)
const enoughSamples = computed(() => ['stable', 'canary'].every(arm => observations.value?.cohorts?.[arm]?.sufficient))
const canTrial = computed(() => capabilities.value.maintain && !busy.value && state.value?.enabled && gate.value?.passed && !gateExpired.value && gate.value.candidateVersion === selected.value && matchingRuns.value && !dirty.value && !isRevoked.value && (!state.value.canary || state.value.canary === selected.value))
const statusText = computed(() => ({ STABLE: '正式版正常使用', CANARY: '正在小范围试用', PROMOTED: '新版已全面启用', ROLLED_BACK: '已恢复原有版本' }[state.value?.status] || '等待设置'))
const testHint = computed(() => dirty.value ? '有未保存的改动，请先保存新版本。' : isRevoked.value ? '已停止使用的版本需要修改并保存为新版本，才能重新试用。' : selected.value === state.value?.stable ? '当前选择的是正式版本，请先在左侧保存一个有改动的新版本。' : pairRunning.value ? '系统会分别测试两个版本，通常需要几分钟。无需填写任何测试编号。' : '每组测试会实际调用模型，完成后在这里查看结论。')
const trialHint = computed(() => !state.value?.enabled ? '启用新版功能暂未开启。' : isRevoked.value ? '该版本已停止使用，请先修改并保存一个新版本。' : dirty.value ? '请先保存并测试当前改动。' : state.value?.canary && state.value.canary !== selected.value ? '已有其他版本正在试用，可先恢复原有版本，或选择正在试用的版本。' : !gate.value?.passed ? '先完成第 2 步，测试达标后即可开启试用。' : gateExpired.value ? '测试结论已过期，请重新检查。' : '开启后按用户固定分配版本。同一用户不会每次随机切换。')
async function request(path, body) { return (await apiFetch(`/api/prompt-releases/${path}`, body ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : {})).json() }
function versionName(id) { if (!id) return '尚未选择'; if (id === 'builtin-v1') return '初始版本'; return versions.value.find(v => v.version === id)?.change_note || '已保存版本' }
function versionOption(v) { return `${v.version === state.value?.stable ? '正在使用 · ' : v.version === state.value?.canary ? '正在试用 · ' : state.value?.revoked?.includes(v.version) ? '已停止 · ' : ''}${v.version === 'builtin-v1' ? '初始版本' : v.change_note}` }
function resetPair() { clearTimeout(timer); progressEpoch++; pairIds.value = { stableRunId: '', candidateRunId: '' }; runs.value = { stable: null, candidate: null }; gate.value = null; progressError.value = '' }
async function selectVersion(force = false) {
  if (!selected.value || (!force && detail.value?.version === selected.value)) return
  const epoch = ++selectionEpoch, id = selected.value
  const data = await request(`versions/${encodeURIComponent(id)}?compareTo=${encodeURIComponent(state.value.stable)}`)
  if (disposed || epoch !== selectionEpoch || id !== selected.value) return
  detail.value = data; draft.value = { ...data.roles }; additions.value = {}; note.value = ''; resetPair()
  const prior = history.value.audit?.find(e => e.action === 'PAIR_CREATE' && e.outcome === 'SUCCESS')?.metadata
  if (prior?.stableRunId && prior?.candidateRunId) {
    try {
      const summaries = await Promise.all([request(`evaluations/${encodeURIComponent(prior.stableRunId)}`), request(`evaluations/${encodeURIComponent(prior.candidateRunId)}`)])
      if (disposed || epoch !== selectionEpoch || id !== selected.value) return
      if (summaries[1].promptVersion === id && summaries[0].promptVersion === state.value.stable) { pairIds.value = { stableRunId: prior.stableRunId, candidateRunId: prior.candidateRunId }; runs.value = { stable: summaries[0], candidate: summaries[1] }; restoreGate(); scheduleProgress() }
    } catch (cause) { if (epoch === selectionEpoch) progressError.value = `历史测试暂时无法读取：${cause.message}` }
  }
}
function restoreGate() { const stored = history.value.gates?.find(g => g.evidence?.stableRunId === pairIds.value.stableRunId && g.evidence?.candidateRunId === pairIds.value.candidateRunId); gate.value = stored ? { ...stored.evidence, gateId: stored.gate_id, candidateVersion: stored.candidate_version, expiresAt: stored.expires_at } : null }
async function refresh() {
  const [nextState, list, nextHistory, nextObservations] = await Promise.all([request('state'), request(`versions?limit=20&offset=${offset.value}`), request('history'), request('observations')])
  if (disposed) return
  state.value = nextState; versions.value = list.items; history.value = nextHistory; observations.value = nextObservations
  if (!selected.value) {
    const latest = nextHistory.audit?.find(e => e.action === 'PAIR_CREATE' && e.outcome === 'SUCCESS')?.metadata
    if (latest?.candidateRunId) {
      try { const run = await request(`evaluations/${encodeURIComponent(latest.candidateRunId)}`); if (!disposed && !nextState.revoked?.includes(run.promptVersion) && versions.value.some(v => v.version === run.promptVersion)) selected.value = run.promptVersion } catch { /* Version editing remains available when old progress cannot be read. */ }
    }
  }
  if (!versions.value.some(v => v.version === selected.value)) selected.value = versions.value.some(v => v.version === nextState.canary) ? nextState.canary : versions.value.some(v => v.version === nextState.stable) ? nextState.stable : versions.value[0]?.version || ''
  await selectVersion()
}
async function act(operation) { if (busy.value) return; busy.value = true; error.value = ''; try { await operation() } catch (cause) { error.value = cause.message; if (cause.status === 409) await refresh().catch(() => {}) } finally { busy.value = false } }
async function load() { await act(async () => { capabilities.value = await request('capabilities'); if (!capabilities.value.view) { state.value = null; resetPair(); return } await refresh(); if (hasPair.value) await updateProgress() }); loading.value = false }
function changeVersion() { resetPair(); notice.value = ''; act(() => selectVersion(true)) }
function page(delta) { act(async () => { offset.value += delta; await refresh() }) }
function save() { act(async () => { const combined = Object.fromEntries(Object.entries(draft.value).map(([r, text]) => [r, additions.value[r]?.trim() ? `${text}\n\n${additions.value[r].trim()}` : text])); const result = await request('versions', { roles: combined, note: note.value }); selected.value = result.version; offset.value = 0; notice.value = '新版本已保存，聊天仍使用原有版本。接下来点击“开始测试”。'; await refresh() }) }
function pair() { act(async () => { const ids = await request('pairs', { candidateVersion: selected.value }); resetPair(); pairIds.value = ids; notice.value = '测试已开始排队，进度会自动更新。完成后查看测试结论即可。'; await updateProgress() }) }
function scheduleProgress() { clearTimeout(timer); if (!disposed && pairRunning.value && !progressError.value) timer = setTimeout(async () => { if (document.visibilityState === 'hidden') { scheduleProgress(); return } await updateProgress() }, 4000) }
async function updateProgress() {
  if (!hasPair.value || disposed) return
  const epoch = ++progressEpoch, ids = { ...pairIds.value }
  try { const [stable, candidate] = await Promise.all([request(`evaluations/${encodeURIComponent(ids.stableRunId)}`), request(`evaluations/${encodeURIComponent(ids.candidateRunId)}`)]); if (disposed || epoch !== progressEpoch) return; runs.value = { stable, candidate }; progressError.value = ''; if (!matchingRuns.value) { progressError.value = '这组测试不属于当前所选版本，请选择对应版本或重新测试。'; gate.value = null } } catch (cause) { if (!disposed && epoch === progressEpoch) progressError.value = `暂时无法读取测试进度：${cause.message}` }
  scheduleProgress()
}
function retryProgress() { act(updateProgress) }
function manualPairChanged() { clearTimeout(timer); progressEpoch++; runs.value = { stable: null, candidate: null }; gate.value = null; progressError.value = '' }
function checkGate() { act(async () => { gate.value = await request('gates', pairIds.value); await refresh() }) }
const keys = new Map()
function publish(action) { act(async () => { const payload = { expectedRevision: state.value.revision, gateId: gate.value?.gateId || null, percentage: percentage.value, allowlist: allowlist.value.split(',').map(v => v.trim()).filter(Boolean) }; const signature = JSON.stringify([action, payload]); if (!keys.has(signature)) keys.set(signature, crypto.randomUUID()); await request(action, { ...payload, idempotencyKey: keys.get(signature) }); notice.value = { canary: '小范围试用已开启。回到聊天页使用后，可以在这里查看实际表现。', promote: '新版已全面启用。', rollback: '已恢复原有版本，后续普通请求使用原有规则。' }[action]; await refresh() }) }
function goTo(id) { document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' }) }
const utc = value => /(?:Z|[+-]\d{2}:?\d{2})$/i.test(value) ? value : `${value}Z`
const formatTime = value => value ? new Date(utc(value)).toLocaleString() : '—'
const seconds = value => value == null ? '暂无数据' : `${value.toFixed(2)} 秒`
const percent = value => value == null ? '暂无数据' : `${(value * 100).toFixed(1)}%`
const eventName = action => ({ canary: '开启或调整了小范围试用', promote: '全面启用了新版', rollback: '恢复了原有版本' }[action.toLowerCase()] || action)
const runText = run => !run ? '正在读取进度' : ({ QUEUED: '排队中', RUNNING: '正在测试', COMPLETED: '测试已结束', FAILED: ({ budget_exceeded: '已停止：超出测试预算', timeout: '已停止：测试超时' }[run.reason] || '测试未完成'), CANCELLED: '测试已取消' }[run.status] || '状态待确认')
function friendlyReason(reason) { return reason.replace(/^stable: /, '正式版本：').replace(/^candidate: /, '新版：').replaceAll('候选', '新版').replaceAll('P95', '较慢请求耗时').replaceAll('worker', '测试服务').replaceAll('部署指纹已变化', '系统配置已更新，请重新测试').replaceAll('模型调用或 Token 超出评测预算', '测试消耗超过上限，请调整规则后重试') }
onMounted(load)
onBeforeUnmount(() => { disposed = true; selectionEpoch++; progressEpoch++; clearTimeout(timer) })
</script>

<style scoped>
.release-page,.release-page *{box-sizing:border-box}
.release-page{min-height:100dvh;padding:24px clamp(18px,4vw,64px) 48px;background:#f3f6f8;color:#243b48;font:15px/1.65 "Segoe UI","Microsoft YaHei UI","Microsoft YaHei","PingFang SC",sans-serif;font-synthesis:none}
.release-page>*{max-width:1536px;margin-inline:auto}.release-page a{color:#11765f}
.release-header{display:flex;align-items:center;justify-content:space-between;gap:20px;padding-bottom:20px;border-bottom:1px solid #dce5e9}
.header-location{display:flex;align-items:center;gap:24px;min-width:0}
.release-page .back-link{display:inline-flex;align-items:center;gap:10px;min-height:44px;padding:10px 16px;background:#fff;border:1px solid #aecfc4;border-radius:10px;color:#126b57;font-size:15px;font-weight:600;text-decoration:none;white-space:nowrap;box-shadow:0 2px 3px #183d4710}
.release-page .back-link:hover{background:#e8f4ef;border-color:#16866d}
.release-header svg,.help-button svg{width:20px;height:20px;fill:none;stroke:currentColor;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round;flex-shrink:0}
.release-breadcrumb{display:flex;align-items:center;gap:12px;font-size:14px;color:#536873}.release-breadcrumb>span:nth-child(2){color:#8497a1}.release-breadcrumb strong{color:#243b48;font-weight:600}
.hero{display:flex;align-items:center;justify-content:space-between;gap:28px;padding:30px 0 26px}.hero h1{font-size:clamp(27px,2.5vw,34px);line-height:1.4;margin:6px 0 12px;font-weight:600;letter-spacing:0}.hero p{margin:0;color:#536873;line-height:1.8}.hero .eyebrow{color:#11765f;font-size:14px;font-weight:600}
.release-page button{padding:11px 17px;min-height:44px;border:1px solid #bdcdd4;background:#fff;border-radius:9px;color:#304b58;cursor:pointer;font-family:inherit;font-size:14px;font-weight:600;line-height:1.5;margin:4px 4px 4px 0;transition:background .15s,border-color .15s}
.release-page button:hover:enabled{background:#f0f5f6;border-color:#8aa8a3}
.release-page .refresh-button{display:inline-flex;align-items:center;justify-content:center;gap:8px;margin:0;white-space:nowrap}
.release-page .help-button{display:flex;align-items:center;gap:13px;padding:15px 18px;margin:0;min-width:246px;background:#e6f3ed;border:1px solid #a5cbbc;text-align:left;color:#16654f;border-radius:12px}
.release-page .help-button:hover{background:#d9eee3;border-color:#479b7e}.help-icon{display:grid;place-items:center;width:34px;height:34px;border-radius:50%;background:#fff;border:1px solid #a5cbbc;font-size:20px;font-weight:600}.help-button strong{display:block;font-size:16px}.help-button small{display:block;color:#416c5d;font-size:13px;font-weight:400;margin-top:3px}.help-button svg{margin-left:auto;transition:transform .15s}.help-button svg.expanded{transform:rotate(90deg)}
.hint{color:#536873;line-height:1.8;margin:8px 0 12px}.small{font-size:14px}.notice{padding:18px 22px;margin-bottom:20px;border:1px solid #c4dfd2;border-radius:12px;background:#e7f3ed;line-height:1.8}.notice p{margin:8px 0 0}.notice.error{background:#fff0e6;border-color:#efd3ba;color:#8e3e29}
.summary{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px}.summary article{display:flex;flex-direction:column;gap:9px;padding:22px 24px;border-radius:14px;background:#193d48;color:white;min-width:0}.summary strong{font-size:22px;font-weight:600;overflow-wrap:anywhere}.summary small,.summary span{color:#d0e2e5;font-size:14px;line-height:1.6}
.steps{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;padding:10px;margin-top:22px;margin-bottom:22px;background:#fff;border:1px solid #dce5e9;border-radius:12px}.steps a{display:flex;align-items:center;gap:11px;padding:10px 12px;text-decoration:none;font-size:15px;font-weight:600}.steps a:hover{background:#edf5f1;border-radius:8px}.steps b,.section-title>b{display:grid;place-items:center;width:32px;height:32px;flex:0 0 32px;border-radius:50%;background:#e4f1eb;color:#11694f;font-size:14px}
.columns{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.12fr);gap:22px;align-items:start}.columns>div{min-width:0}.panel{padding:26px;background:#fff;border:1px solid #dce5e9;border-radius:14px;margin-bottom:22px;min-width:0;scroll-margin-top:20px}.section-title{display:flex;gap:12px;align-items:flex-start;margin-bottom:20px}.section-title .hint{margin:6px 0 0;font-size:14px}.panel h2{margin:0;font-size:20px;font-weight:600;line-height:1.5}
.release-page label{display:grid;gap:9px;font-size:15px;font-weight:600;margin:20px 0 10px}.release-page input,.release-page select,.release-page textarea{width:100%;min-width:0;max-width:100%;padding:12px 14px;border:1px solid #bdcdd4;border-radius:9px;background:#fcfdfd;color:#243b48;font-family:inherit;font-size:15px;font-weight:400;line-height:1.7}.release-page input::placeholder,.release-page textarea::placeholder{color:#637581}.release-page input:focus,.release-page select:focus,.release-page textarea:focus{outline:2px solid #68ae95;outline-offset:1px}.release-page textarea{min-height:150px;resize:vertical;line-height:1.85}.release-page textarea.source-editor{min-height:320px;font:14px/1.8 Consolas,"Microsoft YaHei UI",monospace}
.release-page button.primary{background:#11775f;color:#fff;border-color:#11775f}.release-page button.primary:hover:enabled{background:#0d604c}.release-page button.danger{color:#a33830}.release-page button:disabled{background:#edf1f3;border-color:#d3dde1;color:#617580;cursor:not-allowed}.release-page button.full-button{width:100%;margin-top:10px}.release-page button.text-button{padding:4px 0;min-height:36px;margin:0;background:none;border:0;color:#11765f;font-size:14px;font-weight:500;text-decoration:underline;text-underline-offset:4px}.release-page button:focus-visible,.release-page a:focus-visible,.release-page summary:focus-visible{outline:3px solid #13836e;outline-offset:3px}
.advanced{margin:18px 0;padding-top:16px;border-top:1px solid #dfe7ea}.advanced summary{cursor:pointer;color:#4e6772;font-size:14px;line-height:1.8}.chosen-version{display:flex;flex-direction:column;gap:6px;padding:16px 18px;margin-bottom:14px;border-radius:10px;background:#f0f6f3}.chosen-version small{color:#536873;font-size:13px}.chosen-version strong{font-size:16px;font-weight:600;overflow-wrap:anywhere}
.test-progress{border:1px solid #dce5e2;border-radius:10px;padding:18px;margin:16px 0}.run-row+.run-row{margin-top:18px}.run-row>div{display:flex;justify-content:space-between;gap:12px;font-size:14px}.run-row>div>span{color:#536873}.run-row progress{width:100%;height:8px;accent-color:#13836e;margin:12px 0 6px;display:block;border:0;border-radius:5px;overflow:hidden}.run-row progress::-webkit-progress-bar{background:#e7efed}.run-row progress::-webkit-progress-value{background:#13836e}.run-row small{color:#536873;font-size:13px}
.gate{padding:18px;border-radius:10px;margin-top:16px;background:#fff0e6;color:#8e3e29;line-height:1.8}.gate.passed{background:#e3f2e9;color:#16604b}.gate p{font-size:14px;margin:6px 0}.gate small{font-size:13px}.warning{font-size:14px;line-height:1.8;color:#875226;background:#fff5e5;padding:12px 14px;border-radius:8px}.trial-observation{border-top:1px solid #dfe7ea;margin-top:20px;padding-top:20px}.trial-observation>strong{font-size:15px}.sample-row{display:flex;justify-content:space-between;gap:12px;padding-top:12px;font-size:14px}.sample-row b{color:#11765f}.actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:14px}
.table-scroll{overflow:auto}.release-page table{width:100%;border-collapse:collapse;font-size:15px;white-space:nowrap;font-variant-numeric:tabular-nums}.release-page th,.release-page td{text-align:left;padding:16px 14px;border-bottom:1px solid #dfe7ea}.release-page th{font-size:14px;color:#4d6470;font-weight:600;background:#f7f9fa}.release-page td small{display:block;color:#875226;font-size:13px;margin-top:5px}.release-page pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:360px;overflow:auto;font:13px/1.8 Consolas,"Microsoft YaHei UI",monospace;background:#f5f8f7;padding:16px;border-radius:8px}.release-page dd{margin:4px 0 14px;font-size:14px;overflow-wrap:anywhere}.release-page dt{color:#536873;font-size:14px}.history-panel ol{padding:0;list-style:none}.history-panel li{display:flex;justify-content:space-between;gap:16px;padding:14px 0;border-bottom:1px solid #e7edef;font-size:14px;line-height:1.8}.history-panel li span{color:#536873}
@media(max-width:1000px){.columns{grid-template-columns:minmax(0,1fr)}.release-breadcrumb{display:none}.summary{gap:12px}.summary article{padding:20px}.summary strong{font-size:20px}.steps a{gap:8px;padding-inline:8px;font-size:14px}}
@media(max-width:600px){.release-page{padding-top:18px}.release-header{gap:12px;padding-bottom:16px}.release-page .back-link{padding-inline:12px;font-size:14px;gap:7px}.release-page .refresh-button{padding-inline:12px;font-size:14px}.hero{align-items:stretch;flex-direction:column;gap:20px;padding:24px 0}.hero h1{font-size:27px}.release-page .help-button{width:100%;min-width:0;padding:13px 16px}.summary{grid-template-columns:minmax(0,1fr)}.summary article{gap:5px}.steps{grid-template-columns:minmax(0,1fr);gap:0}.steps a{padding:9px 12px}.panel{padding:22px 18px}.panel h2{font-size:19px}.history-panel li{flex-direction:column;gap:3px}}
@media(prefers-reduced-motion:reduce){.release-page button,.help-button svg{transition:none}}
</style>
