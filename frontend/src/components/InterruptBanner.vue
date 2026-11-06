<template>
  <div class="interrupt-banner" :class="bannerClass">
    <!-- ============================================================ -->
    <!-- 类型 1：数据补充中断（request_order_info） -->
    <!-- ============================================================ -->
    <template v-if="interruptData.interrupt_type === 'order_info_supplement'">
      <div class="interrupt-header">
        <span class="interrupt-icon">📋</span>
        <span class="interrupt-title">订单数据不完整，请补充以下信息</span>
      </div>

      <div class="interrupt-body">
        <div class="info-block missing-fields">
          <div class="info-label">缺少字段</div>
          <div class="info-value">{{ interruptData.missing_fields }}</div>
        </div>
        <div class="info-block collected-data" v-if="interruptData.collected_data">
          <div class="info-label">已收集数据</div>
          <pre class="info-pre">{{ formatCollectedData(interruptData.collected_data) }}</pre>
        </div>
      </div>

      <div class="interrupt-input-area">
        <textarea
          ref="supplementInputRef"
          v-model="supplementText"
          placeholder="请用自然语言描述要补充的信息，例如：物料ID=100、数量=50、单价25.5元、预计6月15日交货..."
          class="supplement-textarea"
          rows="3"
          @keydown.enter.exact.prevent="handleSupplement"
        ></textarea>
        <button
          class="submit-btn supplement-btn"
          @click="handleSupplement"
          :disabled="!supplementText.trim() || submitting"
        >
          {{ submitting ? '提交中...' : '提交补充信息' }}
        </button>
      </div>
      <div class="interrupt-hint">按 Enter 提交，Shift + Enter 换行</div>
    </template>

    <!-- ============================================================ -->
    <!-- 类型 2：HITL 审批中断（order_create / order_update） -->
    <!-- ============================================================ -->
    <template v-else-if="interruptData.interrupt_type === 'hitl_approval'">
      <div class="approval-heading">
        <div class="approval-heading-icon">✓</div>
        <div>
          <div class="approval-eyebrow">人工审批节点</div>
          <h2>确认采购方案</h2>
          <p>系统已完成供应商比价与规则校验，请核对业务信息后决定是否创建订单。</p>
        </div>
      </div>

      <div class="approval-body">
        <div
          v-for="(action, idx) in interruptData.action_requests"
          :key="idx"
          class="approval-card"
        >
          <div class="approval-card-header">
            <div>
              <span class="approval-status">等待确认</span>
              <h3>{{ approvalView(action).title }}</h3>
              <p>{{ approvalView(action).subtitle }}</p>
            </div>
            <div class="amount-block">
              <span>订单总额</span>
              <strong>{{ formatMoney(approvalView(action).totalAmount) }}</strong>
              <small v-if="approvalView(action).budget !== null">
                预算 {{ formatMoney(approvalView(action).budget) }} ·
                结余 {{ formatMoney(approvalView(action).budgetRemaining) }}
              </small>
            </div>
          </div>

          <div class="business-grid">
            <div class="business-item business-item-wide">
              <span>推荐供应商</span>
              <strong>{{ approvalView(action).supplier }}</strong>
              <small v-if="approvalView(action).score !== null">综合评分 {{ approvalView(action).score }} 分</small>
            </div>
            <div class="business-item">
              <span>采购数量</span>
              <strong>{{ approvalView(action).quantity || '—' }} <small>片</small></strong>
            </div>
            <div class="business-item">
              <span>预计交付</span>
              <strong>{{ approvalView(action).leadTimeDays !== null ? `${approvalView(action).leadTimeDays} 天` : '—' }}</strong>
              <small v-if="approvalView(action).expectedDate">目标日期 {{ approvalView(action).expectedDate }}</small>
            </div>
          </div>

          <div class="material-panel" v-if="approvalView(action).items.length">
            <div class="section-heading">
              <div>
                <span>采购明细</span>
                <strong>{{ approvalView(action).partName }}</strong>
              </div>
              <span class="line-count">{{ approvalView(action).items.length }} 项</span>
            </div>
            <div class="material-row" v-for="item in approvalView(action).items" :key="item.index">
              <div class="material-index">{{ item.index }}</div>
              <div class="material-name">
                <strong>{{ item.name }}</strong>
                <small>内部物料编号已收起，可在审计信息中查看</small>
              </div>
              <div class="material-metric">
                <span>数量</span>
                <strong>{{ item.quantity }} 片</strong>
              </div>
              <div class="material-metric">
                <span>单价</span>
                <strong>{{ formatMoney(item.unitPrice) }}</strong>
              </div>
              <div class="material-metric material-subtotal">
                <span>小计</span>
                <strong>{{ formatMoney(item.subtotal) }}</strong>
              </div>
            </div>
          </div>

          <div class="rule-checks" v-if="approvalView(action).checks.length">
            <span class="rule-check" v-for="check in approvalView(action).checks" :key="check.label">
              <i>✓</i>{{ check.label }}
            </span>
          </div>

          <details class="audit-details">
            <summary>
              <span>审计与追溯信息</span>
              <small>仅供管理员和问题排查使用</small>
            </summary>
            <dl>
              <div><dt>决策编号</dt><dd>{{ approvalView(action).audit.decisionId }}</dd></div>
              <div><dt>规则版本</dt><dd>{{ approvalView(action).audit.policyVersion }}</dd></div>
              <div><dt>内部物料 ID</dt><dd>{{ approvalView(action).audit.partIds }}</dd></div>
              <div><dt>申请人</dt><dd>{{ approvalView(action).audit.applicant }}</dd></div>
              <div><dt>订单编号</dt><dd>{{ approvalView(action).audit.orderNumber }}</dd></div>
            </dl>
          </details>
        </div>
      </div>

      <div class="approval-impact">
        <span class="impact-icon">i</span>
        <div>
          <strong>确认后的影响</strong>
          <p>仅创建“待审核”采购订单，不会直接向供应商下单、付款或扣减库存；后续状态仍需采购人员在供应链平台审核。</p>
        </div>
      </div>

      <div class="interrupt-actions">
        <button
          class="submit-btn reject-btn"
          @click="handleDecision('reject')"
          :disabled="submitting"
        >
          {{ submitting ? '处理中...' : '暂不创建' }}
        </button>
        <button
          class="submit-btn approve-btn"
          @click="handleDecision('approve')"
          :disabled="submitting"
        >
          {{ submitting ? '正在提交...' : '确认创建待审核订单' }}
        </button>
      </div>
    </template>

    <!-- ============================================================ -->
    <!-- 未知中断类型（透传） -->
    <!-- ============================================================ -->
    <template v-else>
      <div class="interrupt-header">
        <span class="interrupt-icon">⏸️</span>
        <span class="interrupt-title">Agent 已暂停，等待人工介入</span>
      </div>
      <div class="interrupt-body">
        <pre class="info-pre">{{ JSON.stringify(interruptData, null, 2) }}</pre>
      </div>
    </template>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { buildApprovalPresentation, formatMoney } from '../utils/approvalPresentation.js'

