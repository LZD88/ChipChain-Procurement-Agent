"""Author the project's editable draw.io sources and Yuque board text DSL."""
from pathlib import Path
import json
import re
import xml.etree.ElementTree as ET

ROOT = Path(__file__).parent
COLORS = {
    "blue": ("#eaf2ff", "#5b8bd9"),
    "teal": ("#e8f6f1", "#45a38b"),
    "purple": ("#f0ebff", "#8b73c8"),
    "orange": ("#fff3df", "#d7a149"),
    "gray": ("#f1f5f9", "#94a3b8"),
}


def graph(title):
    mxfile = ET.Element("mxfile", host="app.diagrams.net")
    diagram = ET.SubElement(mxfile, "diagram", id=title, name=title)
    model = ET.SubElement(diagram, "mxGraphModel", adaptiveColors="auto", grid="1", gridSize="10", page="0")
    root = ET.SubElement(model, "root")
    ET.SubElement(root, "mxCell", id="0")
    ET.SubElement(root, "mxCell", id="1", parent="0")
    return mxfile, root


def node(root, key, label, x, y, w=220, h=70, color="blue", shape="", parent="1"):
    fill, stroke = COLORS[color]
    style = f"rounded=1;whiteSpace=wrap;html=1;fillColor={fill};strokeColor={stroke};fontColor=#172b4d;fontSize=14;spacing=8;{shape}"
    cell = ET.SubElement(root, "mxCell", id=key, value=label.replace("\n", "<br>"), style=style, vertex="1", parent=parent)
    ET.SubElement(cell, "mxGeometry", x=str(x), y=str(y), width=str(w), height=str(h), **{"as": "geometry"})


def edge(root, key, source, target, label=""):
    cell = ET.SubElement(root, "mxCell", id=key, source=source, target=target, value=label,
                         style="edgeStyle=orthogonalEdgeStyle;rounded=1;html=1;endArrow=block;strokeColor=#64748b;fontColor=#334155;fontSize=12;defaultLabelBackgroundColor=page;", edge="1", parent="1")
    ET.SubElement(cell, "mxGeometry", relative="1", **{"as": "geometry"})


def save(name, xml, dsl, board_type, section):
    ET.indent(xml)
    ET.ElementTree(xml).write(ROOT / f"{name}.drawio", encoding="utf-8", xml_declaration=True)
    (ROOT / f"{name}.dsl").write_text(dsl.strip() + "\n", encoding="utf-8")
    return {"name": name, "type": board_type, "section": section, "dsl": dsl.strip()}


def architecture():
    layers = [
        ("用户交互层", "blue", [("Vue 3 工作台", ["登录注册 / 会话列表", "SSE 答复 / 图表预览", "补充信息 / 人工审批"])]),
        ("应用接口层", "teal", [("FastAPI", ["Cookie 认证 / 会话归属", "NeMo 输入安全检查", "Chat / Resume / History"]), ("AgentLoader", ["MCP 工具预计算", "按请求创建执行图"])]),
        ("智能编排层", "purple", [("DeepAgents / LangGraph", ["主 Agent：协调与汇总", "分析子 Agent：查询与分析", "订单子 Agent：受控写入"])]),
        ("工具与业务集成层", "orange", [("ERP MCP", ["Java 芯链 REST API"]), ("分析工具", ["图表 MCP / generate_chart", "博查搜索 / 网页采集"]), ("模型服务", ["deepseek-flash", "Agent / 摘要 / 输入审查"])]),
        ("执行与文件后端", "teal", [("CompositeBackend", ["OpenSandbox：用户级沙箱", "StoreBackend：偏好与技能"])]),
        ("状态与业务数据", "gray", [("MySQL agent_db", ["检查点 / 展示历史", "账号 / 会话 / 沙箱绑定"]), ("InMemoryStore", ["偏好 / 共享技能（进程内）"]), ("ERP 业务库", ["物料 / 供应商 / 订单 / 库存"])])
    ]
    xml, root = graph("芯链智能采购助手总体架构")
    lines = []
    for i, (title, color, modules) in enumerate(layers):
        lines.append("+ " + title)
        node(root, f"layer{i}", title, 20, 30 + i * 170, 1200, 150, color,
             "swimlane;startSize=30;rounded=0;fontStyle=1;align=left;spacingLeft=15;")
        col = 0
        for j, (module, leaves) in enumerate(modules):
            lines.append("    - " + module)
            label = module + "\n" + "\n".join(leaves)
            node(root, f"module{i}_{j}", label, 30 + col * 380, 42, 350, 95, color, parent=f"layer{i}")
            lines.extend("        * " + leaf for leaf in leaves)
            col += 1
    return save("procurement-architecture-zh", xml, "\n".join(lines), "architecturediagram", "2.2")


