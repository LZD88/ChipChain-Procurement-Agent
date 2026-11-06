/**
 * 历史记录 API 模块
 *
 * 提供会话列表查询、会话消息获取和会话删除接口
 */

import { apiFetch } from './http.js'

const API_BASE = '/api/history'

/**
 * 获取会话列表
 *
 * @param {number} page - 页码，从 1 开始
 * @param {number} limit - 每页数量
 * @returns {Promise} 返回会话列表响应 { sessions, total, page, limit }
 */
export async function getSessions(page = 1, limit = 20) {
  const response = await apiFetch(`${API_BASE}?page=${page}&limit=${limit}`)

  return response.json()
}

/**
 * 获取会话消息历史
 *
 * @param {string} threadId - 会话 ID
 * @returns {Promise} 返回会话消息响应 { thread_id, messages }
 */
export async function getMessages(threadId) {
  const response = await apiFetch(`${API_BASE}/${encodeURIComponent(threadId)}/messages`)

  return response.json()
}

/**
 * 删除会话
 *
 * @param {string} threadId - 会话 ID
 * @returns {Promise} 返回删除结果 { success, message }
 */
export async function deleteSession(threadId) {
  const response = await apiFetch(`${API_BASE}/${encodeURIComponent(threadId)}`, {
    method: 'DELETE'
  })

  return response.json()
}

export async function updateSession(threadId, changes) {
  const response = await apiFetch(`${API_BASE}/${encodeURIComponent(threadId)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(changes)
  })
  return response.json()
}

export async function getProjects() {
  const response = await apiFetch('/api/projects')
  return response.json()
}

export async function createProject(name) {
  const response = await apiFetch('/api/projects', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name })
  })
  return response.json()
}

export async function updateProject(projectId, changes) {
  const response = await apiFetch(`/api/projects/${encodeURIComponent(projectId)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(changes)
  })
  return response.json()
}

export async function deleteProject(projectId) {
  const response = await apiFetch(`/api/projects/${encodeURIComponent(projectId)}`, {
    method: 'DELETE'
  })
  return response.json()
}
