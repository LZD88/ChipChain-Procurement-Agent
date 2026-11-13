# 提示词发布闭环验收记录

日期：2026-11-13。本次实现对应 OpenSpec `add-prompt-release-lifecycle`，代码尚未提交 GitHub。

当前验收以文末“优化后最终配对与灰度验收”为准；早期失败和待验收记录保留用于追溯。

## 交付范围

五角色不可变版本、角色差异、受权管理 API、独立真实模型评测 worker、配对门禁、
确定性灰度分桶、推广与回滚状态机、会话和审批原快照、请求用量观测均已实现。
七张增量表保存在本机 `agent_db`。版本修改由数据库触发器禁止；发布操作同时记录修订和审计。

管理入口：`http://localhost:3000/#prompt-releases`。
只读观测：`http://localhost:3002/#releases`。
现有 `lizhidian` 已开通两项提示词权限，密码未修改。其他账号按操作说明授权。

## 自动化和浏览器验证

- 后端相关回归涵盖不可变版本、并发入场、审批原版恢复、权限、数据库故障、门禁篡改与过期、
  发布竞态、同版本缓存复用、不同版本隔离、用量去重、保留清理、失租 fencing 与取消后继续工作。
- 独立 `prompt_test_*` MySQL 数据库通过完整 V1 → V2 → 配对 → gate → 10% canary →
  两组各 20 条样本 → 推广 → 回滚测试。该测试使用合成证据，**仅用于状态机验证**；
  helper 强制校验隔离数据库名，未写入运行中的 `agent_db`，不能作为真实模型发布证据。
- 前端 45 项测试和 Vite 构建通过；构建保留既有大体积 chunk 警告。
- 管理页面浏览器检查 8 项：空样本、待审批原版、保存、评测入队、服务端 gate、
  重复点击、409 与幂等重试、503 和无权限。测试仅在页面内拦截请求，不执行真实发布。
- 观测仓库 10 项 Python 测试通过；11 项 JS 测试通过，1 项因未提供 `QUALITY_REPORT_PATH` 跳过。
  只读发布代理拒绝 POST/PUT/PATCH/DELETE，未开放其他 Agent API。
- 观测页面浏览器检查 11 项，包括 401/403/503 后清除旧数据、缺用量、样本不足、
  原审批版本、文本转义和 hash 返回。桌面 1440px、手机 390px 实际截图检查无横向溢出。

可复现浏览器脚本位于 `frontend/browser-tests/prompt-releases.js` 和观测仓库
`tests/browser/releases.js`。先登录相应页面，再通过 `agent-browser eval -b` 执行文件的 UTF-8 Base64；
脚本结束恢复真实 fetch。浏览器测试不替代运行环境中真实灰度验收。

## 真实模型结果

候选版本：`69c76aea-61f1-4f90-a106-00a32cb3f001`。
仅轻量角色新增“结论先行、查询不到明确说明、不重复解释身份”。稳定版保持 `builtin-v1`。

第一组完整保留的最终配置前评测：

| 分组 | Run ID | 结果 | 已记录用例 | 用量 | 估算费用 USD |
| --- | --- | --- | --- | --- | --- |
| stable | 536b0007-f39d-411a-bdbb-75f4f35722da | FAILED / budget_exceeded | 9 PASSED、3 FAILED | 244595 输入 + 4683 输出 | 0.07045346 |
| candidate | 32bc683c-4bbc-45e3-83dc-dc30f2d428d3 | FAILED / budget_exceeded | 9 PASSED、3 FAILED | 253377 输入 + 4113 输出 | 0.07267302 |

两组主 Agent 委派场景的三次重复均超出单用例预算，累计用量触发每组 200000 Token 上限，
后续场景未执行。门禁拒绝，不以缺失场景充当成功。旧报告保留，后续代码更新后其部署指纹自然失效。
预算检查随后细化到每次模型响应结束，达到总上限后不再发起下一次模型调用；
已在途响应产生的实际 Token 仍完整计入报告，不能假装只消耗到预算线。

最终代码的配对结果与 gate 见本文后附记录。真实评测使用内存 fixtures、checkpoint/store、
捕获式 shell 和订单模拟器，不连接生产 MCP，不执行真实 ERP 写入。费用按配置单价估算，
失败和超时已返回的用量同样记录，缺失数据不算零。实际费用以供应商账单为准。

## 真实聊天归属

