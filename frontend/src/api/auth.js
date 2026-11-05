import { apiFetch } from './http.js'

async function authenticate(action, username, password) {
  const response = await apiFetch(`/api/auth/${action}`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username: username.trim().toLowerCase(), password }),
  }, false)
  return (await response.json()).user
}

export const login = (username, password) => authenticate('login', username, password)
export const register = (username, password) => authenticate('register', username, password)
export async function currentUser() {
  return (await (await apiFetch('/api/auth/me', {}, false)).json()).user
}
export async function logout() {
  await apiFetch('/api/auth/logout', { method: 'POST' }, false)
}
