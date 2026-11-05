A[加载图表 MCP 工具\n读取原始 schema] --> B[按工具名分类\n识别当前可用图表类型]
B -->|原始约束| C[后端保留完整能力\ntool_map 与 validators]
B -->|统一入口| D[构建 generate_chart\n类型枚举 + 简短说明]
B -->|未合并工具| E[其余工具单独保留\nextra_mcp_tools]
C -->|封装时引用| D
D -->|仅直接提供一个图表工具| F([按请求绑定分析子 Agent\n主 Agent 委派分析任务])