问候、库存查询、完整沙箱分析均收到 `prompt_version=builtin-v1`，对应请求：

| 场景 | Request ID | 路由 | 模型调用 | 输入 / 输出 Token |
| --- | --- | --- | --- | --- |
| 一句话介绍 | 7e694f24-fcb7-453f-b92f-1d0e38f36ca8 | lightweight | 1 | 1720 / 24 |
| 查询库存 | 04d769ab-132b-4c35-9433-7fae0f98373e | lightweight | 2 | 9910 / 1056 |
| 采购分析 | 8e1e3eeb-8887-45a8-b1ff-7965211e06c0 | sandbox | 17 | 315796 / 8213 |

三条请求数据库归属、usage 与 SSE 一致，浏览器历史恢复显示执行版本。
采购分析触发原有 20 次工具调用限制，业务分析没有完成；stream 正常结束仅表示请求完成，
观测页的请求完成率不等于业务正确率。本轮未批准或创建任何真实采购订单。

补充信息恢复曾实际尝试两次，模型未进入预期 interrupt，因此没有把这两次尝试记为真实 resume 验收通过。
审批后仍沿用原版、回滚迁移与 pending 409 已通过隔离测试，但浏览器完整 resume、
前后端两进程完整发布集成和真实灰度后回滚仍待验收。不得据此宣称线上闭环已全部跑通。

## 启动和回退

使用 Windows 原生 API/UI、独立评测 worker、ERP、MCP、观测服务与 Prometheus；
通过 `start_web.py` 检查 VirtualBox/OpenSandbox `127.0.0.1:18080`，未使用 Docker Desktop。
启动后的公开 health 和 worker 需单独核对；Tempo/Grafana 缺失不影响 MySQL 发布证据。

真实灰度保持关闭：稳定指针为 builtin-v1、revision=1，没有伪造两组各 20 条线上流量。
要完成最后一段真实验收，需要先决定优化原有 Agent 调用链，或显式修订评测预算后重新评测。
没有修改现有门禁阈值来使本次报告通过。

回退方法见 [操作说明](prompt-releases.md)：先处理 pending，再关闭发布运行时开关并重启原 API/UI/worker，
保留新增表与历史记录。回滚业务版本使用管理页动作，带 expectedRevision 和幂等键。
日志、临时 Cookie、数据库导出均不属于交付；临时 QA 登录态测试后单独撤销，不影响用户现有会话。

## 优化前配对记录（历史失败，已由后续验收替代）

Gate ID：`a11b5c88-eca4-4a72-b39e-0b71eeefcdee`；passed=False。

| 分组 | Run ID | 状态 | 用例 | 输入 / 输出 Token | 估算费用 USD |
| --- | --- | --- | --- | --- | --- |
| stableRunId | 60136545-ef6c-4a5a-9b0d-783c103082e4 | FAILED / budget_exceeded | {'PASSED': 9, 'FAILED': 3} | 198814 / 4037 | 0.05736346 |
| candidateRunId | c9ec6057-cb55-40cc-b3de-4fd1d1e7f2d9 | FAILED / budget_exceeded | {'PASSED': 9, 'FAILED': 3} | 197699 / 3617 | 0.05687486 |

两组 manifest 与最终部署 fingerprint 一致。未进入 canary，未推广。

相关后端共 154 项用例、6 项 subtests：最后一轮全量中 153 项通过，1 项旧测试仍期待排队取消后再次写终态；
该断言已改为立即 CANCELLED、后续不得覆盖。随后涉及此变更的 24 项数据库/队列/生命周期/执行测试全部通过。
其余前端与观测测试结果如上。发现并修复了真实取消时结果已冻结导致 worker 退出的问题，
现 claim 冲突返回 False，worker 继续轮询；排队取消立即终结。

最终健康检查：API `/health`、UI、ERP `/actuator/health`、观测 `/healthz`、Prometheus `/-/ready`、
OpenSandbox `/health` 均为 200。MCP 由 API 初始化时实际载入工具验证可用，不提供 `/health` 路由。
评测 worker 为独立单实例 Windows 进程（venv launcher 与 Python 子进程属于同一逻辑 worker）。
Tempo、Grafana 返回 502，为可选遥测故障；持久化发布 history 经观测代理仍返回 200。
本机 Redis 6379、RabbitMQ 5672、Tempo 3200、Grafana 3001 均未监听，属于可选服务缺失；
未为此启动 Docker Desktop，核心 API/ERP/模型路径可用。

