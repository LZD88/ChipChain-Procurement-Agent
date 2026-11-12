# 环境准备优化验收（2026-11-12）

测量代码基于 Agent `8017984a3c4b01c598fb9e7993817112e66c7c99` 加当时尚未提交的工作区改动。本次分批发布的核心实现提交为 `d558271`（配置、指标与健康缓存）、`3e1914c`（预热池与旧绑定恢复）、`9240306`（后台准备与 graph 复用）；既有 SSE 超时恢复独立提交为 `148fac8`。运行环境为 Windows 原生应用 + VirtualBox `opensandbox-vm`，VM 为 4 CPU / 8192 MiB；默认池目标 2，每个沙箱申请 2 CPU / 4 GiB。测量时存在 API 后台补充与独立基准进程竞争，因此这里是本机结果，不是生产 SLO。

## 真实旧绑定卡顿复查（2026-11-12）

用户实际请求在环境准备阶段等待超过 60 秒。本轮确认两个独立问题：旧沙箱连接失败后 `setup_sandbox` 曾隐式冷建，绕过 manager 的 ready 池；同时 VM 控制台有 soft lockup 和 RCU 调度停滞，SSH 和 OpenSandbox `/health` 也超时。后者不能靠应用缓存解决。

修复后，已有绑定连接失败直接交回 manager，先尝试 ready 池，再按需创建；旧环境清理限制为 8 秒。新增回归覆盖 TimeoutError/ConnectionError 不隐式冷建、失效绑定认领 ready 环境及清理超时。相关 24 项测试、2 个 subtests 通过；扩大回归为 227 项、26 个 subtests 通过，仍使用下文三个既有环境排除，保留 9 条 MySQL 库弃用警告。编译检查、`git diff --check` 和 OpenSpec strict validate 通过。

VM 将 2 CPU 的失败尝试回退为 4 CPU / 8192 MiB，execution cap 从 90% 调至 100%。VirtualBox 实际使用 Hyper-V/NEM；普通启动仍曾停在 initramfs。临时指定 `clocksource=acpi_pm tsc=unstable` 后启动成功，来宾确认当前源为 `acpi_pm`，这次启动截至首次对话完成未记录新的 soft lockup。该观察支持采用兼容参数，但不足以证明原先全部停顿的唯一根因。

恢复后的 `start_web.py` 启动器 PID 14180 使用子进程级池目标 1、准备并发 1，真实 `.env` 未修改。ready 达到 1 后，运行 `scripts/verify_preparation_web.py --stale-binding`：创建独立随机测试账号及不存在的沙箱绑定，登录成功后立即发起只读库存查询，不提前等待该用户的 graph。登录返回 200，耗时 1.945 秒；进入 reasoning 约 20.75 秒，首 Token 23.07 秒，完整 SSE 37.58 秒。调用了 `inventory_warning` 等工具，无 ERP 写入；六个公开健康检查全部 200。原始记录为 `runtime-logs/preparation-web-stale-binding.json`。这只是一个真实故障回退样例，不是 P95，也不是无冷准备路径。

MCP 的 HTTP 工具发现返回 11 项分析/订单工具并包含 `inventory_warning`；evaluation worker 35448/8604 仍在运行。Tempo、Grafana、RabbitMQ 指标端点不可达，单独记录为可选组件。Redis exporter 9121 不可达，不据此断言 Redis 本体故障。

时钟参数写入来宾独立 GRUB 配置，原配置有备份；随后 ACPI 正常关机，由 `start_web.py`（最终启动器 PID 7364）自动启动 VM/API/UI 成功。来宾 `/proc/cmdline` 确认参数持久生效，当前源为 `acpi_pm`，截至重启后验收未出现新的 soft lockup。池 ready=1 后再次运行相同旧绑定场景：登录 200 / 1.801 秒，首 Token 25.36 秒，完整 SSE 46.22 秒，无 SSE error、无 ERP 写入；六个公开健康检查均为 200。记录为 `runtime-logs/preparation-web-stale-binding-persistent.json`。两次独立样例均含登录后准备和模型执行，不能将其当作稳定延迟承诺。最终 worker 35448/8604 仍在运行。

