"""
DeepAgent Chat API - FastAPI 主应用

提供基于 DeepAgent 的多智能体分析平台 API。
"""

from contextlib import asynccontextmanager
import logging
import os
from time import perf_counter

from fastapi import FastAPI, Response
from fastapi.responses import Response as RawResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from fastapi.middleware.cors import CORSMiddleware

from api_view.api import auth, chat, decisions, evaluations, history
from api_view.agent_loader import agent_loader
from agent.logging_config import configure_logging
from api_view.observability import configure_opentelemetry

logger = logging.getLogger(__name__)

API_TITLE = "DeepAgent API"
API_VERSION = "0.1"
API_DESCRIPTION = "基于DeepAgent的多智能体分析平台API"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """初始化 Agent，并在应用退出时释放沙箱和存储连接。"""
    configure_logging()
    started = perf_counter()
    logger.info("正在初始化采购助手...")
    await agent_loader.initialize()
    logger.info("采购助手就绪，初始化耗时 %.1f 秒", perf_counter() - started)
    try:
        yield
    finally:
        await agent_loader.shutdown()
        logger.info("采购助手资源已释放")


# 创建 FastAPI 应用
app = FastAPI(
    title=API_TITLE,
    description=API_DESCRIPTION,
    version=API_VERSION,
    lifespan=lifespan,
)
configure_opentelemetry(app)

# ============================================================
# CORS 中间件配置
# ============================================================
# 允许跨域请求，方便前端开发
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in os.getenv("CORS_ORIGINS", "").split(",")
                   if origin.strip() and origin.strip() != "*"] or [
        f"http://127.0.0.1:{os.getenv('FRONTEND_PORT', '3000')}",
        f"http://localhost:{os.getenv('FRONTEND_PORT', '3000')}",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# 注册路由
# ============================================================
# 对话相关接口
app.include_router(auth.router, prefix="/api", tags=["账号"])
app.include_router(chat.router, prefix="/api", tags=["对话"])
# 历史记录相关接口
app.include_router(history.router, prefix="/api", tags=["历史记录"])
app.include_router(decisions.router, prefix="/api", tags=["决策证据"])
app.include_router(evaluations.router, prefix="/api", tags=["决策评测"])


# ============================================================
# 根路径
# ============================================================
@app.get("/", tags=["首页"])
async def root():
    """
    根路径

    返回 API 基本信息
    """
    return {
        "name": API_TITLE,
        "version": API_VERSION,
        "description": "基于 DeepAgent 的 AI 对话系统 API",
        "docs": "/docs",
        "redoc": "/redoc"
    }


# ============================================================
# 健康检查
# ============================================================
@app.get("/health", tags=["系统"])
async def health_check(response: Response):
    """
    健康检查接口

    用于检查服务是否正常运行
    """
    # 启动器用此标识区分本次实例与占用相同端口的旧服务。
    launch_id = os.environ.get("DEEPAGENT_LAUNCH_ID")
    if launch_id:
        response.headers["X-DeepAgent-Launch-Id"] = launch_id
    return {
        "status": "healthy",
        "service": API_TITLE,
        "version": API_VERSION
    }


@app.get("/metrics", include_in_schema=False)
async def metrics():
    return RawResponse(generate_latest(), media_type=CONTENT_TYPE_LATEST)


# ============================================================
# 启动命令
# ============================================================
# uvicorn api_view.web_main:app --reload --host 0.0.0.0 --port 8000