const props = defineProps({
  interruptData: { type: Object, required: true },
  submitting: { type: Boolean, default: false }
})

const emit = defineEmits(['resume'])

const supplementText = ref('')
const supplementInputRef = ref(null)

// ============================================================
// 数据补充 — 提交
// ============================================================

function handleSupplement() {
  const text = supplementText.value.trim()
  if (!text || props.submitting) return
  emit('resume', { supplement: text })
}

// ============================================================
// HITL 审批 — 提交决策
// ============================================================

function handleDecision(decisionType) {
  if (props.submitting) return
  emit('resume', { decisions: [{ type: decisionType }] })
}

// ============================================================
// 格式化辅助函数
// ============================================================

function formatCollectedData(data) {
  if (!data) return ''
  try {
    const parsed = typeof data === 'string' ? JSON.parse(data) : data
    return JSON.stringify(parsed, null, 2)
  } catch {
    return String(data)
  }
}

function approvalView(action) {
  return buildApprovalPresentation(action)
}

// CSS class
const bannerClass = {
  'banner-supplement': props.interruptData.interrupt_type === 'order_info_supplement',
  'banner-approval': props.interruptData.interrupt_type === 'hitl_approval'
}
</script>

<style scoped>
.interrupt-banner {
  margin: 16px 24px;
  border-radius: 16px;
  overflow: hidden;
  box-shadow: 0 4px 24px rgba(0, 0, 0, 0.08);
  animation: slideUp 0.3s ease-out;
}

