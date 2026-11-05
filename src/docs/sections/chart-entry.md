<h4>5.3.1 设计目标与能力范围</h4>

图表适配器把已识别的 26 种可视化工具组织成 7 类，对采购分析子 Agent 只暴露 `generate_chart(chart_type, chart_config)`。模型负责选择类型和组织业务数据；适配器负责参数校验、工具路由、兼容处理与结果保留。完整参数约束仍然存在，只是不再把每种图表的完整 schema 都重复放进模型上下文。

| 分类 | 类型 | 数量 |
| --- | --- | --- |
| 比较 | bar、column、pie、funnel、waterfall | 5 |
| 趋势 | line、area、dual_axes | 3 |
| 分布 | boxplot、violin、histogram、scatter | 4 |
| 指标 | radar、liquid、word_cloud | 3 |
| 层级 | treemap、organization、mind_map、fishbone_diagram | 4 |
| 关系 | sankey、venn、flow_diagram、network_graph | 4 |
| 地图 | district_map、pin_map、path_map | 3 |

26 是当前识别清单的大小。实际暴露的 `chart_type` 枚举与说明，只包含本次 MCP 服务返回且适配器识别的类型；遇到重复类型或没有任何支持类型时，构建工具直接报错。`generate_spreadsheet` 等其余工具保存在 `extra_mcp_tools`，当前没有绑定到 Agent 的 tools 列表，不能据此宣称已开放表格生成能力。

<h4>5.3.2 启动装配与单次调用流程</h4>

{{CHART_BOARD}}

图中把“模型看到的信息”和“服务端保留的能力”分开：简短描述用于选型，完整 schema 用于执行前验证。路由和一次性主题回退由 Python 完成，不需要额外调用模型。

1. **启动时发现工具。** `precompute_agent_context()` 加载 MCP 工具，适配器按原始工具名生成 `chart_type → MCP tool` 映射，并为各类型建立 `ChartConfigValidator`。工具对象和校验器在后端复用。
2. **按角色绑定。** 分析子 Agent 获得统一图表入口；主 Agent 和订单子 Agent 的直接 tools 列表不携带这套图表 schema。主 Agent 需要图表时委派分析任务。
3. **模型选择类型。** 根据业务问题选柱状图、趋势图、双轴图或结构图，阅读常驻的参数形状说明。遇到高级样式或不熟悉的类型时，再读取 `/skills/analyis_chart_params.md` 中对应示例。
4. **构造最小有效参数。** 模型提交 `chart_type` 和该类型的 `chart_config`。外层 schema 限制类型枚举、对象类型及多余字段；完整业务参数在适配器内继续检查。
5. **本地准备与校验。** 复制参数，执行有限数值检查、原始 MCP JSON Schema 校验、未知字段检查及跨字段约束；兼容旧版 treemap 单根对象等已明确的输入形式。失败立即返回可定位的字段错误，不请求远端。
6. **确定性路由并调用。** 校验通过后，通过映射表调用对应 MCP 工具。使用包含调用 ID 的 ToolCall 形式并传递运行配置，保留 MCP 的 `content` 和 `artifact`。
7. **处理结果。** 成功时保留文本、图片和结构化结果，供 Agent 汇总及前端展示；失败时返回错误状态。只有已识别的结构图 dark 主题兼容故障允许用 default 重试一次，并明确提示主题改变。

核心执行路径，以下为省略初始化细节的示意代码：

```python
# tool_map 和 validators 在预计算阶段建立；不是由模型再选择一次工具。
async def generate_chart(chart_type, chart_config, config):
    arguments = validators[chart_type].prepare(chart_config)
    return await _render_chart(
        chart_type, tool_map[chart_type], arguments, config
    )
```

<h4>5.3.3 校验、纠错与失败语义</h4>

| 检查层次 | 代表性规则 | 作用 |
| --- | --- | --- |
| 通用输入 | 数据非空、数值不能为 NaN/Infinity、尺寸为正数、拒绝未定义字段 | 尽早发现无效参数和字段拼写错误 |
| 比较与趋势 | group/stack 互斥；分组行完整；堆叠面积图各行有 group | 避免图形表现与数据结构不一致 |
| 双轴图 | categories 与每条 series.data 等长；补齐并去重系列轴标题 | 避免系列错位或远端错误合并 |
| 层级与关系 | treemap 最多 3 层；组织/思维/鱼骨树最多 4 层；节点名唯一、边引用已有节点 | 避免截断层级及悬空关系 |
| 类型差异 | liquid 用 percent；地图 title 为 1–16 字；结构图不接受普通图的 title/坐标轴 | 不把所有类型误当成同一种参数模型 |

原始 schema 提供字段和类型边界，`chart_validation.py` 补足跨字段规则；新增约束在适配层维护，必要时同步简短说明与参考文件。模型收到字段路径后修正参数即可，不必重新阅读 26 套完整说明。

主题回退仅适用于 organization、mind_map、fishbone_diagram、flow_diagram、network_graph，且必须同时满足 `theme=dark` 和错误包含已知的 `reading 'key'` 或 `reading 'colors'` 特征。其他错误不走该回退；回退再次失败也必须报告失败。地图服务已知的 `reading 'length'` 异常会提示本次没有生成图片，并避免重复提交相同参数。网络超时、服务异常、空缺图片不能包装为“图表生成成功”。

<h4>5.3.4 为什么能够减少 token</h4>