OpenSpec 完成 36/39；未勾选 6.3、8.1、8.3，对应真实 resume、完整跨进程发布联调与真实灰度回滚。

## 2026-11-13 调用链优化追加验收

用户选择先优化主 Agent，保持原预算。没有修改固定场景、必要断言、门禁或 200000 Token 上限；没有改写 builtin-v1。

- 精简 SDK 通用文件/记忆/委派说明，技能索引按需送入模型；运行时和隔离评测使用同一工厂。
- 明确的普通比价只携带业务工具，直接复用 procurement_compare 聚合证据。深度报告/外部核价/趋势/脚本等保留完整工具流程。
- 已完成的普通比价、订单成功或人工拒绝以确定性模板展示，省掉重复总结；证据不完整/并行工具/深度任务不走这条结束路径。
- 已有偏好预加载，不为默认值额外读写；最终审批卡替代提审前的重复文字确认，必要信息仍中断补充。
- 修复真实重复比价撞 ID：证据指纹不变，新签名调用产生独立决策 ID，历史证据不覆盖，单决策下单幂等仍保留。

中间配对 `a2dd6474-916a-48a1-8f40-5b035626ddd7` / `8f76a1cc-27df-421f-b501-36adea912fb8` 保留在库；gate `e8b35d73-ec6f-40ec-b4fa-570fc9ed7de0` 拒绝。
稳定组完成 39 条，36 通过、3 条重复订单场景过度文字确认；182553 输入 + 12270 输出。
候选组 36 条后触发总预算，188032 输入 + 13349 输出。随后精简运行时策略、直接处理审批拒绝，并修正合法首单提审步骤；没有放宽断言。

最终相关后端回归：184 项通过，6 项 subtests。包含完整原版角色实际送入模型、两次调用完成主/子比价与 SSE 最终回复、深度能力保留、审批批准/拒绝/并发隔离、重复签名比价历史不覆盖、证据篡改和单决策原子认领。既有 MySQL/UTC 弃用告警不影响通过。

真实 API 连续两次相同条件（10 个传感器、1500 元、14 天）均完成，无错误事件；返回不同决策 ID 与相同证据指纹。因 MOQ 无合格推荐，如实拒绝推荐，没有创建订单。
- 首次：request `9f023a70-df27-435d-a3a4-c5286e8a620e`，2 次模型，4934/421 Token，stream 61.78 秒，含 API 重启后的沙箱准备。
- 预热后重复：request `64b27325-e51f-4e4f-9b94-f2a23315cc69`，2 次模型，4905/385 Token，stream 5.65 秒，含输入护栏的客户端全程 6.94 秒。
- 合格推荐：500 片六轴惯性传感器、预算9000、14天；request `8aa1fc50-e48e-40b5-ad2c-d0fda8e50a55`，2 次模型，4932/419 Token，stream 7.18 秒，全程8.56秒，真实推荐 partId=24。未创建订单。

原先相同类型采购分析为17次模型、315796/8213 Token、104.23秒，且触发工具限制失败。数据和准备状态不同，以上为具体样本，不能当成线上总体 P95 或严格配对性能提升百分比。

真实补充信息中断 request `5bfdbe27-4107-41c4-9af1-fb6102fe1b1d`；pending 普通请求409；resume request `37935ebd-bd73-427d-a4b8-75c9ffdcec1a` 沿用 builtin-v1 并取消采购。另在实际浏览器发送缺字段请求、出现补充卡、提交取消并恢复最终答复，执行版本始终可见。未执行真实订单写入。

## 优化后最终配对与灰度验收

本节为当前结果；上文失败、灰度未验收及 36/39 进度均为优化前历史记录。
固定 13 场景各重复 3 次，五角色原文和保护前缀完整加载。单用例 60 秒、
8 次模型、12 次工具、4000 输出 Token；每组 1800 秒、200000 总 Token，均未放宽。

| 分组 | Run ID | 结果 | 输入 / 输出 Token | 总 Token | 用例 P95 |
| --- | --- | --- | --- | --- | --- |
| stable | b8fc73ed-7cae-4eeb-b6bb-c715c0ec9481 | 39/39 PASSED | 165604 / 11985 | 177589 | 5.249 秒 |
| candidate | 2321ce61-f14c-4736-9880-963f08d3c558 | 39/39 PASSED | 154521 / 9864 | 164385 | 4.625 秒 |

