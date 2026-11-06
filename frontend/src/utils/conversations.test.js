import test from 'node:test'
import assert from 'node:assert/strict'
import { createConversations } from './conversations.js'

function deferred() {
  let resolve, reject
  const promise = new Promise((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}
function harness(getMessages = async () => ({ messages: [] })) {
  const calls = []
  const request = (resuming, threadId, payload, callbacks, signal, projectId) => {
    const pending = deferred()
    const call = { resuming, threadId, payload, callbacks, signal, projectId, ...pending }
    calls.push(call)
    return pending.promise
  }
  const store = createConversations({
    streamChat: (message, threadId, callbacks, signal, projectId) => request(false, threadId, message, callbacks, signal, projectId),
    resumeChat: (threadId, data, callbacks, signal) => request(true, threadId, data, callbacks, signal),
    getMessages,
  })
  const done = (call, threadId) => {
    call.callbacks.onDone({ thread_id: threadId })
    call.resolve()
  }
  return { store, calls, done }
}

test('新建会话和切换时保留两个并行请求，迟到的会话 ID 和结果各自归属', async () => {
  const { store, calls, done } = harness()
  store.current.value.draft = '保留草稿'
  const a = store.current.value
  const first = store.send('A 查询库存')
  store.newChat('project-b')
  const b = store.current.value
  const second = store.send('B 查询供应商')
  assert.equal(calls.length, 2)
  assert.equal(calls[0].signal.aborted, false)
  calls[0].callbacks.onSession({ thread_id: 'thread-a' })
  assert.equal(store.current.value, b)
  assert.equal(b.threadId, null)
  calls[1].callbacks.onSession({ thread_id: 'thread-b' })
  calls[0].callbacks.onToken('A 的答案', 'main')
  calls[1].callbacks.onProgress({ message: 'B 仍在处理' })
  await store.select('thread-a')
  assert.equal(store.current.value.draft, '保留草稿')
  assert.equal(a.messages.at(-1).content, 'A 的答案')
  assert.equal(a.isStreaming, true)
  assert.equal(calls[1].signal.aborted, false)
  done(calls[1], 'thread-b')
  await second
  assert.equal(b.unread, true)
  assert.equal(a.isStreaming, true)
  done(calls[0], 'thread-a')
  await first
  await store.select('thread-b')
  assert.equal(b.unread, false)
  assert.equal(b.isStreaming, false)
  assert.equal(calls[1].projectId, 'project-b')
})

test('停止只取消所选会话，旧请求的完成或错误不影响后续一轮', async () => {
  const { store, calls, done } = harness()
  const a = store.current.value
  const first = store.send('A')
  calls[0].callbacks.onSession({ thread_id: 'a' })
  store.newChat()
  const second = store.send('B')
  calls[1].callbacks.onSession({ thread_id: 'b' })
  await store.select('a')
  store.stop()
  assert.equal(calls[0].signal.aborted, true)
  assert.equal(calls[1].signal.aborted, false)
  const third = store.send('A 重试')
  calls[0].callbacks.onError(new Error('旧错误'))
  calls[0].callbacks.onDone({ aborted: true })
  calls[0].resolve()
  await first
  assert.equal(a.isStreaming, true)
  assert.equal(a.messages.some(message => message.content.includes('旧错误')), false)
  done(calls[2], 'a')
  done(calls[1], 'b')
  await Promise.all([second, third])
})

test('后台审批在切回后仍可恢复，审批恢复和另一个会话互不干扰', async () => {
  const { store, calls, done } = harness()
  const first = store.send('创建采购单')
  calls[0].callbacks.onSession({ thread_id: 'approval' })
  store.newChat()
  const second = store.send('库存查询')
  calls[1].callbacks.onSession({ thread_id: 'inventory' })
  const interrupt = { thread_id: 'approval', interrupt_type: 'hitl_approval' }
  calls[0].callbacks.onInterrupt(interrupt)
  calls[0].resolve()
  await first
  assert.equal(store.current.value.interruptData, null)
  await store.select('approval')
  assert.deepEqual(store.current.value.interruptData, interrupt)
  const resumed = store.resume({ decisions: [{ type: 'approve' }] })
  await store.select('inventory')
  assert.equal(calls[2].signal.aborted, false)
  calls[2].callbacks.onToken('采购单已提交', 'main')
  done(calls[2], 'approval')
  await resumed
  assert.equal(store.current.value.messages.some(message => message.content === '采购单已提交'), false)
  assert.equal(store.find('approval').interruptData, null)
  done(calls[1], 'inventory')
  await second
})

test('慢历史响应不会覆盖另一条会话，运行中会话直接恢复内存中的最新消息', async () => {
  const histories = new Map()
  const { store, calls, done } = harness(threadId => {
    const pending = deferred()
    histories.set(threadId, pending)
    return pending.promise
  })
  const loading = store.select('old')
  store.newChat()
  const pending = store.send('新任务')
  const active = store.current.value
  histories.get('old').resolve({ messages: [{ role: 'user', content: '旧历史' }] })
  await loading
  assert.equal(store.current.value, active)
  assert.equal(active.messages[0].content, '新任务')
  calls[0].callbacks.onSession({ thread_id: 'new' })
  calls[0].callbacks.onToken('实时片段', 'main')
  await store.select('old')
  await store.select('new')
  assert.equal(histories.has('new'), false)
  assert.equal(active.messages.at(-1).content, '实时片段')
  done(calls[0], 'new')
  await pending
})

test('删除和退出回收对应请求，被删除会话的迟到回调不会复活会话', async () => {
  const { store, calls } = harness()
  const first = store.send('A')
  calls[0].callbacks.onSession({ thread_id: 'a' })
  store.newChat()
  const second = store.send('B')
  calls[1].callbacks.onSession({ thread_id: 'b' })
  store.remove('a')
  assert.equal(calls[0].signal.aborted, true)
  assert.equal(calls[1].signal.aborted, false)
  calls[0].callbacks.onSession({ thread_id: 'a' })
  assert.equal(store.find('a'), undefined)
  store.dispose()
  assert.equal(calls[1].signal.aborted, true)
  calls.forEach(call => call.resolve())
  await Promise.all([first, second])
})
