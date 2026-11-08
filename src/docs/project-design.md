# 芯链智能采购助手项目设计文档

版本：V1.0｜更新日期：2026-10-30｜项目：ProcurementMultiAgent

芯链智能采购助手面向供应商比价、物料采购分析与订单办理，将自然语言交互、企业 ERP 数据、沙箱计算和人工审批连接为一套可恢复的业务流程。系统采用主 Agent 协调、专业子 Agent 执行的分工方式，通过 MCP 接入业务系统，以 MySQL 保存执行状态和会话记录。

本设计覆盖当前 Python 服务、Vue 前端及其对 Java 芯链系统、OpenSandbox、模型服务和图表 MCP 的集成。当前实现与后续建设项分别说明；Java 服务内部实现不属于本代码库的设计范围。

## 1 项目定位与业务目标

### 1.1 业务背景

采购工作需要在供应商资料、物料报价、库存预警、历史订单和外部市场信息之间反复切换。单次查询容易完成，但形成有依据的采购建议，通常还需要数据核对、计算、图表展示和人工决策。订单写入又涉及实际业务状态，必须把自然语言建议与真实执行之间的确认过程明确下来。

本项目将这些步骤组织为“理解需求—收集证据—执行分析—形成建议—人工确认—执行订单”的协作链路。分析结果可用于后续订单沟通，订单执行仍由专门工具和审批节点控制。

### 1.2 主要使用场景

| 场景 | 用户需求示例 | 系统交付 |
| --- | --- | --- |
| 供应商比价 | 对比某类传感器的供应商报价 | 报价对比、数据口径、图表与建议 |
| 物料采购分析 | 分析某芯片近期采购价格变化 | 历史订单与物料数据分析、外部价格参考 |
| 库存辅助决策 | 查看库存预警并给出补货建议 | 预警信息、补充数据需求与采购建议 |
| 订单创建 | 为指定物料创建采购订单 | 信息补齐、待审批参数、ERP 执行结果 |
| 订单调整 | 修改交期、金额或订单状态 | 目标订单确认、变更审批与更新结果 |
| 采购报告 | 输出一份采购对比报告 | 结构化报告、图表和分析文件 |
| 技能扩展 | 安装并分配新的采购分析技能 | 技能文件、沙箱验证与作用域分配 |

### 1.3 系统边界

ERP 负责业务数据与订单状态的最终落地；助手负责意图理解、流程编排、工具调用和结果解释。外部价格仅作为带来源的参考，应核对币种、税费、批量阶梯、时间和规格后再比较。

当前未实现独立审批人体系、企业组织角色权限、采购付款、合同签署或自动资金操作。订单工具调用前的人工确认也不等同于 ERP 内部“已审核”等业务状态，二者需要分别管理。

## 2 总体架构

### 2.1 架构思想

系统采用围绕模型建立执行环境的 Harness 架构：模型承担理解、规划与表达，运行框架提供工具、文件系统、技能、上下文、状态保存和中断恢复，应用层提供登录、交互及业务入口。

这使复杂采购任务能够在明确的工具范围内执行，并在需要补充信息或审批时停下来，依靠检查点继续完成后续步骤。

### 2.2 系统组成

```plain
采购用户
  │ 自然语言请求、补充信息、审批决定
  ▼
Vue 3 工作台
  ├─ 登录注册、会话列表、Markdown 答复
  ├─ 执行过程、图表预览、人工确认组件
  └─ HTTP 请求 / SSE 流式接收
  │
  ▼
FastAPI 应用层
  ├─ Cookie 身份认证与会话归属校验
  ├─ NeMo 输入安全检查
  ├─ Chat / Resume / History API
  └─ AgentLoader：预计算组件 + 按请求创建执行图
  │
  ▼
DeepAgents / LangGraph 编排层
  ├─ 主 Agent：需求理解、任务委派、记忆与技能管理
  ├─ procurement-analyst：采购分析、沙箱计算、图表与报告
  └─ procurement-order：订单查询、信息补齐、审批后创建/更新
  │
  ├──────── MCP 工具层 ────────┬─ ERP MCP → Java 芯链系统 → 业务数据
  │                           └─ 图表 MCP → 图表生成结果
  ├──────── 外部检索 ─────────── 博查搜索 / 沙箱网页采集
  ├──────── 用户执行环境 ─────── OpenSandbox → Python、文件、技能
  ├──────── 会话存储 ─────────── MySQL → 检查点、展示历史、账号、绑定
  └──────── 文件路由 ─────────── CompositeBackend → 沙箱 / StoreBackend
```

### 2.3 分层职责

| 层次 | 主要组件 | 设计职责 |
| --- | --- | --- |
| 展示层 | Vue、ChatWorkspace、InterruptBanner、ChartPreview | 按问题组织答复，展示执行过程与人工交互 |
| 接口层 | FastAPI、auth、chat、history | 认证、参数校验、流式传输、历史读取 |
| 编排层 | DeepAgents、LangGraph、AgentLoader | 任务委派、图执行、检查点、中断恢复 |
| 业务工具层 | FastMCP、HTTPX、MCP adapters | 将 Java REST API 转换为模型可调用工具 |
| 分析执行层 | OpenSandbox、技能脚本、图表入口 | 数据处理、文件操作、计算与可视化 |
| 状态层 | MySQL、InMemoryStore、沙箱文件系统 | 区分执行状态、界面历史、偏好与工作文件 |

