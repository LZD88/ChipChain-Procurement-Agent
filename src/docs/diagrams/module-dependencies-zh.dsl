A[frontend/src\nVue 工作台] -->|HTTP / SSE| B[api_view.web_main\nAPI / 认证 / AgentLoader]
B -->|构图与运行| C[agent.main_agent\n预计算 / 主子 Agent 工厂]
B -->|会话与历史| F[agent.mysql_storage\n账号 / 状态 / 历史]
C --> D[agent.tools / middlewares\n工具、校验与运行钩子]
C --> E[agent.backends\n沙箱与文件路由]
C -->|checkpointer| F
D -->|MCP| G[mcp_server.server_main\nERP MCP → Java REST]
C -->|读取 / 同步| H[skills / agent.memory\n技能与 Agent 指引]
