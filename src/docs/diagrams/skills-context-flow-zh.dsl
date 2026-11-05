A[本地预置技能\nSKILL.md / 脚本 / 参考] --> C[SkillsSyncMiddleware\n增量同步]
B[运行期技能分配\nassign_skill] -->|保存| D[共享技能 Store\nnamespace = skills]
B -->|写入目标作用域| F[用户沙箱技能目录\nmain / procurement / order]
D --> E[UserSkillsRestore\n从 Store 恢复副本]
C --> F
E --> F
F --> G[DeepAgents 发现与按需读取\n按 Agent 作用域使用]
G --> H([结合工具和数据执行\n形成业务结果])