### 2.4 关键技术选型

| 技术 | 当前项目配置 | 用途 |
| --- | --- | --- |
| Python | 3.11–3.13 | Agent、API、MCP 服务 |
| DeepAgents | 0.7.18 | 主子 Agent、技能、文件后端、摘要 |
| LangChain / LangGraph | 1.4.2 / 1.2.12 | 模型与工具抽象、状态图运行 |
| FastAPI / FastMCP | 0.139.0 / 3.2.4 | Web API / ERP MCP 服务 |
| NeMo Guardrails | 0.24.1 | 直接用户输入的策略检查 |
| OpenSandbox SDK | 0.1.7 | 用户执行环境管理 |
| MySQL checkpoint 组件 | 3.0.0 | LangGraph 检查点保存与恢复 |
| Vue / Vite | package.json 声明 ^3.4.21 / ^5.2.8 | Web 工作台与开发代理 |
| 业务与摘要模型 | 配置中的模型标识为 deepseek-flash | 主 Agent 与摘要调用；子 Agent 未单独指定模型 |

以上为项目声明或代码配置，不表示远端模型服务的实际别名映射，也不等同于所有机器的已安装版本。

## 3 多 Agent 协作设计

### 3.1 职责划分

| Agent | 核心职责 | 显式绑定的主要工具 | 单次运行调用上限 |
| --- | --- | --- | --- |
| 主 Agent | 理解需求、委派任务、管理偏好与技能、汇总最终答复 | web_search、assign_skill、download_sandbox_file；框架提供任务与文件工具 | 模型 50 次、工具 200 次 |
| procurement-analyst | 供应商比价、物料行情、采购策略、成本与库存分析 | supplier_query、part_query、part_search、part_by_supplier、order_search_details、inventory_warning、generate_chart、web_search | 模型 50 次、工具 200 次 |
| procurement-order | 订单查询、创建、修改和相关状态调整 | order_create、order_update、order_search_details、web_search、request_order_info | 模型 20 次、工具 50 次 |

主 Agent 的显式工具列表没有直接绑定 ERP 业务工具；业务请求通过子 Agent 完成。分析子 Agent 获得业务查询工具，订单子 Agent 获得写入工具和人工中断能力。该分工约束的是工具分配，不能替代 Java API 及 MCP 服务自身的身份和权限控制。

### 3.2 委派与结果回收

主 Agent 根据任务目标选择专业子 Agent：比价、对比、评估、行情与报告类请求交给采购分析子 Agent；下单、订单更新、交期变更和取消类请求交给订单子 Agent。用户的预算、交期、供应商偏好等已知约束随任务传递。

子 Agent 返回业务结论和执行结果，主 Agent 组织面向用户的最终答复。工作台把中间文本、子 Agent 输出和工具调用放在可展开的执行过程内，正文优先展示主助手最后答复，减少重复信息。

### 3.3 Graph Factory 设计

当前采用“启动时预计算、请求时构图、按用户复用沙箱”的工厂模式。

1. 启动时加载 ERP 和图表 MCP 工具，创建统一图表入口，保存为 PrecomputedContext。
2. 请求到来时从服务端登录态取得 user_id，获取该用户的沙箱代理。
3. 创建 CompositeBackend，绑定用户记忆命名空间，构建依赖沙箱的工具和中间件。
4. 调用 create_deep_agent 创建本次请求的执行图。
5. 执行时使用目标 thread_id 读取或更新 MySQL 检查点。

可复用的工具定义避免重复发现；需要身份和沙箱绑定的对象按请求创建。同一用户的多个会话共享沙箱，但各会话的图状态通过 thread_id 区分。

## 4 核心业务流程

### 4.1 普通对话流程

```plain
用户提交消息
  → 验证登录 Cookie
  → 已有会话检查归属
  → 检查输入安全
  → 新会话登记 thread_id 与 user_id
  → 获取用户沙箱并创建执行图
  → 加载检查点、注入本次运行身份
  → 主 Agent 处理或委派子 Agent
  → SSE 推送答复与执行事件
  → 保存展示消息，发送 done
```

输入检查在创建新会话、写入用户消息及分配本次业务沙箱之前完成。被拦截的新消息不会形成新的业务会话记录。纯问候通过全部安全检查后，可由配置中的 greeting 流程直接回复，跳过业务模型，仍保存本轮问答。

### 4.2 采购分析流程

以“对比某种温度传感器的供应商报价并给出采购建议”为例：

1. 主 Agent 明确物料、数量、预算、交期等约束，按需读取用户偏好后委派分析任务。
2. 分析子 Agent 调用 ERP MCP 查询供应商、物料报价、历史订单及库存预警。
3. 有外部核价需求时，利用报价 URL 映射、博查搜索或网页抓取补充参考信息。
4. 在用户沙箱内使用 Python 整理数据、核对单位、计算价差与趋势，并保留必要中间文件。
5. 调用 generate_chart 生成适合任务的图表，输出数据口径、比较依据、风险和建议。
6. 将报告和文件路径返回主 Agent，由主 Agent 汇总；需要时将沙箱产物复制到服务端下载目录。

