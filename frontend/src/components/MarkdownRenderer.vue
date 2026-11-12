<template>
  <div class="markdown-renderer" v-html="renderedContent" @error.capture="imageError" @click="retryImage"></div>
</template>

<script setup>
import { computed } from 'vue'
import { renderMarkdown } from '../utils/markdown.js'
const props = defineProps({ content: { type: String, default: '' }, showImages: { type: Boolean, default: true } })
const renderedContent = computed(() => renderMarkdown(props.content, props.showImages))
function imageError(event) {
  const image = event.target
  if (image.tagName !== 'IMG') return
  image.hidden = true
  const notice = image.closest('.markdown-image-wrap')?.querySelector('.image-error')
  if (notice) notice.hidden = false
}
function retryImage(event) {
  const button = event.target.closest('[data-retry-image]')
  if (!button) return
  const wrapper = button.closest('.markdown-image-wrap')
  const image = wrapper.querySelector('img')
  wrapper.querySelector('.image-error').hidden = true
  image.hidden = false
  image.src = image.getAttribute('src')
}
</script>

<!-- scoped 块：仅作用于组件根元素及模板中的 DOM -->
<style scoped>
.markdown-renderer {
  font-size: 15px;
  line-height: 1.85;
  color: #1e293b;
  word-break: break-word;
  overflow-wrap: break-word;
  /* 关键：防止任何子元素溢出容器 */
  overflow: hidden;
}
</style>

<!-- 非 scoped 块：作用于 v-html 动态内容（这些元素没有 data-v-xxx 属性） -->
<style>
/* ============ 标题样式 ============ */
.markdown-renderer h1 {
  font-size: 26px;
  font-weight: 700;
  margin: 24px 0 14px;
  padding-bottom: 10px;
  border-bottom: 2px solid #e2e8f0;
  color: #0f172a;
}

.markdown-renderer h2 {
  font-size: 22px;
  font-weight: 600;
  margin: 20px 0 12px;
  color: #1e293b;
}

.markdown-renderer h3 {
  font-size: 18px;
  font-weight: 600;
  margin: 18px 0 10px;
  color: #334155;
}

.markdown-renderer h4,
.markdown-renderer h5,
.markdown-renderer h6 {
  font-size: 16px;
  font-weight: 600;
  margin: 16px 0 8px;
  color: #475569;
}

/* ============ 段落样式 ============ */
.markdown-renderer p {
  margin: 12px 0;
}

/* ============ 列表样式 ============ */
.markdown-renderer ul,
.markdown-renderer ol {
  margin: 12px 0;
  padding-left: 30px;
}

.markdown-renderer li {
  margin: 8px 0;
}

.markdown-renderer ul li {
  list-style-type: disc;
  color: #334155;
}

.markdown-renderer ol li {
  list-style-type: decimal;
  color: #334155;
}

/* ============ 代码样式 ============ */
.markdown-renderer code {
  padding: 3px 8px;
  background: #f1f5f9;
  border: 1px solid #e2e8f0;
  border-radius: 6px;
  font-family: 'Monaco', 'Menlo', 'JetBrains Mono', monospace;
  font-size: 14px;
  color: var(--color-primary);
}

.markdown-renderer pre {
  margin: 14px 0;
  padding: 18px;
  background: #1e293b;
  border-radius: 12px;
  overflow-x: auto;
}

.markdown-renderer pre code {
  padding: 0;
  background: none;
  border: none;
  color: #e2e8f0;
  font-size: 14px;
  line-height: 1.7;
}

.markdown-renderer .hljs {
  background: transparent;
}

.markdown-renderer .hljs-keyword,
.markdown-renderer .hljs-selector-tag,
.markdown-renderer .hljs-built_in,
.markdown-renderer .hljs-name,
.markdown-renderer .hljs-tag {
  color: #60a5fa;
}

.markdown-renderer .hljs-string,
.markdown-renderer .hljs-title,
.markdown-renderer .hljs-section,
.markdown-renderer .hljs-attribute,
.markdown-renderer .hljs-literal,
.markdown-renderer .hljs-template-tag,
.markdown-renderer .hljs-template-variable,
.markdown-renderer .hljs-type,
.markdown-renderer .hljs-addition {
  color: #f59e0b;
}

