A([订单请求\n查询并定位业务对象]) --> B{订单参数完整？}
B -->|否| C[request_order_info\n中断并等待补充]
C -->|等待输入| D[用户补充文本\n身份校验 + 输入检查]
D -->|resume| B
B -->|是| E[准备创建或更新参数\norder_create / order_update]
E --> F[执行前中断\nMySQL 检查点保存]
F -->|等待审批| G{用户审批决定}
G -->|同意| H[approve → resume\n执行 ERP 订单接口]
G -->|拒绝| I[reject → resume\n不执行该写入动作]
H --> J[核对 ERP 返回结果\n成功 / 失败分别反馈]
J --> K([返回用户并保存历史\nSSE / 展示消息])
I --> K