分析中的预算、交付能力和质量评价必须有数据支撑。没有质量、交付或需求预测数据时，应保留待确认项，不能把模型估计写成 ERP 事实。

### 4.3 订单创建与修改流程

```plain
理解订单需求
  → 查询并定位物料 / 订单
  → 参数完整？
      ├─ 否：request_order_info → interrupt → 用户补充 → resume
      └─ 是：准备 order_create / order_update 参数
  → 工具执行前暂停，展示待审批动作
  → 用户 approve / reject
      ├─ approve：恢复执行 → ERP API → 返回实际结果
      └─ reject：拒绝该动作，继续向用户反馈
  → 保存检查点与展示记录
```

两个人工交互节点的职责不同：request_order_info 处理信息不足；interrupt_on 对创建和更新订单进行执行前审批。恢复接口允许补充文本，或固定的 approve/reject 决策，不接受借审批请求夹带修改后的工具参数。

创建订单的明细至少需要 partId、quantity、unitPrice。MCP 层将 Python 参数映射为 Java API 字段，补齐默认订单号、订单时间并序列化请求。接口返回错误时，答复应明确操作未成功，不能以已发送请求代替订单成功。

当前订单号生成采用日期加三位随机数，不构成高并发唯一性或幂等保证；上线前应由业务侧唯一约束、编号服务和幂等键共同保障。

### 4.4 中断与恢复

LangGraph 检查点保存图状态和待恢复信息，前端展示历史单独保存。发生中断时，接口先保存当前展示内容，发送 interrupt 及 interrupted=true 的 done 事件，结束本轮 SSE。

用户完成操作后，前端向同一 thread_id 的 resume 接口提交数据；服务端校验身份和恢复结构，再通过 Command(resume=...) 继续执行。检查点让多步任务可恢复，但尚不能据此宣称 ERP 写操作具备 exactly-once 语义。

## 5 MCP 工具与业务集成

### 5.1 ERP 工具映射

| MCP 工具 | Java REST 接口 | 使用方 |
| --- | --- | --- |
| supplier_query | GET /suppliers/search | 分析子 Agent |
| part_query | GET /parts/page | 分析子 Agent |
| part_search | GET /parts/search | 分析子 Agent |
| part_by_supplier | GET /parts/supplier/{id} | 分析子 Agent |
| inventory_warning | GET /inventory/warning | 分析子 Agent |
| order_search_details | GET /orders/search-details | 分析与订单子 Agent |
| order_create | POST /orders/create | 订单子 Agent，执行前审批 |
| order_update | PUT /orders/update/{id} | 订单子 Agent，执行前审批 |

FastMCP 以 Streamable HTTP 暴露工具。服务生命周期中维护一个 HTTPX 异步客户端，统一使用 Java 服务基地址；当前超时为 30 秒，最大连接数 100，保活连接数 20。

模型通过结构化工具参数访问业务 API，不直接使用 SQL 改写业务库。业务完整性和用户权限仍应由 Java 服务校验。

### 5.2 工具发现与分配

MultiServerMCPClient 启动时分别连接 ERP MCP 和图表 MCP，按 supplier_、part_、inventory_、order_、generate_ 等名称前缀分组。创建 Agent 时再按完整工具名称绑定，避免把所有业务能力都暴露给所有 Agent。

ANALYSIS_MCP_URL 为必填配置。现有启动流程要求完成工具加载，尚未实现图表服务不可用时自动进入纯查询模式。

### 5.3 版本化采购策略与规范化证据

`procurement_compare` 的 ERP 查询仍位于 MCP 适配层，但评分、硬约束、稳定排序和推荐结果已抽取到无网络、无数据库副作用的 `src/agent/policies/procurement.py`。在线工具、后续证据回放和离线评测必须复用同一个策略实现，不能复制一份相似的评分逻辑。当前策略标识为 `supplier-score-v1`。

`src/agent/policies/registry.py` 按完整版本号登记策略，并明确记录是否支持回放。查询未知、已退役或不可回放的版本会返回不同错误，不会静默替换为当前版本。决策或评测基线登记引用后，该策略版本不能退役；只有解除全部引用后才能执行退役操作。

`src/agent/policies/canonical.py` 定义 `procurement-json-v1` 规范化协议：对象键按 JSON 规则排序，Unicode 直接以 UTF-8 表达，null 保持 null，Decimal 与浮点数转成有限的规范十进制字符串。来源中的物料候选属于无序事实，按稳定业务标识排序；结果中的候选顺序代表策略排名，必须保留。完整证据指纹覆盖规范化版本、策略版本、请求、来源事实和结果，使用完整 SHA-256 保存，并可由其前 16 个十六进制字符生成展示用决策号。

验证命令：

```shell
python -m pytest -q src/test/test_procurement_compare.py src/test/test_policy_equivalence.py src/test/test_policy_canonical.py src/test/test_policy_registry.py
```

### 5.4 统一图表入口

