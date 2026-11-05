<template>
  <section class="tool-call-panel" aria-label="工具调用过程">
    <header v-if="!imagesOnly" class="panel-heading">
      <span class="panel-title">执行过程</span><span>{{ tools.length }} 次调用</span>
      <span v-if="runningCount" class="calling">{{ runningCount }} 项执行中</span>
      <span v-else-if="errorCount" class="error">{{ errorCount }} 项未成功</span>
      <span v-else>{{ allDone ? '已完成' : '等待或已停止' }}</span>
    </header>
    <ol class="tool-list">
      <li v-for="(tool, index) in tools" :key="getCallId(tool) || index" class="tool-call" :data-call-id="getCallId(tool)">
        <span v-if="!imagesOnly" class="step-number" :class="getToolStatus(tool, streaming)">{{ String(index + 1).padStart(2, '0') }}</span>
        <article class="call-card">
          <header v-if="!imagesOnly" class="call-heading">
            <div class="call-info"><strong>{{ getToolLabel(tool) }}</strong><code>{{ tool.tool_name || '工具' }}</code></div>
            <span class="call-status" :class="getToolStatus(tool, streaming)">{{ statusLabels[getToolStatus(tool, streaming)] }}</span>
          </header>
          <p v-if="!imagesOnly && getToolTarget(tool)" class="tool-target" :title="getToolTarget(tool)">{{ getToolTarget(tool) }}</p>
          <p v-if="!imagesOnly" class="result-summary" :class="{ error: getToolStatus(tool, streaming) === 'error' }">{{ getToolSummary(tool, streaming) }}</p>
          <ChartPreview v-for="(image, imageIndex) in (hideImages ? [] : getToolImages(tool))" :key="image" :source="image" :title="chartTitle(tool, imageIndex)" />
          <details v-if="!imagesOnly" class="call-details">
            <summary>请求与结果 <span class="chevron">›</span></summary>
            <p class="call-id">调用 ID <code>{{ getCallId(tool) || '历史记录未提供' }}</code></p>
            <section class="detail-section">
              <h4>请求参数</h4><pre v-if="tool.args">{{ formatToolArgs(tool.args) }}</pre>
              <p v-else class="empty-result">{{ getToolStatus(tool, streaming) === 'calling' ? '正在接收参数…' : '无参数' }}</p>
            </section>
            <section class="detail-section">
              <h4>返回结果</h4>
              <pre v-if="parseResult(tool.text)">{{ JSON.stringify(parseResult(tool.text), null, 2) }}</pre>
              <div v-else-if="tool.text" class="tool-result"><MarkdownRenderer :content="tool.text" :show-images="false" /></div>
              <p v-else class="empty-result">{{ getToolImages(tool).length ? '已生成上方图表' : getToolSummary(tool, streaming) }}</p>
            </section>
          </details>
        </article>
      </li>
    </ol>
  </section>
</template>

<script setup>
import { computed } from 'vue'
import MarkdownRenderer from './MarkdownRenderer.vue'
import ChartPreview from './ChartPreview.vue'
import { getCallId, getToolLabel, getToolSummary, getToolTarget, getToolStatus, formatToolArgs, parseToolArgs } from '../utils/toolCalls.js'
import { getToolImages, parseResult } from '../utils/images.js'
const props = defineProps({ tools: { type: Array, default: () => [] }, streaming: { type: Boolean, default: false }, imagesOnly: Boolean, hideImages: Boolean })
const runningCount = computed(() => props.tools.filter(tool => getToolStatus(tool, props.streaming) === 'calling').length)
const errorCount = computed(() => props.tools.filter(tool => getToolStatus(tool, props.streaming) === 'error').length)
const allDone = computed(() => props.tools.every(tool => getToolStatus(tool, props.streaming) === 'done'))
const statusLabels = { calling: '执行中', done: '已完成', error: '未成功', ended: '未收到结果', waiting: '等待确认', stopped: '已停止' }
function chartTitle(tool, index) { return parseToolArgs(tool.args).chart_config?.title || `分析图表 ${index + 1}` }
</script>

<style scoped>
.tool-call-panel { margin: 12px 24px 20px 74px; max-width: 800px; min-width: 0; color: #46545d; }
.panel-heading { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; color: #78888e; font-size: 12px; margin-bottom: 14px; }
.panel-title { color: #334c5c; font-weight: 600; font-size: 13px; }
.tool-list { margin: 0; padding: 0; list-style: none; }
.tool-call { display: flex; gap: 12px; position: relative; padding-bottom: 14px; }
.tool-call:not(:last-child)::before { content: ''; position: absolute; width: 1px; background: #dbe4e6; top: 28px; bottom: -2px; left: 13px; }
.step-number { flex: 0 0 28px; height: 28px; display: grid; place-items: center; border: 1px solid #d9e3e6; border-radius: 50%; background: #f5f8f8; color: #5a727d; font-size: 10px; font-variant-numeric: tabular-nums; }
.step-number.calling { background: #fff5da; border-color: #e6d6aa; }
.step-number.done { background: #edf5f1; border-color: #c4dfd2; }
.step-number.error { background: #fceee9; border-color: #e6c5b9; }
.call-card { flex: 1; min-width: 0; border: 1px solid #dce5e7; border-radius: 12px; background: #f8faf9; padding: 13px 16px; }
.call-heading { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.call-info { display: flex; gap: 9px; flex-wrap: wrap; align-items: baseline; min-width: 0; }
.call-info strong { font-size: 13px; color: #304d5a; }
.call-info code { font-size: 11px; color: #7b8c94; }
code { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; overflow-wrap: anywhere; }
.call-status { flex-shrink: 0; font-size: 11px; color: #687b85; }
.calling { color: #8c691c; }.done { color: #41765f; }.error { color: #a14d35; }.waiting { color: #896c2e; }
.tool-target { margin: 8px 0 0; font-size: 12px; color: #647882; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.result-summary { margin: 9px 0 0; font-size: 12px; line-height: 1.7; color: #5d737b; overflow-wrap: anywhere; }
.call-details { margin-top: 11px; border-top: 1px solid #e4ebec; }
summary { padding-top: 10px; cursor: pointer; list-style: none; font-size: 12px; color: #526d7b; display: flex; justify-content: space-between; }
summary::-webkit-details-marker { display: none; } summary:focus-visible { outline: 2px solid #50778c; outline-offset: 3px; }
.chevron { transition: transform .15s; } details[open] .chevron { transform: rotate(90deg); }
.call-id { display: flex; gap: 8px; font-size: 11px; color: #798c95; margin: 12px 0; }
.call-id code { min-width: 0; }.detail-section + .detail-section { margin-top: 12px; }
h4 { font-size: 11px; color: #6b7d85; margin: 8px 0 5px; font-weight: 500; }
pre, .tool-result { padding: 10px 12px; margin: 0; background: #fff; border: 1px solid #e1e8eb; border-radius: 7px; font-size: 12px; line-height: 1.6; max-height: 300px; overflow: auto; }
pre { white-space: pre-wrap; overflow-wrap: anywhere; }.empty-result { font-size: 12px; color: #74868e; }
@media (max-width: 760px) { .tool-call-panel { margin: 12px; }.tool-call { gap: 8px; }.call-card { padding: 12px; }.call-info { gap: 4px; } }
</style>
