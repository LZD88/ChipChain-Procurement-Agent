import test from 'node:test'
import assert from 'node:assert/strict'
import { createProject, deleteProject, getProjects, updateProject, updateSession } from './history.js'

function jsonResponse(data) {
  return new Response(JSON.stringify(data), {
    status: 200,
    headers: { 'Content-Type': 'application/json' }
  })
}

test('创建项目发送中文名称并返回持久化项目', async t => {
  let request
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    request = { url, options }
    return jsonResponse({ project_id: 'project-1', name: '核心器件采购' })
  })

  const project = await createProject('核心器件采购')
  assert.equal(request.url, '/api/projects')
  assert.equal(request.options.method, 'POST')
  assert.deepEqual(JSON.parse(request.options.body), { name: '核心器件采购' })
  assert.equal(request.options.credentials, 'same-origin')
  assert.equal(project.project_id, 'project-1')
})

test('会话名称、置顶和项目归属通过 PATCH 保存', async t => {
  let request
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    request = { url, options }
    return jsonResponse({ success: true })
  })

  await updateSession('会话/1', { title: '供应商比价', pinned: true, project_id: 'project-1' })
  assert.equal(request.url, '/api/history/%E4%BC%9A%E8%AF%9D%2F1')
  assert.equal(request.options.method, 'PATCH')
  assert.deepEqual(JSON.parse(request.options.body), {
    title: '供应商比价', pinned: true, project_id: 'project-1'
  })
})

test('项目列表使用当前登录会话读取', async t => {
  let request
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    request = { url, options }
    return jsonResponse({ projects: [] })
  })

  assert.deepEqual(await getProjects(), { projects: [] })
  assert.equal(request.url, '/api/projects')
  assert.equal(request.options.credentials, 'same-origin')
  assert.equal(request.options.headers['X-Requested-With'], 'DeepAgent')
})

test('编辑项目名称通过 PATCH 持久化', async t => {
  let request
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    request = { url, options }
    return jsonResponse({ success: true, project_id: 'project/1', name: '网络采购中心' })
  })

  const result = await updateProject('project/1', { name: '网络采购中心' })
  assert.equal(request.url, '/api/projects/project%2F1')
  assert.equal(request.options.method, 'PATCH')
  assert.deepEqual(JSON.parse(request.options.body), { name: '网络采购中心' })
  assert.equal(result.name, '网络采购中心')
})

test('删除项目使用 DELETE 且保留后端返回说明', async t => {
  let request
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    request = { url, options }
    return jsonResponse({ success: true, message: '项目已删除，原有会话已移至独立会话' })
  })

  const result = await deleteProject('project/1')
  assert.equal(request.url, '/api/projects/project%2F1')
  assert.equal(request.options.method, 'DELETE')
  assert.match(result.message, /独立会话/)
})