交付前预热补充已完成：ready=1、target=1、creating=0，补充成功 2 次、失败 0 次。核心服务与 evaluation worker 保持在线；上述可选监控组件故障不影响这两条库存查询。

## 已完成的验证

| 范围 | 证据 |
| --- | --- |
| 准备、配置、认证、缓存、池、技能、子 Agent、会话与指标 | 70 个测试通过，另有 15 个 subtests 通过 |
| 前端认证、SSE、审批、会话等 | 44 个 Node 测试通过；Vite production build 成功（保留原有大 chunk 提示） |
| 可观测中心代理与部署配置 | 9 个 Python 测试通过 |
| 可观测中心数据展示 | 8 个 Node 测试通过，1 个因未配置 QUALITY_REPORT_PATH 跳过 |
| 独立浏览器检查 | 新诊断区正常加载，无脚本错误；390px 手机视口无横向溢出 |
| 真实只读 SSE | 注册 201，响应 0.914 秒；库存预警对话完整返回并调用查询工具，无订单写入 |

最终 Agent 回归通过 224 项（24 个 subtests），命令为 `.venv/Scripts/python.exe -m pytest src/test -q --ignore=src/test/test_chart_generator.py --ignore=src/test/test_chart_live.py --ignore=src/test/test_all_tools.py`。全量测试另有既有环境限制：`test_chart_generator.py`、`test_chart_live.py` 缺 `fixtures/chart_mcp_tools.json`，收集失败；`test_all_tools.py::test_mcp_supplier_query` 的指定演示供应商在真实 ERP 中不存在。没有为得到通过结果伪造 fixture 或修改 ERP 数据。

## 需求与实现证据

| 需求 | 主要实现与验收 |
| --- | --- |
| 认证后非阻塞准备 | `api_view/api/auth.py`；`test_runtime_auth.py` 验证注册/登录/有效 /me 调度，非法/过期身份不调度 |
| 有界共享准备 | `api_view/runtime_preparation.py`；并发 waiter、容量、截止、失败重试、单个断开与孤立取消测试 |
| 短期成功探活缓存 | `sandbox_healthcheck.py`、`sandbox_proxy.py`；30/45/60 秒、TTL 0、single-flight、故障与代际失效测试 |
| 有界预热池 | `sandbox_manager.py`；双用户独占、已有绑定优先、绑定失败、禁用、补充失败、取消与 shutdown 测试；额外覆盖 worker 返回到 gather 结束之间认领时不漏补充，以及数据库绑定中途 shutdown 不发布新资源并清理新绑定 |
| 用户 graph 复用 | `runtime_preparation.py`、`main_agent.py`；fingerprint、代际、闲置淘汰、全活跃有界等待及真实 compiled graph 并发测试 |
| 每轮运行隔离 | `test_subagents.py` 验证同一 compiled graph 不串消息、run 计数从 0 开始、两个线程审批独立；既有签名与权限测试继续运行 |
| 受控生命周期 | 准备 task/通知/闲置清理器均由 registry 管理；多会话 logout、租用释放、关闭等待与保留已恢复文件测试 |
| 独立实际工作/等待/阶段计时 | `observability.py`；实际工作一次、两个 waiter 两次、排队边界和失败/取消/超时分类测试 |
| 低基数资源诊断 | 固定枚举 cache/phase/outcome 标签、ready/target/creating gauges 与导出测试 |
| 新看板与缺失数据兼容 | `preparation.mjs` 与 Node 测试；5 分钟 P95/约算样本、少于 20 提示、零分母、旧 Agent、数据源不可达独立降级；真实浏览器分别注入空结果与查询失败，确认提示及其他页面区域保留，随后恢复正常刷新 |

## 已采集的真实数据

热缓存高精度复测：30 次 acquisition，最近秩 P95 为 0.0004204 秒（约 0.42 ms），最大值 1.08252 秒。最大样本发生在成功健康结果过期后的实际探活，其余样本约 0.23–0.42 ms。这组数据不包括模型执行，不能用于宣称完整请求达到亚毫秒响应。

