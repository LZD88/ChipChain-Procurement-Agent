# MCP 统一代理

在 <http://localhost:18001> 的「AI 网关管理 → MCP 管理」中可以看到两个服务：

| 服务 | 用途 | Agent 连接地址 |
| --- | --- | --- |
| chipchain-erp | 供应商、物料、库存、采购比价、订单，共 11 个工具 | `http://localhost:18881/mcp-servers/chipchain-erp` |
| chipchain-charts | 图表及表格生成，共 27 个工具 | `http://localhost:18881/mcp-servers/chipchain-charts` |

两者使用 Higress 原生的 DIRECT_ROUTE 模式和 Streamable HTTP。继续使用原来的聊天界面即可，工具名称、业务参数和返回格式不变。链路为 `Agent → Higress → 原 MCP 服务 → ERP/图表服务`。此处没有将 ERP REST API 重新转换成一套工具。

## 鉴权与执行约束

MCP 使用单独的 `chipchain-mcp` 调用方和 `MCP_GATEWAY_API_KEY`。模型凭证访问 MCP 返回 403，MCP 凭证访问模型路由也返回 403；无凭证或无效凭证返回 401。凭证仅保存在项目本地 `.env` 和 VM 的私有配置中。

Higress 认证后再按调用方限流，MCP 额度为共享的每分钟 300 个 HTTP 请求，包含协议初始化和工具发现，超过返回 429。单实例内存计数在重启后重置。客户端伪造 `X-Mse-Consumer` 无法绕过限流。网关在转发 MCP 请求前删除 Authorization，避免把内部网关凭证发给外部图表服务；MCP 会话 ID、协议版本及 JSON-RPC 正文保持透传。

业务身份仍通过服务端签名的 `invocation_envelope` 传递。网关凭证不授予最终用户业务权限；采购决策的归属、签名验证、审批流程、认领和订单幂等机制保持原有实现。未签名或伪造签名的订单请求已实测拒绝。

MCP 转发超时为 120 秒，关闭网关自动重试，以免重复执行工具。上游故障原样返回错误；不会自动切换到直连，也不会把一次订单请求重新发送。这里采用故障报错方式，没有为 MCP 另加模型路由使用的异常端点熔断。

## 部署和恢复

Windows 上的 ERP MCP 仍只监听本机。VirtualBox NAT 中，VM 通过 `10.0.2.2:8000/mcp` 访问宿主机回环服务；无需将 MCP 改成对局域网开放。图表服务使用原有 HTTPS 地址。Windows 应用不依赖 Docker Desktop。

项目 `.env`：

```dotenv
MCP_GATEWAY_ENABLED=true
MCP_GATEWAY_BASE_URL=http://localhost:18881
MCP_GATEWAY_API_KEY=<单独生成的 MCP 凭证>
```

保留原 `ERP_MCP_URL` 和 `ANALYSIS_MCP_URL`。开启网关时由服务端配置解析器切换到上表的两个入口，并附加独立认证头。关闭网关属于人工运维变更，修改开关后须重启 Agent。错误或缺失的网关配置会明确失败，不自动退回直连。

VM 私有部署环境还包含 `HIGRESS_MCP_ERP_UPSTREAM`、`HIGRESS_MCP_ANALYSIS_UPSTREAM`，由 `ops/higress/render_config.py` 生成路由、服务来源、认证和限流资源。模板拒绝 URL 中的用户名、密码及查询参数。生成文件包含密钥，不能提交 Git。部署采用同一份模板管理模型和 MCP 资源；启用 MCP 时认证插件统一为 `key-auth.internal`，迁移时应移除旧 `chipchain-key-auth.yaml`，避免重复认证。控制台可查看真实服务和授权，直接编辑后会被下次模板部署覆盖。

`start_web.py` 会管理 VM/OpenSandbox，并通过 `scripts/start_ai_platform_vm.py` 启动 Higress；启动检查同时验证模型和 MCP 的匿名请求均被拒绝。原生 ERP、MCP 服务和评测 Worker 仍按项目原有方式启动。MCP 开关和入口纳入部署指纹，更改后必须重新评测才能继续灰度发布；未修改已有预算或门禁。

## 本次验证

已通过工具发现、真实库存查询、签名比价、无签名/伪造签名订单拒绝及实际柱状图生成。网页 API 通过新链路完成库存查询，约 5.38 秒返回首条物料库存 800 片。此次没有向真实 ERP 创建或修改订单。

私有验收文件位于 `%LOCALAPPDATA%/ChipSupply/higress/`：`mcp-proxy-validation.json`、`mcp-boundary-validation.json`、`mcp-project-validation.json`。相关工具封装、鉴权、请求分流、部署指纹和工作流安全测试共 114 项通过。

`mcp-transport-validation.json` 记录隔离上游实测：Authorization 已移除，会话 ID 与协议版本透传；独立测试调用方使用 2 次/分钟额度得到 `[200, 200, 429, 429]`，伪造调用方头无法绕过。三次模拟 503 请求只产生三次上游调用。临时调用方和路由已清理，生产 MCP 仍使用 300 次/分钟额度。
