# 决策证据 API

所有接口位于 Agent API 的 `/api` 下，使用现有 HttpOnly 登录 Cookie。写请求必须带
`X-Requested-With: DeepAgent`；响应统一 `Cache-Control: no-store`。普通账号只能读取自己的
决策，拥有 `decision:audit` 的账号可通过 `owner` 查询其他所有者。无权访问和不存在统一返回 404。

| 方法与路径 | 说明 |
| --- | --- |
| `GET /api/decisions?page=1&size=20` | 所有者分页列表；`size` 为 1–100。 |
| `GET /api/decisions/{decisionId}` | 不可变证据与追加生命周期。 |
| `GET /api/decisions/{decisionId}/lifecycle` | 只读生命周期。 |
| `GET /api/decisions/policies` | 可用于回放的已注册策略。 |
| `POST /api/decisions/{decisionId}/replay` | 用已存事实执行模拟回放。请求仅允许 `policyVersion`。 |
| `GET /api/decisions/{decisionId}/export` | 带 SHA-256 `exportFingerprint` 的 JSON 导出，并记录导出事件。 |

回放请求：

```json
{"policyVersion":"supplier-score-v1"}
```

回放响应包含 `manifest.simulation=true`、模拟 `result` 和 `diff`。`diff` 明确展示推荐、资格、
淘汰原因、排名、分项分数和总分变化。接口不接受任意 source JSON，不调用订单工具，不创建
claim，也不改写原始证据。未知、退休或不支持回放的策略返回 422，不会替换成当前策略。

证据详细 JSON 被保留策略清理后仍保留 tombstone，但不可再回放。指纹不匹配返回 409，不能
用于回放或下单。旧版进程内临时 ID 没有持久记录，按“不存在”处理，调用方应重新比价。

订单结果不明确时，claim 保持 `RECONCILIATION_REQUIRED`。协调器使用 ERP 的
`GET /api/orders/idempotency/{key}` 只读契约；`COMPLETED` 关联已有订单，`PROCESSING` 保持阻断，
`ABSENT` 只有在 `ORDER_RECONCILIATION_RETRY_IF_ABSENT=true` 时才以同一请求和幂等键重试一次。
