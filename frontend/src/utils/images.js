/** 将报告相对路径、沙箱路径统一映射到同源图表接口。 */
export function normalizeImageSource(value) {
  if (typeof value !== 'string') return ''
  const src = value.trim()
  const match = src.match(/^(?:sandbox:\/analysis\/charts\/|\/analysis\/charts\/|\.?\/?charts\/|\/api\/artifacts\/charts\/)(.+)$/)
  if (match) {
    try {
      const name = decodeURIComponent(match[1])
      if (!/^[\p{L}\p{N}_.-]+\.(png|jpe?g|gif|webp|svg)$/iu.test(name)) return ''
      return `/api/artifacts/charts/${encodeURIComponent(name)}`
    } catch { return '' }
  }
  if (/^https?:\/\//i.test(src) || /^data:image\/(png|jpe?g|gif|webp|svg\+xml);base64,[A-Za-z0-9+/=]+$/i.test(src)) return src
  return ''
}

export function parseResult(text) {
  if (text && typeof text === 'object') return text
  try { return JSON.parse(text) } catch { return null }
}

export function getToolImages(tool) {
  const result = parseResult(tool.text)
  const candidates = [...(Array.isArray(tool.images) ? tool.images : [])]
  if (result?.sandbox_path) candidates.unshift(result.sandbox_path)
  else if (result?.image_url || result?.source_url) candidates.unshift(result.image_url || result.source_url)
  for (const match of (typeof tool.text === 'string' ? tool.text : '').matchAll(/!\[[^\]]*\]\(\s*<?([^\s)>]+)>?(?:\s+["'][^\n]*?["'])?\s*\)/g)) candidates.push(match[1])
  return [...new Set(candidates.map(normalizeImageSource).filter(Boolean))]
}
