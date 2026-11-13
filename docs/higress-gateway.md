# Higress 模型网关

Higress 部署在现有 `opensandbox-vm` 的 Linux Docker Engine 中。Windows 上的 Agent、ERP、MCP 和评测 Worker 仍使用原生进程，不依赖 Docker Desktop。

## 入口与使用

- 网关控制台：<http://localhost:18001>，也可从 Agent 观测中心侧栏的「Higress 模型网关」进入。
- OpenAI 兼容模型接口：`http://localhost:18881/v1`。
- 控制台管理员为 `admin`，密码保存在 `%LOCALAPPDATA%/ChipSupply/higress/service-logins.txt`。
- 「AI 服务提供者」显示 `chipchain-deepseek`；「AI 路由」显示 `chipchain`，仅开放 `deepseek-flash` 和 `deepseek-chat`。

正常使用原来的对话界面即可。输入护栏、分流分类器、主/子 Agent、摘要模型和提示词候选评测通过网关调用模型。ERP 与图表 MCP 也已通过网关代理，详见 [MCP 接入](higress-mcp.md)。

## 当前策略

| 项目 | 行为 |
| --- | --- |
| 鉴权 | 网页与评测使用独立网关 Key，上游 DeepSeek Key 保留在网关私有配置中 |
| 限流 | 网页调用方 120 次/分钟，评测调用方 60 次/分钟，超过返回 429 |
| MCP 限流 | 独立 MCP 调用方 300 次/分钟，覆盖初始化、工具发现、工具调用等 HTTP 请求 |
| 限流维度 | 经鉴权确定的调用方；同一调用方下的所有用户共享额度，非按最终用户计费 |
| 模型范围 | deepseek-flash / deepseek-chat；不自动更换模型或供应商 |
| 重试 | 模型 SDK 和网关转发重试均关闭，避免重复流式回答或工具计划 |
| 超时 | 主模型请求 45 秒，连接上游 5 秒；分类器和安全检查保留各自的业务期限 |
| 熔断 | 同一上游端点连续 3 次 5xx 或连接失败后逐出，基础恢复等待 30 秒；全部端点不可用时返回 503 |

限流为单实例本地额度，重启会重置计数。扩容时需迁移到共享限流存储。上游 DNS 有多个 IP 时熔断分别作用于各端点；连续失败后的隔离时间可能递增。429 通过 ai-proxy 的令牌健康策略处理，不等同于连接层 5xx 熔断。所有令牌失效时该插件仍可能尝试上游，所以额外配置 Envoy 异常端点检测并关闭 panic 模式。

目前的故障降级是明确报错、限制失败请求并等待恢复。没有备用供应商，也不生成伪造答案。启用网关后不会自动直连上游绕过治理。恢复直连属于运维配置变更，需要手动关闭 `LLM_GATEWAY_ENABLED` 后重启 Agent 和 Worker。

## 配置与启动

项目 `.env` 使用 `LLM_GATEWAY_ENABLED`、`LLM_GATEWAY_BASE_URL`、`LLM_GATEWAY_API_KEY` 和 `LLM_GATEWAY_EVALUATION_KEY`。网关凭证不能复用上游 Key，不能放进前端代码。

运行 `python start_web.py` 时，启动器先管理虚拟机和 OpenSandbox，再通过 `scripts/start_ai_platform_vm.py` 启动配置好的 Nacos、Langfuse 和 Higress，并验证无凭证模型请求被拒绝。评测 Worker 仍须单独启动。`--status` 可查看 VM 服务状态。

部署目录为 VM 的 `/home/osb/chipchain-higress`，持久配置在 `data/`，代理日志在 `logs/`。备份整个私有部署目录并保护访问权限。宿主端口转发仅监听 127.0.0.1。

`ops/higress/compose.vm.yml` 固定镜像摘要；`ops/higress/render_config.py` 从私有部署环境生成资源，输出含密钥，不能提交 Git。当前配置以该脚本为准，控制台用于查看；直接修改控制台配置后再次部署会被覆盖。ai-statistics 不采集默认问答属性；插件日志使用 error 级别，避免其故障 warning 输出原始上游令牌。

更改传输方式会改变提示词评测的模型配置指纹，需要重新评测后才能进行后续发布。已保存的提示词、发布状态、预算和门禁不会因此放宽。

## 验收记录

私有证据位于 `%LOCALAPPDATA%/ChipSupply/higress/`：`proxy-validation.json` 验证真实模型的网页/评测凭证、流式回答和工具参数；`fault-validation.json` 记录隔离假上游的限流、故障、熔断与恢复。故障测试使用临时路由及调用方，验收后已移除，不影响真实 ERP。

业务 API 的普通知识问答与真实库存工具查询均完成，记录在 `project-chat-validation.json` 和 `project-inventory-validation.json`。`evaluation-validation.json` 中的主 Agent 委派案例通过，使用两次模型调用执行 `task` 和 `procurement_compare`，并在 Langfuse 查询到 27 个观测节点及评分。该案例使用隔离业务工具，没有向真实 ERP 写订单。相关回归测试 109 项通过。

此项接入验证不代表完整提示词发布评测或灰度门禁已通过。