同一 VM 关闭 graph/健康缓存的参考配置：5 次约 21–22 秒，P95 21.984 秒。该对比是本次代码的关闭配置，不是旧版本逐行复现。早期主基准用了 Windows 低精度 monotonic，热缓存显示的 0 已由单独 perf_counter 复测替代；主基准脚本和正式指标已改用 perf_counter 测量延迟，TTL 仍用单调时钟。

最新冷准备 5 次完整尝试（高精度计时）：302.06、300.00、165.45、303.52、135.98 秒。其中 2 次成功、3 次 TimeoutError；全尝试最近秩 P95 为 303.52 秒，成功样本 P95 为 165.45 秒（仅 2 项，不代表稳定分布）。300 秒截止后的远程清理占用少量时间。绕过了 ready 池，后台补充与独立基准进程仍可能争用资源；不能据此宣称冷启动得到稳定改善。早期中断基准的已完成关闭缓存数据仍保留，未完成的冷阶段不冒充完整一组。

登录已准备（复用同一个真实沙箱，每轮清 graph 后重新在后台编译）5 次 acquisition：0.000412、0.000429、1.065248、0.000439、0.000437 秒，全部成功，最近秩 P95 为 1.065248 秒。这组只有 5 项、包含一次 TTL 到期探活，未达到 1 秒目标；30 次热路径那组达到目标，不混合两组口径。后台实际工作共 104.52 秒，前台等待共 1.06693 秒，成功 graph 构建 5 次、前台 graph hit 5 次、health hit 8/miss 2。graph 阶段共 103.40 秒，其中同步约 103.15 秒，显示重复准备成本主要来自文件同步；同步是嵌套阶段，不相加。热缓存主基准 30 次无额外编译，health hit 29/miss 1；其低精度耗时由高精度复测替代。

故障恢复合计 5 次实际 acquisition：21.81、111.22、149.03、21.54、301.47 秒，结果依次为成功、成功、SandboxInternalException、成功、TimeoutError。全尝试最近秩 P95 为 301.47 秒；3 个成功样本的 P95 为 111.22 秒。两次约 22 秒的成功使用 ready 资源；池耗尽后的按需创建既有成功，也出现 SDK 内部异常和截止超时，不宣称恢复稳定达标。首个基准另外两次在删除已经不存在的沙箱时失败，尚未执行 acquisition，因此作为故障注入失败单列，不算入这 5 次。修正脚本后补测 2 次，使用相同默认配置并保留池状态及错误类型，原始失败不删除。补测与 Web 预热有时间重叠，属于共享 VM 负载下的实测。

| 实际 VM 场景 | 结果 | 实际准备工作 sum / 前台等待 sum（秒） | 成功 graph 构建 / health miss |
| --- | --- | --- | --- |
| 前台冷准备 | 2 成功、3 超时 | 1202.39 / 1202.39 | 2 / 5 |
| 登录已准备 | 5 成功 | 104.52 / 1.06693 | 5 / 2（另有 hit 8） |
| 故障恢复 | 3 成功、1 内部异常、1 超时 | 602.43 / 602.48 | 3 / 14 |

这些 sum 包含成功和失败，等待和实际工作互相重叠，不相加。health miss 在这组顺序 acquisition 中对应远程探活请求；一般并发下 miss 可能被 single-flight 合并，不能无条件当作远程命令数。graph 成功构建计数取 graph 阶段成功样本，包含同步和编译；具体隔离、single-flight 及调用次数保证由受控测试补充，不能把受控桩耗时混入 VM 数据。

首次真实 SSE 对话在注册后立即发起，准备阶段约 122.9 秒，首 Token 126.83 秒，总耗时 152.30 秒。登录不等待准备，并不代表立刻发消息就没有冷启动；必须给后台完成准备的时间。保留了 10 秒级进度心跳。