| 机制 | 当前实现 | 减少的开销与边界 |
| --- | --- | --- |
| 工具合并 | 26 个图表工具变为 1 个入口 | 减少每次分析模型请求附带的 tools 定义 |
| 共性描述复用 | 26 类型按 7 类、19 行参数形状描述；同构类型共用一行 | 去除重复的字段介绍、样式选项和示例 |
| 详细资料按需读取 | 高级选项与 JSON 示例移到技能参考文件 | 不使用图表或只使用常见参数时，无须把整份手册常驻上下文 |
| 完整校验留在后端 | 原始 MCP schema 与跨字段约束由 Python 执行 | 保持约束强度，同时不要求模型每轮携带完整规则 |
| 按角色提供工具 | 统一入口只直接绑定分析子 Agent | 主 Agent 和订单子 Agent 不重复携带图表定义 |
| 确定性处理 | 字典路由、本地校验、有限主题回退 | 路由与兼容处理本身不增加 LLM 往返；减少远端无效请求 |

**启动缓存与 token 优化是两件事。** 预计算减少工具发现、对象构造及校验器初始化开销，但工具 schema 在后续模型请求中仍需发送；不能把“启动只加载一次”解释成“模型只支付一次描述 token”。模型供应商的前缀缓存及缓存价格也需单独按实际 usage 核算。

**按需读取也不是零成本。** 当前入口只给出参考文件路径，没有自动按图表类型检索段落的机制。读取整份文件会消耗文件读取工具调用及返回内容的 token；内容保留在会话历史中时，后续模型请求还可能重复携带。应尽量读取当前类型相关内容，避免每画一张图就重读整份手册。

<h4>5.3.5 可复现的体积对比</h4>

2026-10-30 使用仓库的 `test/fixtures/chart_mcp_tools.json`，通过当前 LangChain 转换函数生成 OpenAI tools 结构，再用相同的紧凑 JSON 格式序列化。两侧均排除独立表格工具，比较同样的 26 种图表能力。token 使用 **cl100k_base 作为统一估算口径，并非当前 DeepSeek 模型的真实分词或账单数据**。

| 比较对象 | 字符数 | cl100k_base 估算 tokens |
| --- | ---: | ---: |
| 26 个原始图表工具的完整 tools JSON | 56,440 | 13,247 |
| 统一入口的完整 tools JSON | 2,722 | 676 |
| 其中：统一入口 description | 2,056 | 520 |
| 完整按需参数参考文件 | 6,011 | 2,790 |

图表工具定义的估算输入开销每次减少 **12,571 tokens，约 94.9%**；字符数减少约 95.18%。520 仅是 description 的估算值，包含参数 schema 的完整工具定义是 676，不能混用这两个口径。即使某次请求还携带整份参数文件，其静态文本估算为 676 + 2,790 = 3,466 tokens；这仍未计入读文件调用的消息包装及其他上下文。

对一段任务，可用下面的关系理解开销。它只用于图表相关输入的对比，不代表端到端账单：

```plain
第 i 次相关模型请求的输入节省 ≈ S_raw - S_unified - D_i
本次测量：S_raw = 13,247；S_unified = 676
D_i = 该次请求实际携带的新增参考内容及读取交互开销
多轮累计节省 ≈ 各次相关请求的节省之和
```

若 10 次相关模型请求都不读取详细参考，仅工具定义这一项的理论差额为 125,710 个估算输入 tokens。实际任务还包含系统提示、历史消息、业务数据、工具结果、模型输出与可能的缓存命中；不能把 94.9% 宣称成整次任务成本下降比例。复现入口为 `docs/measure_chart_tool_tokens.py`，结果记录在 `docs/chart-tool-token-comparison.json`。

<h4>5.3.6 调用示例与进一步优化</h4>

例如比较两家供应商的已核实报价，选择柱状图即可，无需读取全部高级选项。以下为示例数据，不代表真实报价：

```json
{
  "chart_type": "column",
  "chart_config": {
    "data": [
      {"category": "供应商甲", "value": 12.5},
      {"category": "供应商乙", "value": 13.2}
    ],
    "title": "同一物料报价比较",
    "axisYTitle": "元/件"
  }
}
```

适配器还会为这种无分组数据的柱状图补齐 `group=false`、`stack=false`，防止远端分组默认值与数据冲突。若改用双轴图，则需按该类型的 categories/series 结构重新组织参数，不能只修改 chart_type。

后续可按下面顺序优化；这些属于建议，当前尚未全部实现：

1. **按类型读取参考。** 把手册拆为公共边界加类型片段，或提供带类型和版本的精简查询，减少全文读入与重复读取。规则更新时同步索引、示例和校验测试。
2. **控制业务数据体积。** 在沙箱按任务需要完成聚合、筛选或采样，再提交图表数据；注明统计口径，保留原始来源。当前 chart_config 仍以内联 JSON 传输，没有 data_ref/file_path 数据引用能力。
3. **控制返回内容。** 保留真实图片 URL、必要摘要和 artifact，避免把巨大原始表格或编码图片重复塞入文本历史。当前适配器保留原始返回，不等于已经实现了大结果自动裁剪。
4. **用实际任务验收。** 固定问题、数据和模型，比较 input/output tokens、缓存命中、工具调用次数、参数修正次数、图表成功率及端到端耗时。压缩工具描述必须同时验证类型选择与参数正确性，不能只看 schema 更短。

实现依据：`agent/main_agent.py`、`agent/tools/chart_generator.py`、`agent/tools/chart_validation.py`、`skills/analyis_chart_params.md`；适配器验证场景见 `test/test_chart_generator.py`。
