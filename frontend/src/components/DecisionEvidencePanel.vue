<template>
  <aside class="evidence-panel" aria-label="决策证据中心">
    <header>
      <div><small>DECISION EVIDENCE</small><h2>采购决策证据</h2></div>
      <button type="button" aria-label="关闭决策证据" @click="$emit('close')">×</button>
    </header>
    <p v-if="message" class="panel-state">{{ message }}</p>
    <template v-else>
      <section class="evidence-meta">
        <span>{{ detail.decision.policyVersion }}</span>
        <code>{{ detail.decision.decisionId }}</code>
      </section>
      <section>
        <h3>生命周期</h3>
        <ol class="timeline">
          <li v-for="event in detail.lifecycle" :key="event.eventId">
            <strong>{{ event.eventType }}</strong><time>{{ event.createdAt || '时间未知' }}</time>
          </li>
        </ol>
      </section>
      <section>
        <h3>候选解释</h3>
        <article v-for="candidate in candidates" :key="candidate.partId" class="candidate-card">
          <div><b>#{{ candidate.rank }} {{ candidate.partName }}</b><span :class="candidate.eligible ? 'eligible' : 'rejected'">{{ candidate.eligible ? '合格' : '淘汰' }}</span></div>
          <p>{{ candidate.supplierName }} · 总分 {{ candidate.totalScore ?? '—' }}</p>
          <ul><li v-for="line in [...candidate.explanation, ...candidate.reasons]" :key="line">{{ line }}</li></ul>
        </article>
      </section>
      <section>
        <h3>授权关联</h3>
        <dl class="links">
          <div v-for="link in links" :key="link.key"><dt>{{ link.label }}</dt><dd :class="link.status">{{ link.value || linkStatus(link.status) }}</dd></div>
        </dl>
      </section>
      <section class="replay-box">
        <div><h3>策略回放</h3><span>模拟 · 不触发审批或下单</span></div>
        <select v-model="selectedPolicy" aria-label="回放策略">
          <option v-for="policy in policies" :key="policy.version" :value="policy.version">{{ policy.version }}</option>
        </select>
        <button type="button" :disabled="replaying || !selectedPolicy" @click="runReplay">{{ replaying ? '回放中…' : '运行模拟回放' }}</button>
        <p v-if="replayError" class="replay-error">{{ replayError }}</p>
        <div v-if="replayView" class="replay-result">
          <b>{{ replayView.label }}</b>
          <p>策略 {{ replayView.policyVersion }} · 推荐{{ replayView.recommendation?.changed ? '发生变化' : '保持一致' }}</p>
          <ul><li v-for="item in replayView.candidates" :key="item.partId">物料 {{ item.partId }}：资格{{ item.eligibility?.changed ? '变化' : '不变' }}，排名{{ item.rank?.changed ? '变化' : '不变' }}</li></ul>
        </div>
      </section>
    </template>
  </aside>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { getDecision, getReplayPolicies, replayDecision } from '../api/decisions.js'
import { candidateRows, drillDowns, panelMessage, replayPresentation } from '../utils/decisionEvidence.js'

const props = defineProps({ decisionId: { type: String, required: true } })
defineEmits(['close'])
const detail = ref(null)
const loading = ref(true)
const error = ref(null)
const policies = ref([])
const selectedPolicy = ref('')
const replay = ref(null)
const replaying = ref(false)
const replayError = ref('')
const message = computed(() => panelMessage({ loading: loading.value, error: error.value, detail: detail.value }))
const candidates = computed(() => candidateRows(detail.value))
const links = computed(() => drillDowns(detail.value))
const replayView = computed(() => replayPresentation(replay.value))
const linkStatus = status => ({ missing: '暂无关联', pending: '待补充', expired: 'Trace 已过期' }[status] || status)

async function load() {
  loading.value = true; error.value = null; replay.value = null
  try {
    const [loaded, policyList] = await Promise.all([getDecision(props.decisionId), getReplayPolicies()])
    detail.value = loaded
    policies.value = policyList.items || []
    selectedPolicy.value = detail.value.decision.policyVersion || policies.value[0]?.version || ''
  } catch (caught) { error.value = caught } finally { loading.value = false }
}
async function runReplay() {
  replaying.value = true; replayError.value = ''
  try { replay.value = await replayDecision(props.decisionId, selectedPolicy.value) }
  catch (caught) { replayError.value = caught.message }
  finally { replaying.value = false }
}
onMounted(load)
watch(() => props.decisionId, load)
</script>

<style scoped>
.evidence-panel{width:min(460px,96vw);height:100%;overflow:auto;border-left:1px solid var(--color-border);background:var(--color-bg-secondary);padding:22px;color:var(--color-text);box-shadow:-10px 0 30px #20251e12}.evidence-panel header{display:flex;justify-content:space-between;align-items:flex-start}.evidence-panel h2,.evidence-panel h3{margin:3px 0 12px}.evidence-panel header button{border:0;background:transparent;font-size:28px;cursor:pointer}.evidence-meta{display:flex;justify-content:space-between;gap:8px;padding:10px;border-radius:9px;background:var(--color-bg-tertiary);font-size:12px}.timeline,.candidate-card ul,.replay-result ul{padding-left:20px}.timeline li{display:flex;justify-content:space-between;gap:10px;margin:8px 0}.timeline time{color:var(--color-text-secondary);font-size:12px}.candidate-card{margin:10px 0;padding:12px;border:1px solid var(--color-border);border-radius:10px}.candidate-card>div{display:flex;justify-content:space-between}.candidate-card p,.candidate-card li{font-size:12px;color:var(--color-text-secondary)}.eligible{color:var(--color-primary)}.rejected,.replay-error{color:#a13f35}.links>div{display:flex;justify-content:space-between;margin:7px 0}.links dd{margin:0}.pending,.missing,.expired{color:#bd7a19}.replay-box{padding:14px;border-radius:12px;background:var(--color-primary-soft)}.replay-box>div:first-child{display:flex;justify-content:space-between;align-items:center}.replay-box span{font-size:11px;color:var(--color-primary)}.replay-box select,.replay-box button{width:100%;margin-top:8px;padding:9px}.panel-state{padding:30px 10px;text-align:center;color:var(--color-text-secondary)}
</style>