图表适配器将 26 种已识别图表组织为比较、趋势、分布、指标、层级、关系、地图七类，对模型暴露一个 generate_chart(chart_type, chart_config) 入口。

调用链为：选择类型 → 按原始 MCP schema 校验 → 检查跨字段约束 → 调用对应远端工具 → 保留文本、图片及结构化 artifact → 返回前端。

工具描述只保留常见参数形状，详细选项与示例保存在 /skills/analyis_chart_params.md，供 Agent 按需读取。该设计减少每次调用携带的重复说明，并使新增约束集中维护。

校验覆盖有限数值、分组与堆叠关系、双轴数据长度、层级深度及图节点引用等。对已知结构图 dark 主题兼容错误，仅做一次 default 主题重试，并返回主题变化提示。不能把远端失败包装为图表生成成功。

generate_spreadsheet 等未合并工具保存在 extra_mcp_tools 中，当前尚未加入 Agent 的 tools 列表，因此不能将其描述为已开放的表格生成能力。

## 6 技能与上下文设计

### 6.1 技能作用域

| 作用域 | 当前技能 | 使用方式 |
| --- | --- | --- |
| /skills/main/ | find-skills、skill-management | 主 Agent 查找、创建、验证和分配技能 |
| /skills/procurement/ | procurement-analysis、supplier-price-urls、web-scraper、procurement-optimization | 分析流程、价格来源、网页采集和优化方法 |
| /skills/order/ | purchase-order-summarization | 订单摘要与风险分析方法参考 |

技能是按需读取的操作说明与配套脚本。技能文件存在不代表它描述的所有第三方 API、数据字段或求解器已经接入；实际可执行能力以工具绑定、依赖安装和数据可用性为准。

### 6.2 技能同步链路

本地预置技能通过 SkillsSyncMiddleware 增量同步到沙箱；运行期分配的技能通过 assign_skill 写入目标作用域并保存到 Store；UserSkillsRestoreMiddleware 将 Store 中的技能恢复到沙箱；DeepAgents 的技能能力负责发现和按需加载。

当前技能 Store 使用共享的 ("skills",) 命名空间，按 main、procurement、order 区分作用域，没有按用户分区。该设计适合共享技能库，若开放普通用户上传和改写，则需要补充技能版本、发布权限和隔离策略。

### 6.3 用户上下文与记忆

Web 身份来自服务端登录会话，通过 ProcurementContext 传入执行过程。UserInfoInjectionMiddleware 在 wrap_model_call / awrap_model_call 阶段，将本次用户身份附加到模型系统提示，不把身份提示作为聊天消息写入历史。

身份提示明确区分“助手身份”和“当前登录用户”，偏好文件使用 /memories/{user_id}/preferences.md。用户询问身份或简单问候时不必读取偏好；处理采购任务或修改偏好时再按需访问。

主 Agent 和分析子 Agent 配置摘要中间件，可通过 compact_conversation 压缩长任务上下文。摘要服务于上下文管理，不应替代报告中的关键数据来源和计算依据。

## 7 存储设计

### 7.1 存储职责与生命周期

| 数据 | 位置 / 实现 | 生命周期 |
| --- | --- | --- |
| ERP 物料、供应商、订单、库存 | Java 芯链系统业务库 | 由业务系统管理 |
| 图执行状态与中断 | MySQL checkpoint 表 | 跨请求、跨应用进程重启保存 |
| 前端展示历史 | session_display_messages | 跨请求、跨应用进程重启保存 |
| 账号、登录态、会话归属 | app_users、auth_sessions、chat_threads | MySQL 保存，登录态有到期时间 |
| 用户与沙箱绑定 | sandbox_registry | 记录绑定，不保存沙箱文件本身 |
| 用户偏好 | /memories/ → StoreBackend → InMemoryStore | 同进程内跨会话；进程退出丢失 |
| 运行期技能副本 | /persisted-skills/ → StoreBackend → InMemoryStore | 同进程内保存；进程退出丢失 |
| 代码、临时文件、分析产物 | OpenSandbox 默认后端 | 随沙箱生命周期 |
| 已导出文件 | src/download/ | 服务端本地磁盘，当前不是对象存储 |

CompositeBackend 将 /memories/ 和 /persisted-skills/ 分流到 StoreBackend，其余路径使用沙箱代理。记忆 Store 命名空间绑定 user_id，技能 Store 则是共享命名空间。

### 7.2 MySQL 表设计

| 表 | 关键标识 | 保存内容 |
| --- | --- | --- |
| checkpoints | thread_id、checkpoint_ns、checkpoint_id | 图状态快照、父检查点与元数据 |
| checkpoint_blobs | thread_id、命名空间哈希、channel、version | 序列化通道数据 |
| checkpoint_writes | thread_id、checkpoint_id、task_id、idx | 中间写入与恢复所需结果 |
| checkpoint_migrations | v | 组件 schema 版本 |
| app_users | username | 账号与 scrypt 密码哈希 |
| auth_sessions | token_hash | 用户、到期时间和登录会话 |
| chat_threads | thread_id | 会话所属用户 |
| session_display_messages | thread_id + message_index | 用于 UI 还原的逐条 JSON 消息 |
| sandbox_registry | user_id | OpenSandbox ID 与绑定时间 |