def flow(name, title, section, vertices, links):
    xml, root = graph(title)
    definitions = {}
    for key, label, col, row, kind, color in vertices:
        shape = "rhombus;" if kind == "decision" else "ellipse;" if kind == "end" else ""
        node(root, key, label, 40 + col * 300, 40 + row * 160, 220, 100 if kind == "decision" else 80, color, shape)
        text = label.replace("\n", "\\n")
        opening, closing = ("{", "}") if kind == "decision" else ("([", "])") if kind == "end" else ("[", "]")
        definitions[key] = f"{key}{opening}{text}{closing}"
    syntax, emitted = [], set()
    def ref(key):
        if key in emitted:
            return key
        emitted.add(key)
        return definitions[key]
    for i, (a, b, label) in enumerate(links):
        edge(root, f"edge{i}", a, b, label)
        syntax.append(ref(a) + " -->" + (f"|{label}| " if label else " ") + ref(b))
    return save(name, xml, "\n".join(syntax), "flowchart", section)


boards = [architecture()]
boards.append(flow("agent-collaboration-flow-zh", "多 Agent 调用与结果回收", "3.2", [
    ("A", "Vue 工作台\n请求与 SSE", 1, 0, "normal", "blue"),
    ("B", "FastAPI\n登录 / 归属 / 输入检查", 1, 1, "normal", "teal"),
    ("C", "AgentLoader\n用户沙箱 + 请求执行图", 1, 2, "normal", "teal"),
    ("D", "主 Agent\n识别需求与任务委派", 1, 3, "normal", "purple"),
    ("E", "采购分析子 Agent\n收集证据 / 计算 / 报告", 0, 4, "normal", "purple"),
    ("F", "采购订单子 Agent\n准备订单参数", 2, 4, "normal", "purple"),
    ("G", "分析执行工具\nERP 查询 / 沙箱 / 图表 / 搜索", 0, 5, "normal", "orange"),
    ("H", "人工审批\n同意后调用写工具", 2, 5, "normal", "orange"),
    ("I", "ERP MCP\nJava 订单创建 / 更新", 2, 6, "normal", "orange"),
    ("J", "主 Agent 汇总\n分析结论 / 订单实际结果", 1, 7, "normal", "purple"),
    ("K", "Vue 工作台\n最终答复 / 图表 / 执行过程", 1, 8, "end", "blue"),
], [
    ("A", "B", "HTTP"), ("B", "C", "检查通过"), ("C", "D", "运行"),
    ("D", "E", "分析任务"), ("D", "F", "订单任务"), ("D", "J", "直接答复"),
    ("E", "G", "调用"), ("G", "J", "分析结果"), ("F", "H", "执行前暂停"),
    ("H", "I", "同意"), ("H", "J", "拒绝"), ("I", "J", "业务结果"), ("J", "K", "SSE"),
]))
boards.append(flow("order-approval-flow-zh", "订单信息补齐与审批恢复", "4.3", [
    ("A", "订单请求\n查询并定位业务对象", 1, 0, "end", "blue"),
    ("B", "订单参数完整？", 1, 1, "decision", "purple"),
    ("C", "request_order_info\n中断并等待补充", 0, 2, "normal", "orange"),
    ("D", "用户补充文本\n身份校验 + 输入检查", 0, 3, "normal", "blue"),
    ("E", "准备创建或更新参数\norder_create / order_update", 1, 2, "normal", "purple"),
    ("F", "执行前中断\nMySQL 检查点保存", 1, 3, "normal", "orange"),
    ("G", "用户审批决定", 1, 4, "decision", "orange"),
    ("H", "approve → resume\n执行 ERP 订单接口", 0, 5, "normal", "teal"),
    ("I", "reject → resume\n不执行该写入动作", 2, 5, "normal", "gray"),
    ("J", "核对 ERP 返回结果\n成功 / 失败分别反馈", 0, 6, "normal", "teal"),
    ("K", "返回用户并保存历史\nSSE / 展示消息", 1, 7, "end", "blue"),
], [
    ("A", "B", ""), ("B", "C", "否"), ("C", "D", "等待输入"), ("D", "B", "resume"),
    ("B", "E", "是"), ("E", "F", ""), ("F", "G", "等待审批"),
    ("G", "H", "同意"), ("G", "I", "拒绝"), ("H", "J", ""), ("J", "K", ""), ("I", "K", ""),
]))

