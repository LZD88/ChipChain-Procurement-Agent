/** All browser API requests share the server-issued HttpOnly session cookie. */
export async function apiFetch(url, options = {}, notifyUnauthorized = true) {
  const response = await fetch(url, {
    ...options,
    credentials: 'same-origin',
    headers: { 'X-Requested-With': 'DeepAgent', ...options.headers },
  })
  if (!response.ok) {
    const data = await response.json().catch(() => ({}))
    const detail = typeof data.detail === 'string' ? data.detail :
      (typeof data.detail?.message === 'string' ? data.detail.message :
        (Array.isArray(data.detail) ? '请检查输入格式。注册/登录时密码需为 8–128 位。' : `请求失败（${response.status}）`))
    const error = new Error(detail)
    error.status = response.status
    error.code = typeof data.detail?.code === 'string' ? data.detail.code : null
    if (response.status === 401 && notifyUnauthorized && typeof window !== 'undefined') {
      window.dispatchEvent(new Event('auth:expired'))
    }
    throw error
  }
  return response
}