决策证据中心另外使用 `procurement_decisions`、`procurement_decision_events`、
`procurement_decision_claims`、`procurement_replays`、`app_capability_grants`、
`decision_audit_actions`、`procurement_decision_retention_holds` 和
`procurement_decision_tombstones`。证据列表依赖 owner/time 索引，claim 对决策、
claim token 和幂等键施加唯一约束。部署时 MCP 与评测 worker 应使用独立最小权限数据库账号。

版本化评测另外使用 `evaluation_suite_versions`、`evaluation_runs`、`evaluation_cases`、
`evaluation_assertion_results`、`evaluation_baselines` 和 `evaluation_audit_events`。套件版本按
suite/version 唯一且不可原地修改；worker 通过 runs 表的 lease 原子领取任务，场景与断言结果
增量写入，基线按 suite/execution-mode 唯一，发布、运行、取消、基线和导出动作写入独立审计表。

窄权限仅包含 `decision:audit`、`evaluation:view`、`evaluation:maintain`；
`APP_CAPABILITY_BOOTSTRAP` 为空即 deny-by-default。`DECISION_EVIDENCE_RETENTION_DAYS`
控制详细载荷保留期，活动 hold 会阻止清理。清理先写 tombstone 再清空 JSON；备份与恢复必须
把决策、事件、claim、回放、授权、hold 与 tombstone 作为一个一致性单元，恢复后验证外键和指纹。

检查点与展示消息用途不同：前者供运行框架恢复任务，后者供 UI 保留主子 Agent 来源、工具参数、文本与图片等显示信息。不能直接把 checkpoint_blobs 当作普通聊天 JSON 编辑。

### 7.3 一致性与初始化

MySQL 初始化、迁移或权限错误会中止应用启动，不自动降级到内存检查点。应用表使用 utf8mb4，时间按 UTC 写入。连接池当前最小连接数 1、最大连接数 10，连接回收周期 1800 秒。

删除会话时，同一会话的检查点、blob、write、展示消息和归属记录在一个事务中删除。展示消息批量替换也使用事务，插入失败时回滚。项目为 checkpoint 组件 3.0.0 的部分迁移做了兼容处理，升级组件前需要重新验证迁移和序列化兼容性。

## 8 沙箱与容错设计

### 8.1 用户级执行环境

每个用户维护一个 OpenSandbox，同一用户的多个 thread 共享它。沙箱用于代码执行、文件读写、网页采集和技能脚本运行；会话级执行状态仍由 MySQL 按 thread_id 管理。

当前创建配置为 2 CPU、4Gi 内存、2 小时初始超时，使用 code-interpreter:v1.0.2 镜像。沙箱内创建 /opt/skills-venv，预装 numpy、pandas、matplotlib、requests、beautifulsoup4、playwright，并校验导入结果。安装 Python 的 playwright 包不代表浏览器二进制和浏览器采集链路已完整验收。

### 8.2 预热与获取顺序

启动时尝试预热一个沙箱，预热失败允许应用继续启动，请求到来时再创建。获取用户沙箱的当前顺序为：内存代理缓存 → 认领预热沙箱 → 尝试数据库绑定重连 → 创建新沙箱。预热被使用后，后续用户到来可触发后台补充。

预热减少沙箱初始化对首个请求的影响，但当前没有可据此承诺的端到端延迟指标。多用户并发下的创建互斥、资源回收和多实例协调仍需完善。

### 8.3 代理热替换与健康恢复

CompositeBackend、工具和中间件持有 SandboxBackendProxy，而不是直接长期持有某个沙箱实例。重建时代理替换内部 backend，调用方可继续使用原代理对象。

SandboxHealthMiddleware 在 abefore_agent 阶段执行健康检查，失败时重建环境并重新上传 /AGENTS.md。基础技能和依赖可以重建，旧沙箱中的任意工作文件不会自动迁移。正常关闭应用会销毁已加载的用户沙箱及预热沙箱，并清理绑定。

### 8.4 故障处理

ToolErrorMiddleware 将工具异常转换为错误结果，便于模型解释或选择后续动作。SandboxCircuitBreakerMiddleware 检查连续沙箱相关错误，当前 threshold=2，实际条件为错误数大于阈值，即连续 3 次时触发结束保护。

容错遵循故障分级：普通工具异常尽量转为可解释结果；数据库初始化和输入审查失败则停止相应流程，避免在状态或安全检查不可用时继续业务执行。

## 9 登录与输入安全

### 9.1 账号和登录态

用户名规范为小写的 2–32 位字母、数字、下划线或短横线，密码长度为 8–128 位。密码以随机盐和 scrypt 派生值保存；登录令牌使用随机值，数据库只保存其 SHA-256 哈希。

浏览器通过有效期 7 天的 HttpOnly、SameSite=Lax Cookie 登录，Cookie 路径为 /api；HTTPS 部署使用 Secure。退出时撤销服务端登录会话。前端通过 /api 代理访问后端，写请求携带 X-Requested-With: DeepAgent，并配合明确的 CORS 来源限制。

