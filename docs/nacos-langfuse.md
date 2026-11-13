# Nacos 与 Langfuse 本地接入

服务运行在 VirtualBox 的 `opensandbox-vm` 中，使用虚拟机内部的 Linux Docker Engine。Windows 上的 ERP、MCP、Agent API/UI、评测 Worker、Prometheus 和观测中心继续原生运行。

## 从哪里看

在观测中心 `http://localhost:3002` 左侧的「配置与调用追踪」打开：

| 入口 | 用途 |
| --- | --- |
| `http://localhost:18848/nacos` | 查看模型动态配置、已发布提示词的只读投影 |
| `http://localhost:13000` | 查看主 Agent、子 Agent、工具和模型调用、Token、提示词关联 |

本机服务账号保存在 `%LOCALAPPDATA%/ChipSupply/ai-platform/service-logins.txt`。该文件和项目 `.env` 都不能提交到 Git。

Langfuse 项目为 **Procurement Agent**，项目 ID 为 `chipchain-agent`。在 Tracing 中按 session、user 或 metadata 中的 `requestId` 查找请求；模型调用详情包含模型参数、Token 用量和关联的 Prompt。轻量路径同样会记录调用，无需进入沙箱。

提示词候选评测使用相同追踪机制，记录 `evaluationRunId`、`scenarioId` 和 `case_passed` 分数。新产生的评测案例在评测中心提供「查看模型调用详情」链接。历史报告不会补造追踪数据。

`GET /api/integrations/status` 需要登录及提示词/评测查看或维护权限，返回连接状态、配置版本和同步数量，不返回凭据。`configured` 仅代表 SDK 已配置；`connected` 表示提示词同步成功，不代表每条遥测都已送达。追踪是否写入以 Langfuse 实际记录为准。

## 修改模型参数

Nacos 进入配置列表，选择 public 命名空间、`CHIPCHAIN` 分组、`chipchain-models.json`：

```json
{
  "schemaVersion": 1,
  "revision": 4,
  "models": {
    "main": {"model": "deepseek-flash", "temperature": 0.1, "maxTokens": 30000},
    "summary": {"model": "deepseek-flash", "temperature": 0.3, "maxTokens": 30000}
  }
}
```

示例版本号需高于当前版本；每次修改内容必须递增 `revision`，恢复旧参数也使用新版本号。API 默认每 10 秒读取一次，评测 Worker 在领取候选任务时刷新。请求开始时固定一个配置快照，进行中的调用不会混用新旧参数。

允许的模型由本地 `NACOS_ALLOWED_MODELS` 控制，默认 `deepseek-flash,deepseek-chat`；temperature 为 0–2，maxTokens 为 64–30000。远程配置不接受密钥、服务地址、工具或权限配置。无效配置、旧版本、连接故障保留最后有效值；重启后可读取 `%LOCALAPPDATA%/ChipSupply/runtime-models.json` 缓存。首次启动且没有缓存时使用代码默认值。

## 提示词发布与回滚

提示词仍通过采购助手的提示词版本功能维护，继续执行「版本 → 隔离评测 → 小范围发布 → 效果观测 → 回滚」门禁。

- MySQL 的提示词版本和发布状态是权威来源。
- Nacos 的 `chipchain-published-prompts.json` 为只读投影，外部修改会被后台同步恢复。
- Langfuse Prompt 名称为 `chipchain/{本地版本}/{角色}`，固定使用版本 1；关联完整执行提示词和本地指纹，不根据远程 latest 标签切换流量。
- 模型参数和执行代码属于评测指纹。参数变更或代码升级后，旧评测不能继续用于发布，必须重新评测；不会放宽原有预算或门禁。

## 启动与存储

在 Agent 仓库用 `.venv/Scripts/python.exe start_web.py` 启动。启动器先管理 OpenSandbox 虚拟机，随后通过 SSH 启动 Nacos/Langfuse。辅助脚本 `scripts/start_ai_platform_vm.py --status` 查看服务状态；Windows 不调用本地 Docker。

首次部署需将 `ops/ai-platform/compose.vm.yml` 与仅本机保存的密钥配置放到虚拟机 `/home/osb/chipchain-ai-platform/`，镜像预先导入 Linux Docker Engine；脚本使用 `--pull never`，避免启动时依赖外部镜像仓库。虚拟机内 Compose 用户插件版本为 2.40.3。

SSH 使用项目 `.env` 中的 `AI_PLATFORM_SSH_TARGET/PORT/KEY`；项目通过本地 18848、13000、19090 端口访问 VM 内 Nacos、Langfuse 和对象存储。VirtualBox 端口转发仅监听 Windows 127.0.0.1。

| 数据 | 存储位置 |
| --- | --- |
| 业务、会话、提示词版本、发布状态、评测报告 | 原有 Windows MySQL |
| Nacos 配置和日志 | VM Compose 的 nacos_data / nacos_logs 卷 |
| Langfuse 账号、项目、Prompt 元信息 | VM postgres_data 卷 |
| 追踪和用量 | VM clickhouse_data 卷 |
| 事件与媒体对象 | VM minio_data 卷 |
| Langfuse 队列 | VM redis_data 卷 |

Compose 项目名为 `chipchain-ai-platform`，各卷使用该前缀。备份需要同时保存 PostgreSQL、ClickHouse、MinIO、Nacos 卷以及私有密钥文件；不要使用 `down -v` 清理持久数据。Langfuse Redis 独立于原业务 Redis，不替代业务消息服务。

追踪使用独立 OTel Provider 和后台导出，避免覆盖项目原有追踪。Nacos 与 Langfuse 独立降级，服务不可用时不阻塞普通对话；提示词投影和追踪可能暂时缺失。遥测进行常见密钥字段、Bearer 和密钥格式脱敏，仍应避免主动在对话中输入秘密。Langfuse 停机期间未成功导出的调用不保证补传。

## 已完成的验收

- 原进程从 Nacos revision 1 更新到 2，普通对话实际使用 temperature 0.2。
- 发布含非法字段的配置后保持 revision 2；恢复原参数并发布 revision 3。
- Langfuse 实际查询到该对话的模型调用、Token、配置版本和 Prompt 关联。
- 隔离的主 Agent 委派案例通过，包含 `task`、`procurement_compare`、主/子 Agent 和评测分数；未向真实 ERP 写订单。
- 自动化测试覆盖非法配置、离线缓存、请求配置固定、鉴权刷新、遥测降级和状态接口权限。

验收原始记录位于 `%LOCALAPPDATA%/ChipSupply/ai-platform/`。这里只运行接入验收和单个真实模型隔离案例，没有把完整成对评测或灰度发布标记为通过。
