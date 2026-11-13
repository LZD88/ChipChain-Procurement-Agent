/* Run in a separately authenticated browser tab. All release mutations are
 * intercepted; the test never creates evaluations or changes real traffic. */
(async () => {
  const original = window.fetch, checks = [];
  const assert = (value, message) => { if (!value) throw new Error(message); checks.push(message); };
  const wait = async predicate => { for (let i = 0; i < 240; i++) { await new Promise(r => setTimeout(r, 25)); if (predicate()) return; } throw new Error('UI wait timed out'); };
  const button = text => [...document.querySelectorAll('button')].find(e => e.textContent.trim() === text);
  const field = label => [...document.querySelectorAll('label')].find(e => e.textContent.startsWith(label))?.querySelector('input,select,textarea');
  const input = (label, value) => { const e = field(label); e.value = value; e.dispatchEvent(new Event(e.tagName === 'SELECT' ? 'change' : 'input', { bubbles: true })); };
  const refresh = async () => { button('刷新数据').click(); await wait(() => !button('刷新数据').disabled); };
  const reply = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
  const state = { stable: 'builtin-v1', canary: null, previousStable: null, percentage: 0, revision: 1, enabled: true, status: 'STABLE', revoked: ['test-revoked'] };
  const roles = Object.fromEntries(['main','procurement-analyst','procurement-order','procurement-orchestrator','lightweight'].map(r => [r, `原有${r}规则`]));
  const versions = [{ version: 'test-revoked', change_note: '旧测试版本' }, { version: 'builtin-v1', change_note: '初始版本' }];
  let permission = true, failure = false, writes = 0, pairs = 0, saved, progress = 'RUNNING', progressFails = false, history = { events: [], gates: [], audit: [] };
  const keys = [], cohort = { completedCount: 0, sufficient: false, successRate: null, p50Seconds: null, p95Seconds: null };
  window.fetch = async (url, options = {}) => {
    if (!String(url).includes('/api/prompt-releases/')) return original(url, options);
    const path = new URL(url, location.origin).pathname.split('/api/prompt-releases/')[1];
    if (path === 'capabilities') return reply({ view: permission, maintain: permission });
    if (failure) return reply({ detail: '测试：数据服务不可用' }, 503);
    if (path === 'state') return reply(state);
    if (path === 'history') return reply(history);
    if (path === 'observations') return reply({ cohorts: { stable: cohort, canary: cohort }, pendingApprovals: [{ version: 'test-revoked', count: 2 }] });
    if (path.startsWith('versions/')) return reply({ version: path.slice(9), roles, diff: {} });
    if (path === 'versions' && options.method === 'POST') {
      writes++; saved = JSON.parse(options.body); await new Promise(r => setTimeout(r, 120));
      if (writes === 1) return reply({ detail: '测试：重复内容冲突' }, 409);
      Object.assign(roles, saved.roles); versions.unshift({ version: 'test-created', change_note: saved.note }); return reply({ version: 'test-created' }, 201);
    }
    if (path === 'versions') return reply({ items: versions });
    if (path === 'pairs') { pairs++; await new Promise(r => setTimeout(r, 100)); history.audit.unshift({ action: 'PAIR_CREATE', outcome: 'SUCCESS', metadata: { stableRunId: 'test-s', candidateRunId: 'test-c' } }); return reply({ stableRunId: 'test-s', candidateRunId: 'test-c' }, 202); }
    if (path.startsWith('evaluations/')) {
      if (progressFails) return reply({ detail: '测试：进度暂不可用' }, 503);
      return reply({ runId: path.slice(12), status: progress, promptVersion: path.endsWith('test-s') ? 'builtin-v1' : 'test-created', total: 39, completed: progress === 'RUNNING' ? 12 : 39, passed: progress === 'RUNNING' ? 12 : 39, failed: 0 });
    }
    if (path === 'gates') return reply({ passed: true, gateId: 'test-gate', candidateVersion: 'test-created', reasons: [], expiresAt: '2099-01-01T00:00:00' });
    if (path === 'canary') { keys.push(JSON.parse(options.body).idempotencyKey); return reply({ detail: '测试：状态已更新' }, 409); }
    throw new Error(`Unexpected test route ${path}`);
  };
  try {
    await refresh();
    input('从哪个版本开始', 'test-revoked'); await wait(() => !button('刷新数据').disabled);
    input('从哪个版本开始', 'builtin-v1'); await wait(() => !button('刷新数据').disabled);
    assert(document.querySelector('.source-editor').closest('details:not([open])') && field('正式版测试编号').closest('details:not([open])'), 'raw prompts and run IDs hidden by default');
    assert(field('想调整哪位助手').selectedOptions[0].textContent === '日常问答' && field('从哪个版本开始').value === 'builtin-v1', 'friendly role and current version selected');
    assert(document.body.textContent.includes('2 个会话等待确认'), 'pending original version explained');
    button('填入一个示例').click(); input('给新版本起个名字', '简单问题简洁回答'); await wait(() => !button('保存新版本').disabled);
    const addition = field('补充回答要求').value;
    button('保存新版本').click(); button('保存新版本').click(); await wait(() => !button('刷新数据').disabled);
    assert(writes === 1 && document.body.textContent.includes('重复内容冲突') && field('补充回答要求').value === addition, 'duplicate clicks blocked and conflict preserves draft');
    button('保存新版本').click(); await wait(() => !button('刷新数据').disabled);
    assert(saved.roles.lightweight.endsWith(addition) && saved.roles.main === '原有main规则' && !button('开始测试').disabled, 'simple requirements appended to selected role only');
    button('开始测试').click(); button('开始测试').click(); await wait(() => !button('刷新数据').disabled);
    assert(pairs === 1 && button('查看测试结论').disabled && document.body.textContent.includes('12 / 39'), 'queued tests show progress and prevent premature gate check');
    progress = 'COMPLETED'; await wait(() => !button('查看测试结论').disabled);
    assert(document.body.textContent.includes('39 / 39'), 'polling updates completed progress automatically');
    location.hash = ''; await wait(() => !document.querySelector('.release-page'));
    location.hash = '#prompt-releases'; await wait(() => button('查看测试结论') && !button('刷新数据').disabled);
    assert(field('从哪个版本开始').value === 'test-created' && document.body.textContent.includes('39 / 39'), 'returning to page restores latest candidate and test pair from server history');
    input('补充回答要求', '还未保存的补充'); await refresh();
    assert(field('补充回答要求').value === '还未保存的补充', 'refresh preserves unsaved editing');
    progressFails = true; button('读取指定测试进度').click(); await wait(() => !button('刷新数据').disabled);
    assert(document.body.textContent.includes('暂时无法读取测试进度') && button('查看测试结论').disabled, 'progress failure explained without false pass');
    progressFails = false; progress = 'COMPLETED'; button('重试读取').click(); await wait(() => !button('刷新数据').disabled);
    button('查看测试结论').click(); await wait(() => !button('刷新数据').disabled);
    assert(button('开启小范围试用').disabled, 'unsaved changes prevent publishing an older tested version');
    input('补充回答要求', ''); await wait(() => !button('开启小范围试用').disabled);
    for (let i = 0; i < 2; i++) { button('开启小范围试用').click(); await wait(() => !button('刷新数据').disabled); }
    assert(keys.length === 2 && keys[0] === keys[1], 'conflict retries retain idempotency key');
    state.canary = 'test-created'; state.status = 'CANARY'; await refresh();
    assert(button('全面启用试用版').disabled && document.body.textContent.includes('0 / 20 条'), 'sample shortage explains blocked promotion');
    state.canary = null; state.status = 'STABLE'; input('从哪个版本开始', 'test-revoked'); await wait(() => !button('刷新数据').disabled);
    assert(button('开始测试').disabled && document.body.textContent.includes('修改后保存为新版本'), 'revoked versions require a new saved version');
    failure = true; await refresh(); assert(document.body.textContent.includes('数据服务不可用'), 'service outage is readable');
    failure = false; permission = false; await refresh();
    assert(document.body.textContent.includes('没有提示词查看权限') && !button('保存新版本'), 'unauthorized users cannot mutate');
    return { passed: checks.length, checks };
  } finally { window.fetch = original; button('刷新数据').click(); }
})()
