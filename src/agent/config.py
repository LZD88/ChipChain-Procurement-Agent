from datetime import timedelta
from pathlib import Path

import httpx
from langchain_openai import ChatOpenAI
from langgraph.store.memory import InMemoryStore
from opensandbox.config import ConnectionConfig

from agent.env_utils import (
    GLM_API_KEY, SANDBOX_DOMAIN, SANDBOX_USE_SERVER_PROXY,
    SANDBOX_API_KEY, GLM_BASE_URL, DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL,
)

# ---------- 模型配置 ----------
# 主 Agent 模型
MAIN_MODEL = ChatOpenAI(
    model="deepseek-flash",
    temperature=0.1,
    openai_api_key=DEEPSEEK_API_KEY,
    openai_api_base=DEEPSEEK_BASE_URL,
    max_tokens=30000,
    extra_body={"thinking": {"type": "disabled"}},
)
# 摘要专用模型（摘要需要稳定输出，temperature 设为较低值）
SUMMARY_MODEL = ChatOpenAI(
    model="deepseek-flash",
    temperature=0.3,
    openai_api_key=DEEPSEEK_API_KEY,
    openai_api_base=DEEPSEEK_BASE_URL,
    max_tokens=30000,
    extra_body={"thinking": {"type": "disabled"}},
)
# 输入安全审查模型单独配置在 agent/guardrails/config.yml。
# ---------- 沙箱配置 ----------
# OpenSandbox 沙箱配置连接（异步客户端 Sandbox 使用）
SANDBOX_CONFIG = ConnectionConfig(
    domain=SANDBOX_DOMAIN,
    api_key=SANDBOX_API_KEY,
    use_server_proxy=SANDBOX_USE_SERVER_PROXY,
    request_timeout=timedelta(seconds=60),
    transport=httpx.AsyncHTTPTransport(limits=httpx.Limits(max_connections=20)),
)

# ---------- 路径常量 ----------
EXAMPLE_DIR = Path(__file__).parent.parent
# 沙箱内技能根路径
SANDBOX_SKILLS_ROOT = "/skills"
# 本地技能资源目录（项目内的路径，相对于项目根）
LOCAL_SKILLS_DIR = EXAMPLE_DIR / "skills"
# 本地下载目录（从沙箱下载文件的目标路径）
DOWNLOAD_DIR = EXAMPLE_DIR / "download"
# 本地的Agent记忆文件
LOCAL_AGENTS_MD = EXAMPLE_DIR / "agent/memory/AGENTS.md"

# ---------- 文件名常量 ----------
# 主 Agent 只读指引文件（上传到沙箱 /AGENTS.md）
AGENTS_MD_FILENAME = "/AGENTS.md"

# ---------- 用户技能持久化 ----------
# 技能 StoreBackend 命名空间（按 Agent scope 组织，无用户隔离）
SKILLS_STORE_NAMESPACE = ("skills",)
# 子 Agent 名称 → 技能 scope 目录映射
SCOPE_MAP = {
    "main": "main",
    "procurement-analyst": "procurement",
    "procurement-order": "order",
}

# ---------- 持久化存储 ----------
# InMemoryStore: 开发阶段使用。生产环境替换为持久 Store。
STORE = InMemoryStore()

# MySQL 会话持久化由 agent.mysql_storage 在异步启动阶段初始化。
# /memories/ 和 /persisted-skills/ 仍使用上述 STORE，与会话 checkpoint 分开。


