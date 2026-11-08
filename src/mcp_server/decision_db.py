"""Least-privilege database pool used by MCP decision operations."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

import aiomysql

from agent.mysql_storage import MySQLStorage


@dataclass(frozen=True)
class DecisionDBSettings:
    host: str = "localhost"
    port: int = 3306
    user: str = "chipchain_mcp"
    password: str = field(default="", repr=False)
    database: str = "agent_db"

    @classmethod
    def from_env(cls):
        return cls(
            host=os.getenv("DECISION_DB_HOST", os.getenv("MYSQL_HOST", "localhost")),
            port=int(os.getenv("DECISION_DB_PORT", os.getenv("MYSQL_PORT", "3306"))),
            user=os.getenv("DECISION_DB_USER", "chipchain_mcp"),
            password=os.getenv("DECISION_DB_PASSWORD", ""),
            database=os.getenv("DECISION_DB_NAME", os.getenv("MYSQL_DATABASE", "agent_db")),
        )

    def pool_options(self):
        return dict(
            host=self.host, port=self.port, user=self.user, password=self.password,
            db=self.database, charset="utf8mb4", autocommit=True,
            minsize=1, maxsize=10, pool_recycle=1800,
        )


async def create_decision_storage(settings: DecisionDBSettings | None = None):
    settings = settings or DecisionDBSettings.from_env()
    if not settings.password:
        raise RuntimeError("DECISION_DB_PASSWORD is required for actionable MCP tools")
    pool = await aiomysql.create_pool(**settings.pool_options())
    storage = MySQLStorage()
    storage.pool = pool
    return storage