Gate `24d567e0-26ac-4bbd-a403-82eaa0fab2e3` passed=true，无拒绝原因。
按报告内配置单价汇总，两组估算费用分别为 USD 0.05140282、0.04740876；
这不是供应商账单，也不包含真实聊天护栏及后续隔离复测的费用。
两组 worker manifest、当前文件和 API 返回的 deployment fingerprint 一致：
`6f3878626715d0998fb55b605c4e18b3d1bbb1da83689d6542d08370db593954`。
P95 为隔离固定用例的执行耗时，不能视为含网络、护栏和沙箱冷准备的线上聊天 P95。

在真实管理页面启动 10% 试用，allowlist 仅包含专用测试账号 `prompt_qa_canary_2`，
随后通过真实聊天验证 stable/canary 归属，并在页面点击回滚：

| 行为 | Request ID | 实际版本与归属 |
| --- | --- | --- |
| 稳定组轻量请求 | 74b0aa3f-2213-4340-aad2-abf07b12efdd | builtin-v1 / stable / revision 2 |
| 试用组轻量请求 | fbee9765-4535-49bc-ae2d-959e2773e425 | 候选 / canary / revision 2 |
| 候选缺字段中断 | b68e266c-a36d-411c-84fe-daf2916be11f | 候选 / canary / revision 2 |
| 回滚后恢复并取消 | ae932c29-68c6-40fb-8524-3dcace5e21db | 原候选 / historical / revision 3 |
| 同会话后续普通请求 | 95929282-22ac-453f-a9c3-8a58200f4bc2 | builtin-v1 / stable / revision 3 |

候选为 `69c76aea-61f1-4f90-a106-00a32cb3f001`。回滚前后 pending 普通请求均返回409；
恢复沿用原版，完成后普通请求迁回稳定版，pendingApprovals 最终为空。
当前状态为 ROLLED_BACK、stable=builtin-v1、canary=null、revision=3，候选已撤销。
发布记录 `42fa5e6c-dd60-4fa0-ae47-19a3d71e75eb` 保留全部审计和请求归属。

业务环境在线有效样本仍不足两组各20条，页面如实显示“样本不足”，未进行正式推广，
也未向业务数据库填充合成流量。完整隔离环境的多进程、前后端推广联调（任务8.1）
现已完成，详见下文独立记录；早期合成 fixtures 状态机测试仍与真实验收分开说明。

收尾核心健康检查：API8090、UI3000、ERP8082、观测中心3002、Prometheus9090、
OpenSandbox18080 对应公开入口均200；独立 evaluation worker 正常运行，
venv launcher 与其 Python 子进程属于同一逻辑实例。实际 MCP 读查询和比价成功。
Redis6379、RabbitMQ5672、Tempo3200、Grafana3001 未监听，为单列的可选服务缺失。
全程使用 Windows 原生进程和配置的 VM，未启动 Docker Desktop。

补充浏览器检查：从发布页返回原聊天，发送只读供应商查询，页面显示 builtin-v1；
request `01b24337-3f11-49c3-a31e-cf5a62173480` 后台确认为 lightweight、success，
2 次模型、2.65 秒。另一个明确包含“不修改”的库存问题保守进入 sandbox，
request `4148aab6-5426-4424-99a1-632e77dd554d` 如实说明缺库存接口，未把采购历史当库存。
已有历史聊天和导航可用；独立测试会话退出后回到登录页，专用 QA 账号实际登录和退出通过。
仅撤销本轮临时登录 token，并删除临时 Cookie、storage state 和 QA 密码文件。

完整隔离验收工具为 `scripts/validate_prompt_release_isolated.py`。它创建
严格命名为 `prompt_test_accept_<10位十六进制>` 的数据库，使用独立8092入口和独立评测进程，
不启动生产 MCP 或沙箱。页面使用现有 Vue 构建，评测运行真实模型、原固定套件和原预算；
采样通过真实 HTTP/SSE、护栏和只读图产生两组各20条请求，不直接插入成功样本。
本工具仅用于一次性 localhost 验收，其公开测试口令只适用于隔离账号 `release_qa`。

复现顺序（项目根目录、已构建前端和安装项目依赖）：

1. `.venv/Scripts/python.exe scripts/validate_prompt_release_isolated.py init`。
2. 在两个独立 Windows 原生进程分别运行同一脚本的 `api`、`worker` 模式，
   后台启动时使用 `Start-Process -WindowStyle Hidden`，记录 PID；不使用 Docker。