boards.append(flow("chart-generation-flow-zh", "单次图表调用与错误处理", "5.3", [
    ("A", "分析 Agent 选择类型\n准备数据 / 按需读参数", 1, 0, "normal", "purple"),
    ("B", "generate_chart\n类型 + 图表参数", 1, 1, "normal", "purple"),
    ("C", "本地校验通过？", 1, 2, "decision", "teal"),
    ("D", "返回字段错误\n本次不调用远端", 0, 2, "end", "gray"),
    ("E", "按类型路由\n调用对应 MCP 工具", 1, 3, "normal", "orange"),
    ("F", "远端调用结果", 1, 4, "decision", "orange"),
    ("G", "default 主题重试一次\n只处理已知 dark 故障", 1, 5, "normal", "orange"),
    ("H", "保留 content 与 artifact\n回退成功时附主题提示", 0, 6, "normal", "teal"),
    ("I", "返回明确失败\n不伪造图片或成功状态", 2, 6, "end", "gray"),
    ("J", "Agent 汇总 / 前端展示", 0, 7, "end", "blue"),
], [("A","B",""),("B","C",""),("C","D","否"),("C","E","是"),("E","F",""),
    ("F","H","成功"),("F","G","特定兼容错误"),("F","I","其他失败"),
    ("G","H","重试成功"),("G","I","重试失败"),("H","J","")]))
boards.append(flow("chart-startup-flow-zh", "图表工具启动装配", "5.3", [
    ("A", "加载图表 MCP 工具\n读取原始 schema", 1, 0, "normal", "blue"),
    ("B", "按工具名分类\n识别当前可用图表类型", 1, 1, "normal", "teal"),
    ("C", "后端保留完整能力\ntool_map 与 validators", 0, 2, "normal", "gray"),
    ("D", "构建 generate_chart\n类型枚举 + 简短说明", 1, 2, "normal", "purple"),
    ("E", "其余工具单独保留\nextra_mcp_tools", 2, 2, "normal", "gray"),
    ("F", "按请求绑定分析子 Agent\n主 Agent 委派分析任务", 1, 3, "end", "blue"),
], [("A","B",""),("B","C","原始约束"),("B","D","统一入口"),("B","E","未合并工具"),
    ("C","D","封装时引用"),("D","F","仅直接提供一个图表工具")]))
boards.append(flow("input-guard-flow-zh", "输入检查与业务分流", "9.2", [
    ("A", "聊天请求\n认证与会话归属校验", 1, 0, "normal", "blue"),
    ("B", "基础输入与配置校验\n非空 / 长度 / 控制字符", 1, 1, "normal", "teal"),
    ("C", "语义审查允许？", 1, 2, "decision", "purple"),
    ("D", "确定性业务规则\n指定整句联系人导出检查", 1, 3, "normal", "orange"),
    ("E", "命中固定问候？", 1, 4, "decision", "purple"),
    ("F", "固定回复中间件\n写入答复并结束本轮", 0, 5, "end", "blue"),
    ("G", "验证 PASSED\n且返回内容未被改写", 1, 5, "normal", "teal"),
    ("H", "进入业务 Agent\n分析 / 工具 / 订单审批", 1, 6, "end", "blue"),
    ("I", "403 策略拦截\n不进入业务执行", 2, 3, "end", "gray"),
], [("A","B","认证通过"),("B","C","格式与配置有效"),("C","D","ALLOW"),
    ("C","I","不允许"),("D","I","规则命中"),("D","E","未命中"),
    ("E","F","是"),("E","G","否"),("G","H","状态和内容符合")]))