@keyframes slideUp {
  from { opacity: 0; transform: translateY(16px); }
  to { opacity: 1; transform: translateY(0); }
}

.banner-supplement {
  border: 2px solid #f59e0b;
  background: #fffbeb;
}

.banner-approval {
  border: 1px solid #dbe6ef;
  background: linear-gradient(180deg, #f8fbfd 0%, #ffffff 34%);
  box-shadow: 0 18px 50px rgba(15, 49, 71, 0.12);
}

.approval-heading {
  display: flex;
  align-items: flex-start;
  gap: 16px;
  padding: 24px 26px 20px;
  border-bottom: 1px solid #e7eef4;
}

.approval-heading-icon {
  width: 44px;
  height: 44px;
  display: grid;
  place-items: center;
  flex: 0 0 auto;
  border-radius: 14px;
  color: #ffffff;
  font-size: 22px;
  font-weight: 800;
  background: linear-gradient(135deg, #0f766e, #0d9488);
  box-shadow: 0 8px 20px rgba(13, 148, 136, 0.24);
}

.approval-eyebrow {
  margin-bottom: 4px;
  color: #0f766e;
  font-size: 12px;
  font-weight: 800;
  letter-spacing: .12em;
}

.approval-heading h2 {
  margin: 0;
  color: #102a3a;
  font-size: 21px;
  line-height: 1.3;
}

.approval-heading p {
  margin: 6px 0 0;
  color: #667b88;
  font-size: 14px;
  line-height: 1.65;
}

.approval-body { padding: 20px 26px 8px; }

.approval-card {
  overflow: hidden;
  border: 1px solid #dfe8ee;
  border-radius: 16px;
  background: #ffffff;
  box-shadow: 0 8px 24px rgba(25, 54, 72, 0.06);
}

.approval-card-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 24px;
  padding: 22px;
  background: linear-gradient(100deg, #f5faf9 0%, #fbfcfd 100%);
  border-bottom: 1px solid #e5edef;
}

.approval-status {
  display: inline-flex;
  margin-bottom: 8px;
  padding: 4px 9px;
  border-radius: 999px;
  color: #9a6700;
  background: #fff5cf;
  font-size: 12px;
  font-weight: 700;
}

.approval-card-header h3 {
  margin: 0;
  color: #132f40;
  font-size: 18px;
}

.approval-card-header p {
  max-width: 620px;
  margin: 7px 0 0;
  color: #657985;
  font-size: 13px;
  line-height: 1.6;
}

.amount-block {
  min-width: 220px;
  text-align: right;
}

.amount-block > span,
.business-item > span,
.material-metric > span {
  display: block;
  color: #738793;
  font-size: 12px;
  font-weight: 600;
}

.amount-block strong {
  display: block;
  margin: 2px 0;
  color: #0f766e;
  font-size: 27px;
  line-height: 1.35;
}

.amount-block small,
.business-item small,
.material-name small {
  color: #8496a0;
  font-size: 12px;
}

.business-grid {
  display: grid;
  grid-template-columns: minmax(240px, 1.5fr) 1fr 1fr;
  gap: 12px;
  padding: 18px 22px;
}

.business-item {
  min-height: 76px;
  padding: 13px 15px;
  border: 1px solid #e5ecef;
  border-radius: 12px;
  background: #fbfcfd;
}

.business-item strong {
  display: block;
  margin: 5px 0 2px;
  color: #183647;
  font-size: 17px;
}

.business-item strong small { color: inherit; font-size: 12px; }

.material-panel {
  margin: 0 22px 16px;
  border: 1px solid #e1e9ed;
  border-radius: 13px;
  overflow: hidden;
}

.section-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 15px;
  background: #f5f8fa;
  border-bottom: 1px solid #e1e9ed;
}

.section-heading div > span {
  display: block;
  color: #758994;
  font-size: 11px;
}

.section-heading strong {
  color: #1a394a;
  font-size: 14px;
}

.line-count {
  padding: 3px 8px;
  border-radius: 999px;
  color: #516b79;
  background: #e8eff2;
  font-size: 11px;
}

.material-row {
  display: grid;
  grid-template-columns: 34px minmax(220px, 1.6fr) repeat(3, minmax(100px, .65fr));
  align-items: center;
  gap: 12px;
  padding: 14px 15px;
}

.material-index {
  width: 28px;
  height: 28px;
  display: grid;
  place-items: center;
  border-radius: 9px;
  color: #0f766e;
  background: #e9f6f3;
  font-size: 12px;
  font-weight: 800;
}

.material-name strong {
  display: block;
  color: #183748;
  font-size: 14px;
}

.material-metric strong {
  display: block;
  margin-top: 3px;
  color: #193849;
  font-size: 14px;
}

.material-subtotal { text-align: right; }
.material-subtotal strong { color: #0f766e; }

.rule-checks {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin: 0 22px 16px;
}

.rule-check {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 9px;
  border: 1px solid #cce8df;
  border-radius: 999px;
  color: #176b58;
  background: #f0faf6;
  font-size: 12px;
  font-weight: 650;
}

.rule-check i {
  width: 16px;
  height: 16px;
  display: grid;
  place-items: center;
  border-radius: 50%;
  color: #ffffff;
  background: #2f9d7e;
  font-size: 10px;
  font-style: normal;
}

.audit-details {
  margin: 0 22px 20px;
  border-top: 1px dashed #d7e1e6;
}

.audit-details summary {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 13px 0 4px;
  color: #506a78;
  cursor: pointer;
  font-size: 13px;
  font-weight: 700;
  list-style-position: inside;
}

.audit-details summary small {
  color: #94a3ab;
  font-weight: 400;
}

.audit-details dl {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px 20px;
  padding: 12px;
  border-radius: 10px;
  background: #f7f9fa;
}

.audit-details dl div { min-width: 0; }
.audit-details dt { color: #85969f; font-size: 11px; }
.audit-details dd {
  margin: 3px 0 0;
  color: #415966;
  font: 12px/1.5 'JetBrains Mono', Consolas, monospace;
  overflow-wrap: anywhere;
}

.approval-impact {
  display: flex;
  gap: 11px;
  margin: 8px 26px 0;
  padding: 12px 14px;
  border: 1px solid #dce8ee;
  border-radius: 12px;
  color: #476270;
  background: #f6fafc;
}

.impact-icon {
  width: 20px;
  height: 20px;
  display: grid;
  place-items: center;
  flex: 0 0 auto;
  border-radius: 50%;
  color: #ffffff;
  background: #5f7f90;
  font-family: Georgia, serif;
  font-size: 12px;
  font-weight: 800;
}

.approval-impact strong { color: #294957; font-size: 13px; }
.approval-impact p { margin: 2px 0 0; font-size: 12px; line-height: 1.55; }

/* 头部 */
.interrupt-header {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 16px 20px;
  border-bottom: 1px solid rgba(0, 0, 0, 0.06);
}

.interrupt-icon { font-size: 22px; }

.interrupt-title {
  font-size: 16px;
  font-weight: 700;
  color: #1e293b;
}

/* 内容区 */
.interrupt-body {
  padding: 16px 20px;
}

.info-block {
  margin-bottom: 12px;
}

.info-block:last-child { margin-bottom: 0; }

.info-label {
  font-size: 12px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  color: #64748b;
  margin-bottom: 4px;
}

.missing-fields .info-value {
  font-size: 15px;
  color: #b45309;
  font-weight: 600;
  background: #fef3c7;
  padding: 8px 14px;
  border-radius: 8px;
}

.info-pre {
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  padding: 10px 14px;
  border-radius: 8px;
  font-size: 13px;
  font-family: 'Monaco', 'Menlo', 'JetBrains Mono', monospace;
  overflow-x: auto;
  white-space: pre-wrap;
  margin: 0;
  line-height: 1.6;
}

/* 输入区 */
.interrupt-input-area {
  padding: 0 20px 16px;
  display: flex;
  gap: 10px;
  align-items: flex-end;
}

.supplement-textarea {
  flex: 1;
  border: 1px solid #e2e8f0;
  border-radius: 10px;
  padding: 10px 14px;
  font-size: 14px;
  line-height: 1.6;
  resize: none;
  outline: none;
  font-family: inherit;
  min-height: 60px;
  transition: border-color 0.2s;
}

.supplement-textarea:focus {
  border-color: #f59e0b;
  box-shadow: 0 0 0 3px rgba(245, 158, 11, 0.1);
}

.interrupt-hint {
  padding: 0 20px 12px;
  font-size: 12px;
  color: #94a3b8;
}

/* 操作卡片 */
.action-card {
  background: #ffffff;
  border: 1px solid #fecaca;
  border-radius: 12px;
  padding: 16px;
  margin-bottom: 12px;
}

.action-card:last-child { margin-bottom: 0; }

.action-header { margin-bottom: 12px; }

.action-type {
  font-size: 15px;
  font-weight: 700;
  color: #b91c1c;
  background: #fef2f2;
  padding: 4px 14px;
  border-radius: 6px;
}

/* 订单详情表 */
.order-table {
  width: 100%;
  border-collapse: collapse;
}

.order-table td {
  padding: 6px 0;
  font-size: 14px;
}

.field-label {
  width: 100px;
  color: #64748b;
  font-weight: 600;
}

.field-value {
  color: #1e293b;
}

.detail-table-wrap {
  margin-top: 12px;
}

.detail-label {
  font-size: 13px;
  font-weight: 600;
  color: #64748b;
  margin-bottom: 6px;
}

.detail-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}

.detail-table th {
  background: #fef2f2;
  padding: 8px 10px;
  text-align: left;
  font-weight: 600;
  color: #b91c1c;
  border-bottom: 1px solid #fecaca;
}

.detail-table td {
  padding: 8px 10px;
  border-bottom: 1px solid #f1f5f9;
}

/* 按钮 */
.submit-btn {
  padding: 12px 24px;
  border: none;
  border-radius: 12px;
  font-size: 14px;
  font-weight: 700;
  cursor: pointer;
  transition: all 0.2s ease;
}

.submit-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.supplement-btn {
  flex-shrink: 0;
  background: linear-gradient(135deg, #f59e0b 0%, #d97706 100%);
  color: #fff;
  box-shadow: 0 2px 8px rgba(245, 158, 11, 0.3);
}

.approve-btn {
  min-width: 210px;
  background: linear-gradient(135deg, #0f766e 0%, #0d9488 100%);
  color: #fff;
  box-shadow: 0 6px 16px rgba(13, 148, 136, 0.24);
}

.reject-btn {
  min-width: 116px;
  color: #49616e;
  background: #ffffff;
  border: 1px solid #cbd8de;
  box-shadow: none;
}

.interrupt-actions {
  display: flex;
  gap: 12px;
  padding: 18px 26px 24px;
  justify-content: flex-end;
  border-top: 1px solid #edf2f4;
  margin-top: 18px;
}

@media (max-width: 760px) {
  .interrupt-banner { margin: 10px 12px; }
  .approval-heading, .approval-body { padding-left: 16px; padding-right: 16px; }
  .approval-card-header { flex-direction: column; }
  .amount-block { min-width: 0; text-align: left; }
  .business-grid { grid-template-columns: 1fr; }
  .material-row { grid-template-columns: 28px 1fr; }
  .material-metric { grid-column: 2; }
  .material-subtotal { text-align: left; }
  .audit-details dl { grid-template-columns: 1fr; }
  .approval-impact { margin-left: 16px; margin-right: 16px; }
  .interrupt-actions { padding-left: 16px; padding-right: 16px; }
  .approve-btn { min-width: 0; flex: 1; }
}
</style>