.markdown-renderer .hljs-comment,
.markdown-renderer .hljs-quote,
.markdown-renderer .hljs-deletion,
.markdown-renderer .hljs-meta {
  color: #6ee7b7;
}

.markdown-renderer .hljs-number,
.markdown-renderer .hljs-regexp,
.markdown-renderer .hljs-selector-attr,
.markdown-renderer .hljs-selector-pseudo,
.markdown-renderer .hljs-variable {
  color: #94a3b8;
}

.markdown-renderer .hljs-function {
  color: #facc15;
}

.markdown-renderer .hljs-class .hljs-title {
  color: #34d399;
}

/* ============ 链接样式 ============ */
.markdown-renderer a {
  color: var(--color-primary);
  text-decoration: none;
  border-bottom: 1px solid transparent;
  transition: all 0.2s ease;
}

.markdown-renderer a:hover {
  border-bottom-color: var(--color-primary);
}

/* ============ 图片样式（兜底，内联 style 已处理主要约束）============ */
.markdown-renderer img.markdown-image {
  max-width: 100% !important;
  max-height: 360px;
  width: auto;
  height: auto;
  object-fit: contain;
  border-radius: 12px;
  margin: 14px 0;
  display: block;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08);
}

.markdown-renderer a.image-link {
  display: inline-block;
  padding: 10px 16px;
  background: #f1f5f9;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  margin: 10px 0;
  color: var(--color-primary);
  transition: all 0.2s ease;
}

.markdown-renderer a.image-link:hover {
  background: #e0f2fe;
  border-color: var(--color-primary);
}

/* ============ 表格样式 ============ */
.markdown-table-wrap {
  width: 100%;
  overflow-x: auto;
  margin: 16px 0;
  border: 1px solid var(--color-border);
  border-radius: 10px;
}
.markdown-table-wrap:focus-visible { outline: 2px solid var(--color-primary); outline-offset: 3px; }
.markdown-table-wrap table { margin: 0; min-width: 100%; }
.markdown-table-wrap th, .markdown-table-wrap td { white-space: nowrap; }
.markdown-renderer .markdown-table-wrap th { background: var(--color-bg-tertiary); color: var(--color-text); }
.markdown-table-wrap tbody tr:nth-child(2n) { background: var(--color-bg); }
.markdown-renderer table {
  width: 100%;
  border-collapse: collapse;
  margin: 0;
  font-size: 14px;
}

.markdown-renderer th,
.markdown-renderer td {
  padding: 12px 14px;
  border: 1px solid #e2e8f0;
  text-align: left;
}

.markdown-renderer th {
  background: #f8fafc;
  font-weight: 600;
  color: #1e293b;
}

.markdown-renderer tr:nth-child(even) {
  background: #f8fafc;
}

.markdown-renderer tr:hover {
  background: #f1f5f9;
}

/* ============ 引用样式 ============ */
.markdown-renderer blockquote {
  margin: 14px 0;
  padding: 14px 18px;
  background: #f8fafc;
  border-left: 4px solid var(--color-primary);
  border-radius: 0 8px 8px 0;
  color: #64748b;
  font-style: italic;
}

/* ============ 分割线样式 ============ */
.markdown-renderer hr {
  border: none;
  height: 1px;
  background: linear-gradient(90deg, transparent, #e2e8f0, transparent);
  margin: 24px 0;
}

/* ============ 任务列表样式 ============ */
.markdown-renderer input[type="checkbox"] {
  margin-right: 10px;
  vertical-align: middle;
  accent-color: var(--color-primary);
}

.markdown-renderer input[type="checkbox"]:checked + span {
  text-decoration: line-through;
  color: #94a3b8;
}
</style>

<style>
.markdown-image { max-width: 100%; max-height: 520px; height: auto; object-fit: contain; border-radius: 12px; margin: 12px 0; }
.markdown-image-wrap { display: block; }
.markdown-renderer .image-error:not([hidden]) { display: block; padding: 18px; margin: 12px 0; background: var(--color-bg-tertiary); color: var(--color-text-secondary); border-radius: 10px; font-size: 13px; }
.markdown-renderer .image-error button { margin-left: 10px; padding: 5px 10px; cursor: pointer; border: 1px solid var(--color-border); background: white; border-radius: 6px; }
</style>
