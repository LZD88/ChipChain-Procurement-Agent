# 采购决策评测系统

评测系统用于证明规则与工作流在固定输入下的正确性、安全性和稳定性。它输出的是离线/测试证据，
不是线上准确率、采购节省或业务 ROI。默认套件位于
`src/evaluation/suites/default-v1.json`，冻结数据位于
`src/evaluation/fixtures/procurement-fixtures-v1.json`。

## 1. 套件编写与发布

套件是纯 JSON，顶层必须包含 `suiteId`、`title`、`owner`、`fixtureVersion` 和非空
`scenarios`。每个场景必须声明：

- 稳定且唯一的 `id`；
- `rule-contract` 或 `workflow-safety` 执行模式；
- 只引用冻结数据的 `fixture`；
- 至少一个客观 `assertions`；
- 可选的 1–300 秒超时与 1–20 次重复。

断言只允许预定义类型：`recommended_part_equals`、`candidate_eligible`、
`rejection_reason_contains`、`policy_version_equals`、`approval_required`、
`order_fields_equal`、`unsafe_write_count_equals`、`idempotency_outcome_equals` 和
`scenario_status_equals`。套件拒绝 `code`、`script`、`python`、`javascript`、`command`、
`shell`、`exec`、`eval` 等可执行字段，不接受自由文本模型裁判。

发布会计算完整内容 SHA-256 指纹。同一 `suiteId + version` 永不可原地修改；内容变化必须发布
新版本。历史运行始终解析其清单中记录的准确版本和指纹。

```powershell
& .\.venv\Scripts\python.exe -m agent.evaluation.cli validate-suite src/evaluation/suites/default-v1.json
```

## 2. Runner 隔离与模型重复

`rule-contract` 直接调用版本化纯策略注册表，不加载模型、MCP 或 ERP，固定为一次重复；相同套件、
fixture、策略和代码清单必须生成相同结果。

`workflow-safety` 只注册 fixture-backed 读取工具和不写入的订单模拟器。worker 启动时若发现
生产 ERP URL、用户名、密码或 Token 就在执行场景前失败；订单请求只记录为 intended call，
不会触发真实订单、库存或消息变更。其清单必须记录模型、provider、模型配置指纹与重复次数。
重复次数用于描述抽样稳定性，不能和确定性断言混成一个“准确率”。

## 3. 指标口径

所有比例指标都公开分子、分母、排除数、排除原因、单位和执行模式：

```text
rate = passed applicable assertions / applicable assertions
scenario_completion_rate = PASSED or FAILED cases / all produced cases
```

`hard_constraint_pass_rate` 只统计候选资格和淘汰原因断言；
`expected_recommendation_agreement` 只统计期望推荐断言；
`unsafe_write_prevention` 和 `approval_boundary_compliance` 只统计各自安全断言。
超时、配置错误、依赖错误与取消不会算作通过；无适用样本时显示 `NOT_APPLICABLE`，而不是
0% 或 100%。延迟报告 min/p50/p95/max；缺失 token 或价格时成本显示 `NOT_AVAILABLE`，
配置价格得到的成本只标记为 `ESTIMATED`。

## 4. 基线、比较和回归门禁

只有 `COMPLETED` 运行可设置为某个 `suiteId + executionMode` 的基线。直接比较要求 suite、
suite version、fixture version、fixture fingerprint 和执行模式一致；不兼容时拒绝汇总，不做
近似比较。切换基线会审计旧/新运行 ID。

比较展示指标 delta、推荐变化、断言退化和场景级候选差异。安全回归门禁阈值以指标最低可接受值表达；
`unsafe_write_prevention` 或 `approval_boundary_compliance` 退化时始终阻断，即使延迟或成本改善。

重复场景以 `(scenarioId, repetition)` 对齐比较，旧报告未声明 repetition 时视为第 1 次；每次执行均保留，
基线已通过而候选缺失的执行也会触发回归门禁。相同键重复出现时拒绝比较，避免静默覆盖。
`scenarioSummaries` 汇总每个场景两侧实际产生的样本数、通过数、通过率、状态分布及推荐分布；
通过率为 PASSED 数 / 实际产生样本数，超时或取消样本仍在分母内。波动表示重复样本的状态或推荐存在差异，
少于 2 个样本时不判断波动；观察结果一致不代表已证明模型稳定。看板的回归列表标出执行次数，并显示这些汇总。

```powershell
& .\.venv\Scripts\python.exe -m agent.evaluation.cli compare baseline.json candidate.json --threshold unsafe_write_prevention=1 --threshold approval_boundary_compliance=1 --output comparison.json
if ($LASTEXITCODE -ne 0) { throw "evaluation regression gate failed" }
```

门禁失败返回退出码 `2`，输入、模式或文件错误返回 `1`，通过返回 `0`。

## 5. 本地与 CI 命令

从干净 checkout 创建环境并验证默认套件：

```powershell
py -3.11 -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt
& .\.venv\Scripts\python.exe -m agent.evaluation.cli validate-suite src/evaluation/suites/default-v1.json
& .\.venv\Scripts\python.exe -m agent.evaluation.cli run-rule-contract --output evaluation-rule.json
```

最后一条命令不需要模型、MCP、ERP 或网络；断言失败返回退出码 `2`。需要人类摘要时使用
`--format markdown --output evaluation-rule.md`。也可安装项目后调用等价入口
`procurement-eval`。

完整专项回归：

```powershell
& .\.venv\Scripts\python.exe -m pytest -q src/test/test_evaluation_schema.py src/test/test_default_evaluation_suite.py src/test/test_evaluation_manifest.py src/test/test_evaluation_metrics.py src/test/test_rule_contract_runner.py src/test/test_workflow_safety_runner.py src/test/test_evaluation_queue_mysql.py src/test/test_evaluation_repository_mysql.py src/test/test_evaluation_comparison.py src/test/test_evaluation_api_cli.py src/test/test_evaluation_docs.py
```

## 6. API、权限和报告

认证接口位于 `/api/evaluations`。`evaluation:view` 可读套件、运行、基线、比较和导出；
`evaluation:maintain` 另外可发布套件、创建/取消运行和切换基线。未授权操作、变更操作与导出都写入
评测审计表。响应统一 `Cache-Control: no-store`。

JSON 与 Markdown 导出来自同一规范报告，包含清单、指标定义/数值、实际产生的场景结果和完整性
指纹。`FAILED`、`PARTIAL`、`CANCELLED`、`TIMED_OUT` 以及尚未结束的运行均明确标记
`INCOMPLETE`，不会补造未产生的场景结果。
