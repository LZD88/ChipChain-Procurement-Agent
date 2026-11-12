# 对话按能力分流

默认启用 `AGENT_REQUEST_ROUTING_ENABLED=true`，不需要改数据库。路由在登录、会话归属和现有 NeMo 输入护栏之后执行，先用本地规则筛选；规则不能确定时，才调用独立的 `deepseek-flash` 分类器。不让客户端指定执行权限。

| 路径 | 例子 | 可用能力 |
| --- | --- | --- |
| 护栏固定回复 | 护栏已批准的“你好” | 保留原固定回复及 checkpoint，不调用业务模型或沙箱 |
| 轻量问答 | 你好呀、你是谁、你能做些什么呢、可以帮我做哪些事情、P95是啥 | 单 Agent 简短回答，无沙箱准备 |
| 轻量只读查询 | 查询库存预警、查看物料库存、查询采购订单状态 | 仅开放明确登记的八个只读 MCP 工具 |
| 完整流程 | 运行 Python、生成文件、复杂采购分析、创建/修改订单、含义不明确的“继续” | 原 DeepAgent、用户沙箱、子 Agent、权限与 HITL 审批 |
| 中断恢复 | 审批或补充信息后 `/resume` | 始终恢复原完整流程 |

轻量工具清单：`supplier_get`、`supplier_query`、`part_query`、`part_search`、`part_by_supplier`、`inventory_query`、`inventory_warning`、`order_search_details`。按完整工具名匹配，新增工具不会按前缀自动放行。不包含 `procurement_compare`（会持久化决策证据）、订单写工具、文件操作、Shell/Python、技能分配、任意 HTTP 或委派工具。

路由只优化执行方式，不能作为授权结果。请求中只要出现代码、文件产物、写操作或复杂任务信号，就保留完整流程，分类模型不覆盖这些规则。“继续”“照上面做”等明确依赖前文的短句也直接保留完整流程。否定句中的这些词同样保守回退，例如“查询库存，不要修改订单”可能进入完整流程，可直接说“查询库存”。即使发生轻量误判，受限 graph 也没有执行危险工具的能力，不能仅凭模型输出名称调用隐藏工具。

规则剩余的 `uncertain` 请求交给 `deepseek-flash`，例如“能聊聊你的本领不”“芯片和传感器差别大吗”“帮我瞅瞅六轴惯性传感器还有几片”。分类器仅接收本轮用户原文和固定分类指令，不读取历史、不调用工具、不生成业务回答，必须完整输出 `LIGHTWEIGHT` 或 `SANDBOX`。口语写操作（如“把这批芯片订了吧”）、依赖上下文或用途不明确的请求应判为 `SANDBOX`。

分类器复用 `DEEPSEEK_API_KEY` / `DEEPSEEK_BASE_URL`，关闭思考、重试，最多输出 16 token。默认总超时 5 秒，超时、接口失败、非预期输出一律回退沙箱。`AGENT_REQUEST_ROUTER_LLM_ENABLED=false` 可关闭模型兜底；`AGENT_REQUEST_ROUTER_TIMEOUT_SECONDS` 可设为 1–10 秒。300 字以上的请求不走简单句式匹配，可由模型判断；超过 4096 字或空输入直接回退，完整输入不会被截断后分类。上述配置不改变输入护栏原有长度限制。

功能询问按完整句式识别，支持“能/可以/会”“做/干/提供”“什么/些什么/哪些”及常见语气词，不依赖某一句固定文本。混合请求仍先检查完整流程信号，例如“你能做些什么呢，帮我下单500片传感器”不会进入轻量路径。

轻量和完整 graph 使用相同的 MySQL 消息状态，SSE 的 session/token/tool/done 及展示历史契约不变。同一会话可先查库存、再执行分析、再问概念。存在待审批/待补充的 checkpoint 时，普通发消息返回 409，要求先通过既有中断恢复入口处理，避免替换正在等待的 graph。用户身份仍来自本轮登录上下文，不从历史或模型参数读取。

启用分流时，登录、注册、`/me` 不再提前分配用户沙箱，记为 `deferred_routing`；匿名预热池仍按既有配置运行。首次完整任务按需领取环境，因此这类任务仍可能有冷启动。设置 `AGENT_REQUEST_ROUTING_ENABLED=false` 并重启可恢复原请求准备与登录提前准备，护栏固定回复保持既有行为。不要修改真实 `.env` 中的凭据；Windows 通过 `start_web.py` 启动，使用 VirtualBox/OpenSandbox。