主要聊天、恢复、会话读取与删除接口从登录态取用户身份，客户端 ChatRequest 不接受额外 user_id 字段。当前原始状态历史接口 GET /api/chat/{thread_id}/history 缺少同等的用户与归属依赖，需要补齐后再对外开放。

### 9.2 输入护栏

直接聊天文本、订单补充文本及 CLI 输入通过 NeMo Guardrails 检查。策略覆盖提示注入、凭据外传、跨用户访问和绕过审批等请求；正常业务下单请求本身允许进入后续审批流程。

| 配置项 | 当前值 / 行为 |
| --- | --- |
| 最大输入长度 | 16000 字符 |
| 输入检查总超时 | 15 秒 |
| 审查模型输出 | 明确 ALLOW 才放行 |
| 策略拦截 | HTTP 403 |
| 输入或恢复格式错误 | HTTP 422 |
| 审查服务异常或配置异常 | HTTP 503，停止本次执行 |
| 配置更新 | YAML / Colang 文件变更后在后续检查中重新加载 |

输入护栏当前不审查网页内容和 MCP 工具返回，也不承担数据权限校验。外部文本的间接提示注入、输出审查、业务权限和审计需要独立建设。

### 9.3 业务侧责任

订单审批发生在 Agent 调用工具之前。生产环境还需让 ERP MCP 和 Java API 验证调用方身份、业务权限、允许的状态流转及参数范围，防止绕过 Web 工作台直接调用写入接口。

## 10 Web API 与流式交互

### 10.1 主要接口

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| POST | /api/auth/register | 注册并建立登录会话 |
| POST | /api/auth/login | 登录 |
| GET | /api/auth/me | 恢复当前登录用户 |
| POST | /api/auth/logout | 退出并撤销登录会话 |
| POST | /api/chat/stream | 新消息，返回 SSE |
| POST | /api/chat/{thread_id}/resume | 恢复人工中断，返回 SSE |
| GET | /api/chat/{thread_id} | 当前会话消息 |
| GET | /api/history | 分页会话列表 |
| GET | /api/history/{thread_id}/messages | 展示历史 |
| DELETE | /api/history/{thread_id} | 删除会话及关联状态 |
| PATCH | /api/history/{thread_id} | 标题更新占位接口，当前未持久化实现 |
| GET | /health | 进程健康信息与启动实例标识 |

### 10.2 请求示例

新建会话：

```json
{"message":"请比较数字温度传感器的供应商报价","thread_id":null}
```

补充订单信息：

```json
{"resume":{"supplement":"数量为 100，期望交货日期为 2026-11-15"}}
```

同意当前待审批动作：

```json
{"resume":{"decisions":[{"type":"approve"}]}}
```

以上请求均需已有登录 Cookie；写请求需携带规定的请求头。示例不包含真实业务订单数据。

### 10.3 SSE 协议

每个事件使用 data: JSON 与空行分隔。事件通过 type 区分，模型文本与工具调用都携带必要的来源信息，工具事件使用调用 ID 关联，避免重复调用同名工具时发生覆盖。

| 事件 | 前端处理 |
| --- | --- |
| token | 追加文本，识别主 Agent 或子 Agent 来源 |
| tool_start | 建立工具执行条目 |
| tool_args | 增量拼接参数 |
| tool_result | 更新文本、图片与结果状态 |
| tool_end | 结束工具执行状态 |
| interrupt | 显示补充信息或审批组件 |
| done | 标记本轮完成，或进入等待人工操作状态 |
| error | 展示执行失败，不能误判为成功完成 |

前端使用 fetch 读取流式响应，可支持 POST 请求体；解析器处理分块、UTF-8 解码和不完整行。历史记录遵循相同的答复分组规则。停止按钮中止浏览器请求，但当前没有独立的后端任务取消与回滚协议。

## 11 核心亮点与设计价值

### 11.1 专业分工与可控写入

主 Agent 负责协调，分析子 Agent 专注数据与结论，订单子 Agent 聚焦业务动作。订单工具前设置人工中断，将“建议买什么”和“实际写入订单”衔接为清晰可见的业务步骤。

### 11.2 预计算与按请求构图结合

MCP 工具加载和图表适配在启动阶段复用，用户身份、沙箱及依赖它们的工具按请求绑定。这既减少重复发现，又避免用一个全局 Agent 固定绑定全部用户的执行环境。

### 11.3 可恢复执行与可读历史分别设计

MySQL checkpoint 保留任务执行状态，展示表保存适合用户阅读的消息结构。恢复执行和还原 UI 各自使用适合的数据模型，人工审批能够跨请求衔接，子 Agent 与图表也能保留在历史展示中。

### 11.4 沙箱代理支持故障后的继续工作

用户沙箱、预热、健康检查、代理替换和熔断共同管理执行环境。底层沙箱失效后，可以重建基础环境并继续使用原代理，减少工具和中间件对实例生命周期的耦合。

### 11.5 图表工具的统一入口与渐进式参数说明

26 种图表保留一个选择入口，schema 校验与业务约束在适配层统一执行；详细参数按需从技能资料读取。该设计控制工具描述体积，同时保留类型差异与错误反馈。

