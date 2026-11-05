+ 用户交互层
    - Vue 3 工作台
        * 登录注册 / 会话列表
        * SSE 答复 / 图表预览
        * 补充信息 / 人工审批
+ 应用接口层
    - FastAPI
        * Cookie 认证 / 会话归属
        * NeMo 输入安全检查
        * Chat / Resume / History
    - AgentLoader
        * MCP 工具预计算
        * 按请求创建执行图
+ 智能编排层
    - DeepAgents / LangGraph
        * 主 Agent：协调与汇总
        * 分析子 Agent：查询与分析
        * 订单子 Agent：受控写入
+ 工具与业务集成层
    - ERP MCP
        * Java 芯链 REST API
    - 分析工具
        * 图表 MCP / generate_chart
        * 博查搜索 / 网页采集
    - 模型服务
        * deepseek-flash
        * Agent / 摘要 / 输入审查
+ 执行与文件后端
    - CompositeBackend
        * OpenSandbox：用户级沙箱
        * StoreBackend：偏好与技能
+ 状态与业务数据
    - MySQL agent_db
        * 检查点 / 展示历史
        * 账号 / 会话 / 沙箱绑定
    - InMemoryStore
        * 偏好 / 共享技能（进程内）
    - ERP 业务库
        * 物料 / 供应商 / 订单 / 库存