boards.append(flow("input-guard-resume-flow-zh", "补充信息与审批恢复", "9.2", [
    ("A", "恢复请求\n认证并校验会话归属", 1, 0, "normal", "blue"),
    ("B", "resume 输入类型", 1, 1, "decision", "purple"),
    ("C", "仅含 supplement\n文本走完整输入护栏", 0, 2, "normal", "teal"),
    ("D", "仅含 decisions\n1–20 项 / 每项仅 type\napprove 或 reject", 2, 2, "normal", "teal"),
    ("E", "422 格式错误\n混合字段或其他结构", 1, 2, "end", "gray"),
    ("F", "Command resume\n从原中断点继续", 1, 4, "end", "blue"),
    ("G", "停止文本补充\n422 / 403 / 503", 0, 3, "end", "gray"),
    ("H", "停止审批恢复\n结构错误 422 / 配置错误 503", 2, 3, "end", "gray"),
], [("A","B","归属通过"),("B","C","补充信息"),("B","D","审批决定"),("B","E","其他结构"),
    ("C","G","未通过"),("D","H","未通过"),("C","F","检查通过"),("D","F","校验通过")]))

boards.append(flow("business-capabilities-zh", "采购业务目标与系统边界", "1", [
    ("A", "采购用户\n提出问题或业务任务", 1, 0, "end", "blue"),
    ("B", "分析场景\n比价 / 行情 / 采购策略", 0, 1, "normal", "purple"),
    ("C", "订单场景\n查询 / 信息补齐 / 参数准备", 1, 1, "normal", "purple"),
    ("D", "知识与偏好\n技能复用 / 用户上下文", 2, 1, "normal", "purple"),
    ("E", "证据与计算\nERP / 搜索 / 沙箱", 0, 2, "normal", "teal"),
    ("F", "人工审批\n同意后执行 ERP 写入", 1, 2, "normal", "orange"),
    ("G", "可读业务结果\n结论 / 图表 / 实际订单结果", 1, 3, "end", "blue"),
], [("A","B","分析"),("A","C","订单"),("A","D","复用"),("B","E","取证"),
    ("C","F","需要写入"),("D","G","提供方法与上下文"),("E","G",""),("F","G","")]))
boards.append(flow("skills-context-flow-zh", "技能同步与按需加载", "6.2", [
    ("A", "本地预置技能\nSKILL.md / 脚本 / 参考", 0, 0, "normal", "blue"),
    ("B", "运行期技能分配\nassign_skill", 2, 0, "normal", "blue"),
    ("C", "SkillsSyncMiddleware\n增量同步", 0, 1, "normal", "teal"),
    ("D", "共享技能 Store\nnamespace = skills", 2, 1, "normal", "gray"),
    ("E", "UserSkillsRestore\n从 Store 恢复副本", 2, 2, "normal", "teal"),
    ("F", "用户沙箱技能目录\nmain / procurement / order", 1, 3, "normal", "orange"),
    ("G", "DeepAgents 发现与按需读取\n按 Agent 作用域使用", 1, 4, "normal", "purple"),
    ("H", "结合工具和数据执行\n形成业务结果", 1, 5, "end", "blue"),
], [("A","C",""),("B","D","保存"),("B","F","写入目标作用域"),("D","E",""),
    ("C","F",""),("E","F",""),("F","G",""),("G","H","")]))
boards.append(flow("storage-responsibilities-zh", "状态与文件的存储分工", "7.1", [
    ("A", "Agent 执行与 Web 会话", 1, 0, "normal", "purple"),
    ("B", "MySQL agent_db\n检查点 / 历史 / 账号 / 绑定", 0, 1, "normal", "teal"),
    ("C", "CompositeBackend\n按路径选择后端", 2, 1, "normal", "orange"),
    ("D", "恢复任务与还原界面\n两套数据分别使用", 0, 2, "normal", "blue"),
    ("E", "StoreBackend → InMemoryStore\n偏好按用户 / 技能共享", 1, 2, "normal", "gray"),
    ("F", "SandboxBackendProxy\n其余工作文件与分析产物", 3, 2, "normal", "gray"),
    ("G", "download_sandbox_file\n复制到服务端 download 目录", 3, 3, "normal", "teal"),
    ("H", "ERP MCP → Java ERP\n业务库保存订单等正式数据", 1, 4, "normal", "orange"),
], [("A","B","执行状态与展示消息"),("A","C","文件操作"),("B","D","跨进程保存"),
    ("C","E","memories / persisted-skills"),("C","F","其他路径"),("F","G","显式导出"),
    ("A","H","业务读写")]))
