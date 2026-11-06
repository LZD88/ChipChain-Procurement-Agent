import { fileURLToPath } from 'node:url'
import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig(({ mode }) => {
  // 仅在 Vite 配置阶段读取服务端口，不将根目录密钥暴露给浏览器。
  const projectRoot = fileURLToPath(new URL('..', import.meta.url))
  const env = loadEnv(mode, projectRoot, ['BACKEND_', 'FRONTEND_'])
  let backendHost = env.BACKEND_HOST || '0.0.0.0'
  if (['0.0.0.0', '::'].includes(backendHost)) backendHost = 'localhost'
  if (backendHost.includes(':') && !backendHost.startsWith('[')) backendHost = `[${backendHost}]`
  const backendUrl = env.BACKEND_URL || `http://${backendHost}:${env.BACKEND_PORT || 8090}`

  return {
    plugins: [vue()],
    server: {
      port: Number(env.FRONTEND_PORT || 3000),
      strictPort: true,
      // 启动器通过响应头确认服务属于本次启动，标识只从进程环境读取。
      headers: process.env.DEEPAGENT_LAUNCH_ID
        ? { 'X-DeepAgent-Launch-Id': process.env.DEEPAGENT_LAUNCH_ID }
        : {},
      proxy: {
        // 后端路由已经带 /api 前缀，无需 rewrite。
        '/api': {
          target: backendUrl,
          changeOrigin: true,
          configure(proxy) {
            proxy.on('error', (_error, _request, response) => {
              if (!response || typeof response.writeHead !== 'function' || response.destroyed || response.writableEnded) return
              // SSE 已经开始时保留断流语义，不能在流中间追加 JSON 错误。
              if (response.headersSent) {
                response.destroy()
                return
              }
              response.writeHead(503, {
                'Content-Type': 'application/json; charset=utf-8',
                'Retry-After': '5',
              })
              response.end(JSON.stringify({ detail: {
                code: 'backend_unavailable',
                message: '智能助手服务暂时不可用，可能正在启动，请稍后重试。',
              } }))
            })
          },
        }
      }
    }
  }
})
