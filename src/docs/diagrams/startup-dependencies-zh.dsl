A[配置环境与依赖\n模型 / MySQL / ERP / 沙箱] --> B[准备外部服务\nJava ERP / 图表 MCP / OpenSandbox]
A --> C[初始化 MySQL\nscripts/init_mysql.py]
B --> D[启动 ERP MCP\nserver_main]
D --> E[启动 Web\nstart_web.py]
C --> E
E --> F[FastAPI 启动阶段\nMySQL 初始化 / MCP 预计算]
F -->|关键初始化成功| G[沙箱预热\n失败可留到请求时创建]
F -->|关键初始化失败| H([启动失败并检查依赖\n不降级为内存检查点])
G -->|继续启动| I([接受 Web 请求\n验证 SSE 与真实业务链路])