boards.append(flow("sandbox-lifecycle-zh", "用户沙箱获取与故障恢复", "8.2", [
    ("A", "按 user_id 获取环境", 1, 0, "end", "blue"),
    ("B", "内存代理缓存命中？", 1, 1, "decision", "purple"),
    ("C", "有可认领的预热沙箱？", 0, 2, "decision", "purple"),
    ("D", "数据库绑定可重连？", 0, 3, "decision", "purple"),
    ("E", "新建沙箱\n安装依赖 / 保存绑定", 0, 4, "normal", "orange"),
    ("F", "返回用户级代理\n同用户多会话共享", 2, 4, "normal", "teal"),
    ("G", "abefore_agent 健康检查", 2, 5, "decision", "purple"),
    ("H", "重建基础环境\n上传 AGENTS.md", 1, 6, "normal", "orange"),
    ("I", "代理替换内部 backend\n调用方继续持有原代理", 1, 7, "normal", "teal"),
    ("J", "进入本轮 Agent 执行\n旧任意工作文件不自动迁移", 2, 8, "end", "blue"),
], [("A","B",""),("B","F","命中"),("B","C","未命中"),("C","F","认领成功"),
    ("C","D","不可用"),("D","F","重连成功"),("D","E","不可用"),("E","F",""),
    ("F","G",""),("G","J","健康"),("G","H","失败"),("H","I",""),("I","J","")]))
boards.append(flow("sse-interaction-zh", "SSE 展示与人工交互闭环", "10.3", [
    ("A", "浏览器 fetch POST\nCookie + 请求正文", 1, 0, "normal", "blue"),
    ("B", "FastAPI\n认证 / 归属 / 输入检查", 1, 1, "normal", "teal"),
    ("C", "Agent astream\n模型消息 + 图状态", 1, 2, "normal", "purple"),
    ("D", "SSE 事件\ntoken / tool_* / interrupt", 1, 3, "normal", "orange"),
    ("E", "前端按调用 ID 关联\n最终答复 / 执行过程 / 图片", 0, 4, "normal", "blue"),
    ("F", "人工中断组件\n补充信息或同意拒绝", 2, 4, "normal", "blue"),
    ("G", "POST resume\n再次校验后恢复", 2, 5, "normal", "teal"),
    ("H", "展示消息入 MySQL\n会话历史重新加载", 0, 5, "normal", "gray"),
    ("I", "done / error\n本轮结束或显示失败", 1, 6, "end", "blue"),
], [("A","B",""),("B","C","通过"),("C","D","流式"),("D","E","文本与工具事件"),
    ("D","F","interrupt"),("F","G","用户操作"),("G","C","Command resume"),
    ("E","H","流结束时保存"),("D","I","终止事件")]))
boards.append(flow("design-value-map-zh", "核心机制与设计价值", "11", [
    ("A", "采购任务复杂且包含写入", 1, 0, "normal", "blue"),
    ("B", "专业分工 + 人工审批", 0, 1, "normal", "purple"),
    ("C", "预计算 + 按请求绑定\n统一图表 + 按需技能", 1, 1, "normal", "purple"),
    ("D", "检查点 + 展示历史\n沙箱代理 + 健康恢复", 2, 1, "normal", "purple"),
    ("E", "职责和业务动作可检查", 0, 2, "normal", "teal"),
    ("F", "减少重复配置与上下文", 1, 2, "normal", "teal"),
    ("G", "任务可恢复 / 结果可阅读", 2, 2, "normal", "teal"),
    ("H", "真实任务评估\n质量 / 成本 / 延迟 / 成功率", 1, 3, "end", "orange"),
], [("A","B",""),("A","C",""),("A","D",""),("B","E",""),("C","F",""),
    ("D","G",""),("E","H","验证价值"),("F","H","验证价值"),("G","H","验证价值")]))
