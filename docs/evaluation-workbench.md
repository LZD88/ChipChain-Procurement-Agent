# 评测工作台

入口：[评测中心](http://localhost:3002/#evaluations)。先在采购助手登录，两个页面使用相同主机名（均用 localhost）。

## 新建评测

在页面顶部选择套件、验证方式、场景和执行次数，点击“开始评测”。真实 Agent 调用使用实际模型和隔离的业务工具，订单仅模拟；“审批边界模拟”沿用原有模拟执行器，不产生真实模型准确率。

下方报告自动更新已完成样本数。支持取消、下载 JSON、把已完成报告设为比较基线；未完成或取消的运行不能设置基线。查看权限和维护权限继续由服务端控制。

## 实验对比

两组共享选定场景、fixture 和重复次数，可选择项目已审核的 deepseek-flash / deepseek-chat、已有提示词版本、当前技能 / 不加载技能。技能对照实际控制技能文件和中间件加载，建议选择“采购技能读取与分析”场景观察差异。普通问答本来就可能不使用技能。

比较场景通过率、P95、Token 和成本估算，标出从通过变成失败的样本，同时显示模型、提示词、技能和代码指纹。新任务执行当前部署；代码升级前后的效果通过两份兼容的历史报告比较，不从网页执行上传代码或切换服务器代码。

成本单价可选，未配置时明确显示缺失，不填写假定价格。例如在本地 .env 配置供应商当前实际单价（单位：每千 Token）：

```dotenv
EVALUATION_PRICE_DEEPSEEK_FLASH_INPUT_PER_1K=<实际输入单价>
EVALUATION_PRICE_DEEPSEEK_FLASH_OUTPUT_PER_1K=<实际输出单价>
EVALUATION_PRICE_DEEPSEEK_CHAT_INPUT_PER_1K=<实际输入单价>
EVALUATION_PRICE_DEEPSEEK_CHAT_OUTPUT_PER_1K=<实际输出单价>
EVALUATION_PRICE_CURRENCY=CNY
```

任务创建时冻结价格和模型配置。不同币种的估算需要分别查看，不能直接相减。真实调用仍保留每场景 8 次模型调用、12 次工具调用、4000 输出 Token、全任务 200000 Token 和 1800 秒预算。

普通实验使用独立 `experiment` 模式，不能充当提示词发布证据；原发布预算、审批、安全断言和灰度门禁保持有效。

## 失败案例

展开已结束报告中的失败场景，点击“保存为回归案例”。服务端从已有评测读取输入、固定测试数据、原结果、预期断言和版本信息，不接受浏览器伪造执行结果；重复点击不会重复建案例。

在“失败案例”中选择修复后的提示词、模型或技能配置，点击“修复后重跑”。使用当前代码重新执行原场景，保留原预期。真实模型案例重跑三次；只有最新重跑完成、样本齐全、全部断言通过才显示“已验证修复”。失败、超时、取消和缺样本均不算修复。原始快照和历次重跑编号保留。

线上请求失败时会保存脱敏后的本轮问题到当前账号的失败收件箱（不采集推理过程和整段历史）。在“从我的线上失败请求保存案例”中选择请求和对应的检查场景，即可保存为回归案例。重跑保留原问题，但业务数据使用固定隔离 fixture；涉及多轮上下文、真实库存或附件的失败需要结合原会话核查，单轮回放不能宣称完整复现线上环境。审批恢复失败不自动截取为单轮问题。仅显示当前账号最近 30 天记录。

测试数据改变时禁止静默替换原数据，需恢复相应版本后再重跑。

## 数据与运行

MySQL `agent_db`：复用 evaluation_suite_versions、evaluation_runs、evaluation_cases、evaluation_assertion_results、evaluation_baselines、evaluation_audit_events；新增 evaluation_failures、evaluation_failure_replays 和 evaluation_online_failures。应用初始化按缺失表建表，不清理已有数据。Worker 继续仅具有评测结果写权限，不能改发布状态或生产订单。

Windows 原生启动应用和评测 worker；OpenSandbox 由 start_web.py 管理指定虚拟机。无需 Docker Desktop。serve.py 仅为明确列出的评测路径转发写请求，保留会话、请求头、64 KiB 限制及服务端鉴权。Nginx 参考部署也更新了相同操作入口。

## 本地验收（2026-11-13）

- 57 项后端回归通过，覆盖失败冻结、重跑修复判定、取消、租约隔离、权限、线上失败归属、敏感值脱敏、技能成功读取和发布门禁隔离。
- 前端数据展示测试及代理权限、方法白名单、请求大小测试通过；一个依赖外部报告文件的可选测试未提供文件而跳过。
- 独立浏览器实际点击：选套件、创建任务、看到 1/1 进度、设基线、取消并禁止设置基线、查看实验、查看失败案例，7 项检查通过，无页面错误或水平溢出。
- 模型对照实验：`93cdf37b-4415-49ea-b7f1-e892eda7445a`，flash/chat 均执行问答和库存查询。
- 技能对照实验：`eb33b4e6-6d53-432d-ac34-7770b9a95be8`，当前技能组成功读取文件并通过；不加载技能组 `skill_loaded=false`，保留真实失败。
- 保存上述失败：`b68bc95b-4f45-4546-adf6-f383a0b91812`；恢复当前技能后重跑 `2c277888-94ec-47eb-8e67-e5d2687d355b`，三次成功，列表显示“已验证修复”。
- API/UI、ERP、MCP（ping 与 11 项工具）、观测中心、Prometheus、OpenSandbox、Nacos、Langfuse、Higress 健康检查通过，Windows 评测 worker 进程存活。原有业务 Redis、RabbitMQ、Tempo、Grafana 未运行，单独记录为可选服务缺失。
- 提示词发布状态保持 `builtin-v1`、无试用版本、revision=3、ROLLED_BACK。未启动 Docker Desktop，未提交 GitHub。
