import test from 'node:test'
import assert from 'node:assert/strict'
import { login, register, currentUser, logout } from './auth.js'
import { apiFetch } from './http.js'
import { getSessions } from './history.js'

test('账号请求使用 Cookie，发送规范化用户名，不在浏览器保存密码', async t => {
  const requests = []
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    requests.push([url, options])
    return new Response(JSON.stringify({ user: { username: 'alice' } }), { status: 200 })
  })
  assert.equal((await register(' Alice ', 'test-password')).username, 'alice')
  await login('Alice', 'test-password')
  await currentUser()
  await logout()
  assert.deepEqual(requests.map(([url]) => url), ['/api/auth/register', '/api/auth/login', '/api/auth/me', '/api/auth/logout'])
  for (const [, options] of requests) {
    assert.equal(options.credentials, 'same-origin')
    assert.equal(options.headers['X-Requested-With'], 'DeepAgent')
  }
  assert.deepEqual(JSON.parse(requests[0][1].body), { username: 'alice', password: 'test-password' })
})

test('登录失败显示后端提示；业务会话过期通知界面退出', async t => {
  const events = []
  const previousWindow = globalThis.window
  globalThis.window = { dispatchEvent: event => events.push(event.type) }
  t.after(() => {
    if (previousWindow === undefined) delete globalThis.window
    else globalThis.window = previousWindow
  })
  t.mock.method(globalThis, 'fetch', async () => new Response(JSON.stringify({ detail: '请重新登录' }), { status: 401 }))
  await assert.rejects(login('alice', 'wrong-password'), /请重新登录/)
  assert.deepEqual(events, [])
  await assert.rejects(getSessions(), /请重新登录/)
  assert.deepEqual(events, ['auth:expired'])
})

test('验证错误和非 JSON 服务器错误均返回可读消息', async t => {
  t.mock.method(globalThis, 'fetch', async () => new Response(JSON.stringify({ detail: [{ msg: 'short password' }] }), { status: 422 }))
  await assert.rejects(apiFetch('/api/auth/register'), /8–128/)
  t.mock.method(globalThis, 'fetch', async () => new Response('Bad Gateway', { status: 502 }))
  await assert.rejects(apiFetch('/api/history'), /502/)
})

test('服务不可用时保留代理的错误原因和错误码，非 JSON 网关错误也提示重试', async t => {
  t.mock.method(globalThis, 'fetch', async () => new Response(JSON.stringify({ detail: {
    code: 'backend_unavailable', message: '智能助手服务暂时不可用，可能正在启动，请稍后重试。',
  } }), { status: 503 }))
  await assert.rejects(apiFetch('/api/chat/stream'), error => {
    assert.equal(error.status, 503)
    assert.equal(error.code, 'backend_unavailable')
    assert.match(error.message, /可能正在启动/)
    return true
  })
  for (const status of [502, 503, 504]) {
    t.mock.method(globalThis, 'fetch', async () => new Response('Gateway error', { status }))
    await assert.rejects(apiFetch('/api/chat/stream'), error => {
      assert.equal(error.status, status)
      assert.match(error.message, /服务暂时不可用.*请稍后重试/)
      return true
    })
  }
})