第二次使用新的随机测试账号，注册响应 0.905 秒，等待后台准备约 22.61 秒后发起只读库存对话；首 Token 3.33 秒，完整返回 17.77 秒。两次均完成 SSE、身份检查和工具调用，无 ERP 写入。模型选用的工具与输出长度不同，这只是两个独立样例，不能据此宣称端到端 P95 或固定提升比例。跨线程审批隔离由真实 compiled graph 的模拟 HITL 测试覆盖：同一个缓存 graph 的两个线程独立中断，拒绝一个后另一个继续等待，后者单独批准才执行模拟写工具。

最终代码重启后第三次只读 SSE 再次完整成功：注册 0.920 秒，准备完成后首 Token 4.00 秒，总耗时 21.32 秒，调用 `inventory_warning` 等工具；6 个公开健康端点全部 200，无 ERP 写入，准备指标已导出。原始记录为 `preparation-web-final.json`，同样仅是单次端到端样例。

预热补充中实测一次 300 秒超时，资源被清理并重试，后续恢复到 2/2 ready、0 creating。失败仍计入池补充指标，不计为成功。可选 Tempo、Grafana、RabbitMQ 当前不可用；ERP、MCP、Agent/API/UI、Prometheus、可观测中心保持运行，evaluation worker 进程已确认。

第一次复核通过 `start_web.py` 原生重启（16:31，启动器 PID 35280）。OpenSandbox `/health`、ERP `/actuator/health`、API `/health`、UI 首页、Prometheus `/-/ready`、看板 `/healthz` 均返回 200；MCP 协议发现成功返回 8 个分析、3 个订单、27 个图表工具。MCP URL 的普通 GET 返回 406 是 Accept 协议要求，不用它判断服务故障。evaluation worker 父/子进程为 35448/8604，命令为 `agent.evaluation.worker`；VirtualBox 确认 running、4 CPU、8192 MiB。Redis exporter 9121 当前不可达，不由此断言 Redis 服务不可用；Tempo/Grafana/RabbitMQ 指标端点不可达，单独列为可选组件。

最后两处竞争修复于 17:05 通过 `start_web.py` 重新加载（启动器 PID 7080），上述 6 个公开端点再次全部返回 200，evaluation worker 35448/8604 继续运行；初始池为 ready=0/target=2/creating=1，启动不会等待池填满。

最终只读 SSE 注册后后台准备 22.34 秒，查询完成时池为 1/2 ready、1 creating；17:12 浏览器复核恢复到 2/2 ready、0 creating。准备看板显示 0.005 秒直方图估算、约 1 个新样本和“样本较少”提示；该桶内估算不等同于原始 acquisition 的精确时间，也不能当作端到端耗时。

双用户从 2 个 ready 沙箱并发获取完成约 22.41 秒，验证了 ID 不同且领取后 ready 为 0。该基准显式关闭认领后补充，以隔离按需/恢复场景；它不代表默认运行时禁止补充。补充竞争及退出绑定竞争另由受控测试覆盖。

同一 VM 集成回退：background=false、graph=false、health TTL=0、pool target=1，两次实际 acquisition 成功，约 21.99 和 86.65 秒；随后池实际 ready=1。关闭后台调度的独立注册表检查 pending=0、graphs=0。回退保证功能可运行，不保证低延迟；第二次耗时上升没有足够阶段数据归因，不写成稳定 22 秒。浏览器旧指标/查询失败的兼容检查通过，详情见上表。

原始记录保存在本地忽略目录 `runtime-logs/`：`preparation-benchmark.json` 保留早期中断记录，`preparation-benchmark-followup.json` 记录完整冷/登录/恢复尝试、双用户和回退，`preparation-recovery-followup.json` 为补测、`preparation-recovery-summary.json` 为区分注入失败后的 5 次恢复汇总，`preparation-hot-high-resolution.json` 为热路径复测，`preparation-web-verification.json`、`preparation-web-prepared.json`、`preparation-web-final.json` 为三条只读 SSE。记录不包含 Cookie、凭据或业务响应正文。首个完整基准进程在末次池补充/退出竞争修复前启动；这些末次修复由 224 项最终回归覆盖，补测进程及最终原生服务已加载它们。
