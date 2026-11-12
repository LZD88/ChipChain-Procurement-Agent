# 环境提前准备与复用

运行范围为 Windows 单 worker 应用进程；OpenSandbox 由 `start_web.py` 管理 VirtualBox VM。无需数据库迁移，不依赖 Docker Desktop。

| 配置 | 默认 | 约束 |
| --- | --- | --- |
| SANDBOX_HEALTH_CACHE_TTL_SECONDS | 45 | 0 关闭，或 30–60 秒 |
| SANDBOX_PREWARM_TARGET | 2 | 0–4，0 关闭预热 |
| SANDBOX_PREWARM_CONCURRENCY | 1 | 1–4，不大于非零 target |
| AGENT_BACKGROUND_PREPARE_ENABLED | true | 认证后推测性准备 |
| AGENT_PREPARE_CONCURRENCY | 2 | 正整数 |
| AGENT_PREPARE_MAX_PENDING | 32 | 不小于准备并发 |
| AGENT_GRAPH_CACHE_ENABLED | true | 用户级编译缓存 |
| AGENT_GRAPH_CACHE_MAX_USERS | 32 | 正整数，包含在用条目 |
| AGENT_GRAPH_CACHE_IDLE_SECONDS | 1800 | 正整数秒 |

准备工作截止 300 秒，前台等待继续发送 SSE 进度。认证响应不等待沙箱。默认开启[请求分流](request-routing.md)：简短问答和白名单只读查询跳过沙箱，认证只记录 `deferred_routing`，首次完整任务才获取用户环境。关闭分流时，多次登录恢复与前台对话合并同用户正在执行的准备。后台不执行模型或 ERP 写入。容量满时跳过后台准备，前台可有界等待按需准备。失败可重试。

实际工作 `agent_preparation_duration_seconds` 不含全局排队；前台 `agent_preparation_wait_seconds` 含排队与缓存获取，每次请求单独计时。阶段 `sandbox/graph/sync` 可能嵌套，不直接求和。原请求及首 Token 耗时保持原口径；总耗时相减不能当作实测模型时间。标签均为固定枚举，无用户或沙箱 ID。

回退时关闭后台准备和 graph 缓存，将健康 TTL 设 0、池目标设 1；完全恢复旧行为需要回退代码。配置示例不包含真实凭据，多进程会各自创建池与缓存。

健康成功结果由 setup、manager、健康守护共享，过期并发探活合并；代理替换、删除和基础设施故障立即失效。业务拒绝、用户命令非零退出不等同于沙箱死亡，不触发 ERP 写入重试。

恢复旧绑定仅尝试连接已有沙箱，失败交回 manager，按 ready 池、按需创建顺序回退；setup 不在重连内部隐式冷建。旧环境清理最多等待 8 秒，失败单独记录，避免清理阻塞已经领取的可用环境。

每个预热沙箱请求 2 CPU/4 GiB，默认 2 个空闲环境申请共 4 CPU/8 GiB。VM 资源紧张可设 target=1 或 0。已有用户绑定优先于池，分配后立即补充；`SANDBOX_PREWARM_REPLENISH=false` 仅禁止认领后的补充，保留初次填充。池上限不限制所有已分配用户环境的总量。

graph 绑定用户，线程、可信工具身份、审批和调用计数从本次运行读取。缓存由 sandbox generation、提示/AGENTS.md/非敏感模型参数/工具 schema 和预计算版本的 fingerprint 失效；代码更新需重启。租用中的条目不淘汰；释放编译缓存不会删除文件、历史或决策证据。同步按 generation 重置，只在成功上传后记住哈希，运行期新增技能仍可恢复。

## 复现实测

先通过 `python start_web.py` 启动 VM/API/UI，保持 ERP 和 MCP 原生进程在线。运行 `python scripts/benchmark_preparation.py`，结果写入 `runtime-logs/preparation-benchmark.json`。该脚本只创建并清理自己带 `prepbench-` 前缀的沙箱绑定，不执行 graph/模型或 ERP 写入。会创建多个沙箱并安装依赖，需预留数十分钟；冷启动与故障恢复各 5 次，热缓存 30 次，登录准备后 5 次，另外验证双用户预热分配及回退。

`cache_disabled_same_vm` 是本次代码关闭缓存后的参考配置，不等价于历史版本逐行复现。热路径目标 P95 ≤ 1 秒只针对 acquisition，不是模型端到端 SLO。Python 3.12/Windows 的 `monotonic` 使用低精度 GetTickCount64，因此延迟测量使用高精度 `perf_counter`，健康 TTL 仍使用单调时钟。

每次样本完成即保存耗时、结果和准备/等待/阶段指标增量，失败或超时也保留；冷样本少时 P95 接近最大值，不代表稳定生产分布。中断后可用 `--paths cold,auth,recovery,dual,rollback --output runtime-logs/preparation-followup.json` 仅补测相应阶段，保留原始记录。

恢复阶段已经不存在的沙箱视为删除完成，继续实际 acquisition；其他删除错误仍记失败，避免把“故障注入失败”当作恢复成功。仅补齐缺少的尝试可用 `--paths recovery --scenario-samples 2 --output runtime-logs/preparation-recovery-followup.json`，默认仍为 5 次。报告同时列出全部尝试与成功样本的 P95，错误类型单独保留。

`python scripts/verify_preparation_web.py` 使用随机测试账号进行一条真实只读库存对话，保留 SSE 事件类型、首 Token/总耗时和公开健康状态；不会输出密码/Cookie、业务内容或用户/线程/沙箱 ID。只读验证账号和对话会留在本地，便于回看，不创建真实订单。可选 Tempo/Grafana/RabbitMQ 状态单独记录。

模拟老账号的过期沙箱绑定时，先确认 `agent_sandbox_pool_ready >= 1`，再运行 `python scripts/verify_preparation_web.py --stale-binding --output runtime-logs/preparation-web-stale-binding.json`。该模式在独立随机测试账号中写入不存在的绑定，登录后立即提问，验证重连失败后领取 ready 环境；不改动已有用户的绑定。`--prepare-first` 会等待后台准备，不能用它替代立即提问场景。

## 本机 VirtualBox 启动兼容处理

2026-11-12，本机 VirtualBox 日志显示 VT-x 不可用并回退到 Windows Hyper-V/NEM；来宾曾出现 soft lockup、RCU 调度停滞及健康/SSH 超时。仅调整 CPU 数量未恢复正常启动。使用 `clocksource=acpi_pm tsc=unstable` 后来宾成功启动，并通过一条真实库存查询；这是当前机器的兼容处理，不代表已证明所有停顿都由时钟源引起。

来宾配置位于 `/etc/default/grub.d/99-opensandbox-clock.cfg`，由 `update-grub` 生成启动项；原 GRUB 配置备份为 `/boot/grub/grub.cfg.before-opensandbox-clock-20261112`。需要回退时，在来宾以管理员身份删除该单独配置文件并执行 `update-grub`，下次启动生效。不要关闭宿主 Hyper-V/VBS 或重启 Windows 来替代已验证的本机运行方式。

当前 VM 为 4 CPU / 8192 MiB，CPU execution cap 为 100%。恢复运行期间仅对启动器子进程设置 `SANDBOX_PREWARM_TARGET=1` 和 `AGENT_PREPARE_CONCURRENCY=1`，真实 `.env` 未修改；常规默认值仍为池 2、准备并发 2。应用继续通过 `start_web.py` 启动并等待 OpenSandbox 健康检查，不依赖 Docker Desktop。