boards.append(flow("module-dependencies-zh", "正式运行入口与模块依赖", "12", [
    ("A", "frontend/src\nVue 工作台", 1, 0, "normal", "blue"),
    ("B", "api_view.web_main\nAPI / 认证 / AgentLoader", 1, 1, "normal", "teal"),
    ("C", "agent.main_agent\n预计算 / 主子 Agent 工厂", 1, 2, "normal", "purple"),
    ("D", "agent.tools / middlewares\n工具、校验与运行钩子", 0, 3, "normal", "orange"),
    ("E", "agent.backends\n沙箱与文件路由", 1, 3, "normal", "orange"),
    ("F", "agent.mysql_storage\n账号 / 状态 / 历史", 2, 3, "normal", "gray"),
    ("G", "mcp_server.server_main\nERP MCP → Java REST", 0, 4, "normal", "teal"),
    ("H", "skills / agent.memory\n技能与 Agent 指引", 1, 4, "normal", "gray"),
], [("A","B","HTTP / SSE"),("B","C","构图与运行"),("B","F","会话与历史"),
    ("C","D",""),("C","E",""),("C","F","checkpointer"),("D","G","MCP"),
    ("C","H","读取 / 同步")]))
boards.append(flow("startup-dependencies-zh", "部署依赖与启动检查", "13", [
    ("A", "配置环境与依赖\n模型 / MySQL / ERP / 沙箱", 1, 0, "normal", "blue"),
    ("B", "准备外部服务\nJava ERP / 图表 MCP / OpenSandbox", 0, 1, "normal", "orange"),
    ("C", "初始化 MySQL\nscripts/init_mysql.py", 2, 1, "normal", "gray"),
    ("D", "启动 ERP MCP\nserver_main", 0, 2, "normal", "teal"),
    ("E", "启动 Web\nstart_web.py", 1, 3, "normal", "teal"),
    ("F", "FastAPI 启动阶段\nMySQL 初始化 / MCP 预计算", 1, 4, "normal", "purple"),
    ("G", "沙箱预热\n失败可留到请求时创建", 0, 5, "normal", "orange"),
    ("H", "启动失败并检查依赖\n不降级为内存检查点", 2, 5, "end", "gray"),
    ("I", "接受 Web 请求\n验证 SSE 与真实业务链路", 0, 6, "end", "blue"),
], [("A","B",""),("A","C",""),("B","D",""),("D","E",""),("C","E",""),
    ("E","F",""),("F","G","关键初始化成功"),("F","H","关键初始化失败"),("G","I","继续启动")]))
boards.append(flow("improvement-roadmap-zh", "后续建设顺序与验收", "14", [
    ("A", "P0 权限与订单一致性\n会话归属 / 服务鉴权 / 幂等", 1, 0, "normal", "orange"),
    ("B", "P1 持久化与隔离\n持久 Store / 技能权限 / 沙箱协调", 1, 1, "normal", "purple"),
    ("C", "P1 完整交付与运行控制\n鉴权下载 / 标题 / 取消 / 内容边界", 1, 2, "normal", "purple"),
    ("D", "P2 外部能力与兼容\n图表降级 / 独立表格工具绑定", 1, 3, "normal", "teal"),
    ("E", "持续以真实任务验收\n权限测试 / 重放 / 恢复 / 成本延迟", 1, 4, "end", "blue"),
], [("A","B","先解决越权与重复写入"),("B","C","再完善状态与交付"),
    ("C","D","按需扩展能力"),("D","E","验证后对外承诺")]))

for board in boards:
    tree = ET.parse(ROOT / (board["name"] + ".drawio"))
    cells = tree.findall(".//mxCell")
    ids = [c.attrib["id"] for c in cells]
    assert len(ids) == len(set(ids))
    for c in cells:
        if c.get("edge"):
            assert c.get("source") in ids and c.get("target") in ids
            assert c.find("mxGeometry") is not None
    assert "\t" not in board["dsl"]
(ROOT / "boards.json").write_text(json.dumps(boards, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(f"Created and validated {len(boards)} editable draw.io diagrams and matching Yuque DSL sources.")
