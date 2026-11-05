A[浏览器 fetch POST\nCookie + 请求正文] --> B[FastAPI\n认证 / 归属 / 输入检查]
B -->|通过| C[Agent astream\n模型消息 + 图状态]
C -->|流式| D[SSE 事件\ntoken / tool_* / interrupt]
D -->|文本与工具事件| E[前端按调用 ID 关联\n最终答复 / 执行过程 / 图片]
D -->|interrupt| F[人工中断组件\n补充信息或同意拒绝]
F -->|用户操作| G[POST resume\n再次校验后恢复]
G -->|Command resume| C
E -->|流结束时保存| H[展示消息入 MySQL\n会话历史重新加载]
D -->|终止事件| I([done / error\n本轮结束或显示失败])