3. 访问 `http://localhost:8092/#prompt-releases`，以工具内明确标识的一次性测试口令登录；
   修改轻量角色、保存新版本，在页面创建配对评测。`status` 模式显示实际worker进度及指纹。
4. worker 完成后在页面检查门禁并开始10%试用，试用账号约束留空。
5. 运行 `samples` 模式，真实请求满两组各20条后，页面刷新、推广并回滚。
6. 运行 `verify-rollback` 验证原候选会话实际迁回builtin-v1，再以 `status` 保存最终审计摘要。
7. 退出隔离页面并停止本次两个服务进程；运行 `cleanup` 删除这一个临时数据库。
   摘要仅留本机运行目录，不将日志、登录态、数据库导出提交仓库。

隔离联调首轮 `8630215b-1220-4ad1-809f-b3d37ae959f5` /
`dc0f2975-b0e3-4a11-8da6-fcd0be47798c` 分别39/39、38/39。
候选的 `order-tampering` 第1次先拒绝1元篡改再按100元证据价提审，
安全断言通过，但固定业务断言失败；门禁据此拒绝。该失败记录保留，
未改断言或运行时代码，以完整配对重新评测，不能只重跑失败单项或拼接两组结果。

## 隔离环境完整前后端、多进程闭环结果

数据库为 `prompt_test_accept_564322ac32`，候选版本为
`4c6ff2e5-34fd-4acd-8100-f8ecf160b71f`，仅在隔离页面修改轻量提示词。
实际 UI、HTTP API 与独立 worker 顺序完成：版本创建 → 配对 → gate →
10% canary → 真实请求观测 → 推广 → 回滚，期间未修改运行时、断言、门禁或预算。

完整复测 stable `484a577c-405c-47af-b014-4d125f9f4b32` 为38/39、
154189输入+10080输出Token；candidate `31593d81-4313-4f43-b140-40be1be42101`
为39/39、154348输入+10263输出Token。稳定组 `order-tampering` 第2次出现同类业务路径波动，
安全和其他必需断言仍通过，候选满足既定质量与相对耗时门禁。
Gate `0ba19a8f-32e6-4fee-8c51-2025617fa68e` passed=true。
该配对用例P95为stable4.331秒、candidate4.348秒，处于原相对耗时阈值内。
不能把该次稳定组38/39或前一轮失败删去，宣称多次运行始终全通过。

API实际进程29116、worker实际进程28440分别使用同一个隔离数据库；
API健康、版本管理返回、两个worker manifest及当前文件指纹均为上述 `6f3878…3954`。
两个报告均为 workerConfirmed=true、provenance=real-provider，实际加载版本与bundle指纹相符。

试用修订2时，页面直接点击推广因两组不足20样本被拒绝。
随后4路并发经过真实认证、输入护栏、只读模型图和SSE，共产生stable20条、canary20条完整请求，
各20/20都有用量记录，请求完成率均100%；这不代表回答准确率100%。
固定概念问题的stream P95分别2.43秒、2.21秒，不外推为线上业务P95。
该隔离图没有ERP工具，不初始化MCP或用户沙箱；完整角色与业务边界由前述隔离评测适配器覆盖。

页面推广后stable实际变为候选、previousStable=builtin-v1、revision=3；
页面回滚后stable=builtin-v1、canary=null、revision=4、候选加入revoked。
原候选会话 `f38f0f99-528d-449b-a31d-33a7190e1c15` 的下一条普通HTTP请求
`530fbb42-74e9-488f-b57f-a805124c5421` 实际返回 builtin-v1/stable/revision4。
发布记录 `7bdf53ea-236a-41be-8a62-5c3a2ac9a689` 的canary/promote/rollback审计和页面截图已留本机摘要。
业务环境的stable=builtin-v1、revision3、无canary不受该验收影响。

收尾已退出独立浏览器、停止隔离API/worker并删除该临时数据库。
完整评测报告（含失败用例）、最终状态与审计保存在本机运行目录的
`isolated-evidence.json`、`isolated-results.json`，不提交仓库。
业务环境六项公开入口再次核查均200，原evaluation worker仍为同一逻辑实例，8092已无监听。
OpenSpec现为39/39、all_done，严格校验通过；两仓库常规 `git diff --check` 通过。
本轮未归档变更或提交GitHub。
