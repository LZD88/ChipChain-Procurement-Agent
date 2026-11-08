# 本地采购证据演示

## 1. 启动

填写 `.env` 中的 Agent MySQL、ERP 服务账号、专用 `DECISION_DB_*` 和至少 32 字节的
`MCP_INVOCATION_SIGNING_SECRET`，然后依次启动 ERP、MCP 和 Web：

```powershell
$env:PYTHONPATH = "src"
& .\.venv\Scripts\python.exe -m mcp_server.server_main
& .\.venv\Scripts\python.exe start_web.py
```

浏览器登录后输入：

```text
帮我采购500片六轴惯性传感器，预算9000元，要求两周内交付。先给出方案，再创建采购单。
```

`procurement_compare` 先持久化证据再返回决策 ID。MCP 重启后该 ID 仍能读取；数据库不可用时
比较结果不会包含可下单 ID。部署前产生的旧临时 ID 不迁移，必须重新比较。

## 2. 批准或拒绝

工作台显示人工审批卡片。选择批准会追加 `APPROVED` 后执行严格字段校验和原子 claim；选择
拒绝会追加 `REJECTED`，不会调用 ERP。重复提交同一审批返回冲突，跨用户恢复统一不可见。

## 3. 查看证据

点击工作台右上角“决策证据”，粘贴本轮 `DEC-...`。也可以在浏览器已登录会话中执行：

```javascript
const headers = {"X-Requested-With":"DeepAgent"};
const list = await fetch('/api/decisions?page=1&size=20', {headers}).then(r => r.json());
const id = list.items[0].decisionId;
const evidence = await fetch(`/api/decisions/${encodeURIComponent(id)}`, {headers}).then(r => r.json());
console.log(evidence);
```

详情保留不可变请求/来源/结果，并以事件连接审批、订单、Trace 和 ERP 审计引用。缺失关系显示为
缺失或待补，不伪造链接；Trace 后端过期不影响证据读取。

## 4. 模拟回放

```javascript
const policies = await fetch('/api/decisions/policies', {headers}).then(r => r.json());
const replay = await fetch(`/api/decisions/${encodeURIComponent(id)}/replay`, {
  method: 'POST', headers: {...headers, 'Content-Type':'application/json'},
  body: JSON.stringify({policyVersion: policies.items[0].version}),
}).then(r => r.json());
console.log(replay.manifest.simulation, replay.diff);
```

期望 `simulation` 为 `true`。回放只使用存储事实和注册策略，生成独立 replay 记录；订单数、claim
和原证据不变。将策略改为不存在的版本会得到 422，不会静默使用最新策略。

## 5. 自动验证

```powershell
& .\.venv\Scripts\python.exe -m pytest -q src/test/test_durable_comparison.py src/test/test_durable_order.py src/test/test_order_lifecycle.py src/test/test_order_reconciliation.py src/test/test_hitl_decision_events.py src/test/test_decision_api.py src/test/test_decision_replay.py
npm --prefix frontend test
npm --prefix frontend run build
```

如 ERP 调用发送后超时，证据页显示待协调状态。默认不自动重试；只有 ERP 明确报告 `ABSENT`
且配置允许时，才以原订单号、原请求和原幂等键重试一次，避免重复订单。