### 11.6 技能驱动的业务扩展

业务方法可以沉淀为 SKILL.md、脚本和参考资料，通过作用域分配到专业 Agent。相比每增加一种分析方法就修改主流程，技能机制更便于积累可复用的采购步骤与报告模板。

### 11.7 把身份、输入检查和审批分别落实

登录态回答“谁在操作”，输入护栏检查“本次输入是否符合规则”，订单审批决定“是否执行这次动作”，业务后端仍负责“该身份能否操作这条数据”。各机制的职责分别描述，便于后续补齐端到端权限。

### 11.8 最终答复与执行过程同时可读

工作台按用户问题聚合结果，把中间执行细节收进展开区域，图表仍可直接展示。用户可以先读结论，再查看工具参数、数据结果和人工确认过程。

这些亮点主要来自结构与能力设计。当前仅有默认冻结 fixture 的离线评测证据：
[硬约束断言 10/10、期望推荐 3/3、规则场景 6/6](../../docs/evaluation-results/rule-baseline.md)，以及
[workflow-safety 的危险写入断言 12/12、审批边界 6/6、场景 12/12](../../docs/evaluation-results/workflow-safety.md)；
原始导出和复现命令见项目 README 的“可复现评测结果”。这些数字不支持宣称线上准确率、
实际采购节省、生产吞吐或业务 ROI。

## 12 模块与代码组织

```plain
ProcurementMultiAgent/
├─ frontend/src/                 Vue 工作台、API 封装、图表与审批组件
├─ start_web.py                  Web 前后端启动器
├─ pyproject.toml                Python 依赖与包配置
├─ scripts/init_mysql.py         MySQL 初始化入口
└─ src/
   ├─ agent/
   │  ├─ main_agent.py           预计算上下文、主子 Agent 工厂
   │  ├─ config.py               模型、后端与路径配置
   │  ├─ mysql_storage.py        检查点兼容、应用表、事务与账号存储
   │  ├─ input_guard.py          输入检查与恢复数据校验
   │  ├─ backends/               沙箱创建、管理、代理与文件后端
   │  ├─ middlewares/            身份、技能、错误、健康与熔断
   │  ├─ guardrails/             YAML / Colang 安全配置
   │  ├─ tools/                  MCP、图表、搜索、HITL、技能和导出工具
   │  └─ memory/                 Agent 指引与提示词
   ├─ api_view/                  FastAPI 入口、加载器、认证与业务接口
   ├─ mcp_server/                ERP MCP 服务及业务工具映射
   ├─ skills/                    主 Agent、分析、订单作用域技能
   ├─ test/                      当前测试与验证入口
   ├─ deepagents_test/           框架和后端实验代码
   └─ download/                  沙箱导出的本地文件
```

实验代码与正式 Web 入口应分别维护，设计和部署以 api_view.web_main、agent.main_agent 及 mcp_server.server_main 的实际调用关系为准。

## 13 部署与运行

### 13.1 依赖服务

| 服务 | 默认地址 / 端口 | 说明 |
| --- | --- | --- |
| Vue 开发服务 | 3000 | /api 代理到 FastAPI |
| FastAPI | 8090 | start_web.py 默认端口 |
| ERP MCP | 127.0.0.1:8000/mcp | 可通过环境变量调整 |
| Java ERP | localhost:8082/api | JAVA_API_BASE_URL |
| OpenSandbox Server | localhost:8080 | 当前在 agent/config.py 配置 |
| MySQL | localhost:3306，agent_db | 独立保存助手状态 |
| 图表服务 | ANALYSIS_MCP_URL | 外部 MCP 地址 |

上述地址是开发默认值。容器中的 localhost 指向容器自身，沙箱访问宿主机或其他服务时需按部署网络配置地址。

### 13.2 启动步骤

在项目根目录安装依赖：

```shell
python -m pip install -r requirements.txt
npm --prefix frontend install
```

根据 .env.example 配置模型、MySQL、ERP MCP、图表 MCP 和 OpenSandbox 所需参数。已有 .env 应保留并修改，不覆盖已有配置。模型凭据使用环境变量，不能写入前端或设计文档。

先准备 MySQL、Java ERP、OpenSandbox 和可访问的图表服务，再运行：

```shell
python -B scripts/init_mysql.py
PYTHONPATH=src python -m mcp_server.server_main
```

另一个终端启动 Web：

```shell
python start_web.py
```

可选联网搜索读取 BOCHA_API_KEY。HTTPS 部署配置 AUTH_COOKIE_SECURE=true，并用 CORS_ORIGINS 指定可信来源。反向代理需支持 SSE，关闭响应缓冲并设置适合长任务的超时。

### 13.3 运行观测

现有日志包含启动、沙箱生命周期与异常；DEBUG 可查看更细的工具与同步信息。流式调试日志可能含用户文本、工具参数或结果，生产环境需要脱敏、访问控制和清理策略。

/health 返回进程健康状态和启动实例标识，当前不是 MySQL、模型、MCP 与沙箱的完整依赖探测。生产环境可分别补充存活检查与就绪检查。

## 14 当前边界与后续建设