观测新增 `agent_request_routes_total{route,reason}` 和 `agent_route_duration_seconds{route,status}`。标签是固定路径及原因，不包含用户、线程或输入。route 为 `direct`、`lightweight`、`sandbox`；按路径查看耗时分布时只比较相同结果和样本口径。流耗时包含模型兜底分类，但不包含 SSE 前的输入护栏；用户实际等待还包含护栏及模型/ERP 耗时。`agent_request_classifier_duration_seconds{outcome}` 单独记录兜底分类耗时，outcome 为 `llm_simple`、`llm_sandbox`、`llm_timeout`、`llm_error`、`llm_invalid` 或 `cancelled`。规则命中没有分类耗时样本。轻量路径没有 `agent_preparation_wait_seconds` 样本，不要把缺少样本解释成零秒沙箱准备。

离线回归：

```powershell
.venv\Scripts\python.exe -m pytest -q src/test/test_request_classifier.py src/test/test_request_routing.py src/test/test_agent_preparation.py src/test/test_subagents.py src/test/test_runtime_config.py
```

测试使用真实编译 graph 与内存 checkpoint，检查隐藏写工具不可调用、身份隔离、跨路径历史、审批恢复、输入护栏和沙箱故障不影响轻量路径；不创建真实采购订单。

## 本机验证记录（2026-11-12）

通过 `start_web.py` 启动 Windows 原生 API/UI 和现有 VirtualBox，保持池目标 1、准备并发 1；未改真实 `.env`。相邻回归共 174 项通过，另有 18 个子测试通过。

运行 `python scripts/verify_request_routing.py --include-workspace` 使用随机测试账号，在同一会话依次完成问候、概念解释、真实库存预警查询、沙箱计算、再次概念解释。前三轮数据库没有用户沙箱绑定；库存工具返回三条结构化记录且无 API 错误。计算实际调用 `execute` 执行 `python -c "print(1+1)"`，工具返回 `2`、退出码 0。随后概念解释跳过准备并保持历史。

| 单次端到端样本（包含输入护栏） | 耗时 |
| --- | --- |
| 问候 | 2.58 秒 |
| P50/P95 概念解释 | 3.01 秒 |
| 库存预警查询 | 4.09 秒 |
| Python 计算（包含沙箱冷准备） | 137.37 秒 |
| 完整流程后的轻量问答 | 3.68 秒 |

最终规则重启后再次验证前三项分别为 2.45、3.36、3.67 秒，三轮均没有准备事件或用户沙箱绑定，准备等待成功计数仍为 0。报告分别在忽略目录 `runtime-logs/request-routing-verification.json`、`runtime-logs/request-routing-final.json`。这些是功能验收的单次样本，不能当作 P50/P95 或稳定时延承诺；复杂任务冷启动仍需优化。

公开健康端点 Agent、UI、ERP、OpenSandbox、看板及 Prometheus 均返回 200；evaluation worker 的 Python 进程 35448/8604 仍在运行。可选 Tempo、Grafana、RabbitMQ 指标端点不可达，单独记录为未启动组件。

### 功能询问误分流修复验证

此前“你能做些什么呢”未匹配简短问答规则，回退为 `sandbox/uncertain`，导致普通问答等待环境准备。修复后路由、真实编译 graph、SSE 和准备流程相关回归共 80 项、6 个子测试通过。通过 `start_web.py` 重启后，真实 SSE 验证该原句首 token 1.576 秒、完整回复 2.757 秒；“你能干什么”“有什么功能呀”“可以帮我做哪些事情”完整回复分别为 2.575、3.134、2.824 秒，均无准备事件、无用户沙箱绑定。这些仍是单次样本。验证脚本报告保存在 `runtime-logs/request-routing-capabilities.json`。

### 模型兜底验证

接入 deepseek-flash 后，路由、分类失败/超时/取消、SSE、工具边界、身份、审批恢复与准备流程相关测试共 149 项、17 个子测试通过。默认上限最终设为 5 秒：3 秒试验曾出现一次分类超时，已按预期回退；该失败报告保留在 `runtime-logs/request-routing-classifier.json`。

最终通过 `start_web.py` 重启并执行 `python scripts/verify_request_routing.py --include-classifier --output runtime-logs/request-routing-classifier-final.json`。5 个独立分类样本全部正确：普通问答/知识/口语库存查询进入轻量路径，口语订货与依赖前文请求进入完整流程；分类耗时 0.460–2.445 秒。订货样本只做分类，不执行业务写入。

10 个真实聊天样本全部完成，均无准备事件、无用户沙箱绑定；其中规则未覆盖的功能询问、概念问题与库存查询完整回复分别为 4.735、4.236、5.243 秒，库存查询实际调用 `inventory_query`。规则明确命中的请求没有分类阶段；一次功能问答仍用了 8.630 秒，说明分流不消除业务回答模型本身的延迟。这些仅是单次样本，不代表 P50/P95。

最终公开健康端点 Agent、UI、ERP、OpenSandbox、看板和 Prometheus 全部返回 200，evaluation worker 进程仍在运行。可选 Tempo、Grafana、RabbitMQ 仍不可达。
