"""应用入口统一配置日志；业务模块只获取 logger，不注册 handler。"""

import logging
import os


def configure_logging() -> None:
    """默认展示应用摘要和异常；LOG_LEVEL=DEBUG 开启执行细节。"""
    level = os.getenv("LOG_LEVEL", "INFO").strip().upper()
    if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        raise ValueError(f"无效的 LOG_LEVEL: {level}")

    handlers = [logging.StreamHandler()]
    if os.getenv("APP_ENV") == "production":
        handlers.append(logging.FileHandler("erp_agent.log", encoding="utf-8"))

    # 第三方库默认只输出警告；重复初始化时替换旧 handler，避免同一条日志多次输出。
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=handlers,
        force=True,
    )
    for name in ("agent", "api_view"):
        logging.getLogger(name).setLevel(level)