| 优先级 | 当前情况 | 建设方向 |
| --- | --- | --- |
| P0 | 原始会话状态历史接口缺少用户归属校验 | 补齐认证与归属依赖；对全部会话路由进行越权验证 |
| P0 | Web 审批没有替代 ERP/MCP 服务鉴权 | 贯通服务身份、数据权限、审批依据和操作审计 |
| P0 | ERP 写操作缺少本层幂等机制 | 引入请求幂等键、业务唯一约束与重放结果查询 |
| P1 | 偏好和动态技能使用 InMemoryStore | 引入持久 Store，明确迁移、备份和版本策略 |
| P1 | 动态技能命名空间跨用户共享 | 区分公共技能发布与用户私有技能，增加审核及版本管理 |
| P1 | 沙箱注册依赖进程内缓存，创建过程缺少完整并发协调 | 用户级锁、预热任务去重、沙箱配额及多实例协调 |
| P1 | 同用户多会话共享沙箱工作目录 | 按 thread_id 划分产物路径，避免同名文件相互覆盖 |
| P1 | 导出工具只复制到服务端本地目录 | 增加鉴权下载 API、文件归属与过期清理，或接入对象存储 |
| P1 | 标题更新接口仍是占位响应 | 增加标题字段及持久化逻辑 |
| P1 | 客户端停止不等同于业务撤销 | 明确后端取消、审批重放和已执行操作的状态反馈 |
| P1 | 输入护栏未覆盖工具与网页内容 | 增加外部内容信任边界、输出审查与针对性评估 |
| P2 | 外部图表服务启动依赖较强 | 能力探测、可选服务降级和上游兼容回归 |
| P2 | extra_mcp_tools 尚未显式开放 | 按任务需要绑定独立表格工具并补充验证 |

建议分三步推进：先补齐权限与订单一致性，再完善持久化和文件交付，最后通过真实任务评估延迟、成本、分析质量与并发容量。吞吐、准确率和节省成本等指标应由可复现测试给出。

## 15 测试与验收设计

| 验收范围 | 核心验证内容 |
| --- | --- |
| 登录与隔离 | 未登录访问、Cookie 到期、退出撤销、跨用户会话访问 |
| 输入安全 | 正常采购请求放行、注入拦截、超长输入、审查超时、无效恢复结构 |
| 采购分析 | 工具数据与结论对应、指标计算可复现、缺少数据时不虚构结论 |
| 人工审批 | 缺失字段补齐、同意执行、拒绝不执行、恢复仍检查用户归属 |
| 检查点恢复 | 中断后重新请求恢复、数据库重连、嵌套子 Agent 状态恢复 |
| 历史展示 | 重复同名工具不串结果、子 Agent 来源正确、图表可回看 |
| 沙箱 | 预热失败、失效重建、代理引用连续性、工作文件生命周期 |
| 图表 | schema 约束、26 类路由、错误状态与主题兼容提示 |
| 订单一致性 | 重复请求、审批重放、网络中断后的业务结果核对 |

当前仓库中可见的验证入口包括 src/test/test_chart_generator.py、test_chart_live.py、test_skills_sync.py、test_web_search.py，以及 frontend/src/api 和 utils 下的测试。真实图表验证、ERP 集成和沙箱验证需要相应外部服务；验证命令存在不代表本版本已完成所有端到端验收。

```shell
# 离线图表适配与参数验证
python -B src/test/test_chart_generator.py

# 前端 API 与消息处理测试
npm --prefix frontend test

# 外部图表服务验证，需要配置真实服务
python -B src/test/test_chart_live.py --output /tmp/chart-live
```

## 16 设计依据与参考文档衔接

业务背景和早期结构参考：[基于 Harness 架构的芯链智能采购项目](https://www.yuque.com/zhuimeng-w3g4g/cubysy/poo0w2e87bi9iudz)。本版以当前代码中的实际入口、工具绑定和存储实现为准。

| 主题 | 当前实现依据 |
| --- | --- |
| Agent 工厂、分工与工具范围 | src/agent/main_agent.py、src/api_view/agent_loader.py |
| 模型、Store 与沙箱配置 | src/agent/config.py、src/agent/env_utils.py |
| 用户身份、认证与 API | src/api_view/auth.py、src/api_view/api/、src/agent/schema.py |
| 输入护栏与恢复校验 | src/agent/input_guard.py、src/agent/guardrails/ |
| 检查点与应用存储 | src/agent/mysql_storage.py |
| 沙箱、技能与中间件 | src/agent/backends/、src/agent/middlewares/、src/skills/ |
| 业务与图表工具 | src/mcp_server/、src/agent/tools/ |
| 工作台与流式展示 | frontend/src/ChatWorkspace.vue、frontend/src/api/chat.js、frontend/src/utils/ |
| 安装与服务启动 | pyproject.toml、frontend/package.json、start_web.py、README.md |

与早期参考文档相比，本版更新了全局懒加载到 Graph Factory 的结构变化、模型配置标识、主 Agent 调用上限、用户上下文注入方式和现有技能名称，并补充 MySQL、登录、安全、恢复和生产建设边界。偏好与技能的“跨会话”使用明确限定在当前内存 Store 生命周期内。
