import MarkdownIt from 'markdown-it'
import hljs from 'highlight.js'
import { normalizeImageSource } from './images.js'

const md = new MarkdownIt({
  html: false, breaks: true, linkify: true, typographer: true,
  highlight(text, language) {
    if (language && hljs.getLanguage(language)) {
      try { return hljs.highlight(text, { language }).value } catch { /* 使用纯文本 */ }
    }
    return ''
  }
})
const escape = md.utils.escapeHtml
md.renderer.rules.table_open = () => '<div class="markdown-table-wrap" tabindex="0" role="region" aria-label="表格，可横向滚动"><table>'
md.renderer.rules.table_close = () => '</table></div>\n'
md.renderer.rules.image = (tokens, index, options, env) => {
  if (env.showImages === false) return ''
  const token = tokens[index]
  const source = normalizeImageSource(token.attrGet('src'))
  if (!source) return `<span class="image-error">图片地址不可用：${escape(token.content || '图片')}</span>`
  return `<span class="markdown-image-wrap"><a href="${escape(source)}" target="_blank" rel="noopener noreferrer"><img src="${escape(source)}" alt="${escape(token.content || '图片')}" class="markdown-image" loading="lazy" referrerpolicy="no-referrer" /></a><span class="image-error" hidden>图片暂时无法加载 <button type="button" data-retry-image>重新加载</button></span></span>`
}
const defaultLinkOpen = md.renderer.rules.link_open || ((tokens, index, options, env, self) => self.renderToken(tokens, index, options))
md.renderer.rules.link_open = (tokens, index, options, env, self) => {
  tokens[index].attrSet('target', '_blank')
  tokens[index].attrSet('rel', 'noopener noreferrer')
  return defaultLinkOpen(tokens, index, options, env, self)
}
export function renderMarkdown(content, showImages = true) { return md.render(content || '', { showImages }) }
