"""MySQL persistence for graph checkpoints, display messages and sandbox bindings.

Connections are opened during async startup, never while importing configuration.
The four checkpoint tables are managed by langgraph-checkpoint-mysql 3.0.0.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

import aiomysql
from dotenv import load_dotenv

from langgraph.checkpoint.mysql.aio import AIOMySQLSaver


@dataclass(frozen=True)
class MySQLSettings:
    host: str = "localhost"
    port: int = 3306
    user: str = "root"
    password: str = field(default="", repr=False)
    database: str = "agent_db"
    connect_timeout: int = 5
    auto_create_database: bool = True

    def __post_init__(self):
        if not re.fullmatch(r"[A-Za-z0-9_]{1,64}", self.database):
            raise ValueError("MYSQL_DATABASE 只能包含字母、数字、下划线，长度为 1–64")

    @classmethod
    def from_env(cls):
        # MySQL uses project-local settings so CLI and Web connect to the same database.
        load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=True)
        return cls(
            host=os.getenv("MYSQL_HOST", "localhost"),
            port=int(os.getenv("MYSQL_PORT", "3306")),
            user=os.getenv("MYSQL_USER", "root"),
            password=os.getenv("MYSQL_PASSWORD", ""),
            database=os.getenv("MYSQL_DATABASE", "agent_db"),
            connect_timeout=int(os.getenv("MYSQL_CONNECT_TIMEOUT", "5")),
            auto_create_database=os.getenv("MYSQL_AUTO_CREATE_DATABASE", "true").lower()
            in {"true", "1", "yes"},
        )

    def connection_options(self):
        return dict(
            host=self.host, port=self.port, user=self.user, password=self.password,
            connect_timeout=self.connect_timeout, charset="utf8mb4", autocommit=True,
        )


async def _create_table_if_missing(cur, table_name, ddl):
    # Avoid issuing redundant CREATE statements on every startup. Keep SQL
    # errors and other warnings visible instead of globally silencing aiomysql.
    await cur.execute(
        "SELECT 1 FROM information_schema.TABLES "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s", (table_name,),
    )
    if await cur.fetchone() is None:
        await cur.execute(ddl)


class CompatibleAIOMySQLSaver(AIOMySQLSaver):
    """Keep the upstream schema while avoiding MD5 in generated columns.

    MySQL >= 9.6 disallows MD5 in generated columns. Upstream 3.0.0 migrations
    15–17 temporarily create such columns; migrations 19–21 make them ordinary
    columns again. Create/backfill ordinary columns directly with the same keys
    and migration version numbers. Runtime checkpoint serialization stays upstream.
    """

    async def setup(self):
        hash_migrations = {
            15: ("checkpoints", "thread_id, checkpoint_ns_hash, checkpoint_id"),
            16: ("checkpoint_blobs", "thread_id, checkpoint_ns_hash, channel, version"),
            17: ("checkpoint_writes", "thread_id, checkpoint_ns_hash, checkpoint_id, task_id, idx"),
        }
        async with self._cursor() as cur:
            await _create_table_if_missing(cur, "checkpoint_migrations", self.MIGRATIONS[0])
            await cur.execute("SELECT v FROM checkpoint_migrations ORDER BY v DESC LIMIT 1")
            row = await cur.fetchone()
            version = row["v"] if row else -1
            for v in range(version + 1, len(self.MIGRATIONS)):
                if v in hash_migrations:
                    table, key = hash_migrations[v]
                    await cur.execute(
                        "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
                        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s "
                        "AND COLUMN_NAME = 'checkpoint_ns_hash'", (table,),
                    )
                    if await cur.fetchone() is None:
                        await cur.execute(
                            f"ALTER TABLE {table} ADD COLUMN checkpoint_ns_hash BINARY(16)"
                        )
                    await cur.execute(
                        f"UPDATE {table} SET checkpoint_ns_hash = UNHEX(MD5(checkpoint_ns))"
                    )
                    await cur.execute(
                        f"ALTER TABLE {table} DROP PRIMARY KEY, ADD PRIMARY KEY ({key})"
                    )
                elif v != 0:  # Migration 0's table was already ensured above.
                    await cur.execute(self.MIGRATIONS[v])
                await cur.execute("INSERT INTO checkpoint_migrations (v) VALUES (%s)", (v,))


APPLICATION_DDL = {
    "app_users": """CREATE TABLE IF NOT EXISTS app_users (
        username VARCHAR(32) NOT NULL PRIMARY KEY,
        password_hash VARCHAR(255) NOT NULL,
        created_at DATETIME(6) NOT NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin""",
    "auth_sessions": """CREATE TABLE IF NOT EXISTS auth_sessions (
        token_hash CHAR(64) NOT NULL PRIMARY KEY,
        username VARCHAR(32) NOT NULL,
        expires_at DATETIME(6) NOT NULL,
        created_at DATETIME(6) NOT NULL,
        INDEX idx_auth_expiry (expires_at),
        FOREIGN KEY (username) REFERENCES app_users(username) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin""",
    "chat_projects": """CREATE TABLE IF NOT EXISTS chat_projects (
        project_id VARCHAR(64) NOT NULL PRIMARY KEY,
        user_id VARCHAR(32) NOT NULL,
        name VARCHAR(40) NOT NULL,
        created_at DATETIME(6) NOT NULL,
        updated_at DATETIME(6) NOT NULL,
        INDEX idx_project_user (user_id, updated_at),
        FOREIGN KEY (user_id) REFERENCES app_users(username) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin""",
    "chat_threads": """CREATE TABLE IF NOT EXISTS chat_threads (
        thread_id VARCHAR(150) NOT NULL PRIMARY KEY,
        user_id VARCHAR(32) NOT NULL,
        project_id VARCHAR(64) NULL,
        custom_title VARCHAR(80) NULL,
        pinned TINYINT(1) NOT NULL DEFAULT 0,
        created_at DATETIME(6) NOT NULL,
        INDEX idx_thread_user (user_id, pinned, created_at),
        INDEX idx_thread_project (project_id),
        FOREIGN KEY (user_id) REFERENCES app_users(username) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin""",
    "session_display_messages": """CREATE TABLE IF NOT EXISTS session_display_messages (
        thread_id VARCHAR(150) NOT NULL,
        message_index INT UNSIGNED NOT NULL,
        message JSON NOT NULL,
        updated_at DATETIME(6) NOT NULL,
        PRIMARY KEY (thread_id, message_index)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin""",
    "sandbox_registry": """CREATE TABLE IF NOT EXISTS sandbox_registry (
        user_id VARCHAR(150) NOT NULL PRIMARY KEY,
        sandbox_id VARCHAR(255) NOT NULL,
        created_at DATETIME(6) NOT NULL,
        updated_at DATETIME(6) NOT NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin""",
}


def _json_default(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise TypeError(f"Unsupported display message value: {type(value).__name__}")


class MySQLStorage:
    def __init__(self, settings: MySQLSettings | None = None):
        self.settings = settings
        self.pool = None
        self._checkpointer = None
        self._initialize_lock = asyncio.Lock()

    @property
    def checkpointer(self):
        if self._checkpointer is None:
            raise RuntimeError("MySQL 尚未初始化，请先 await mysql_storage.initialize()")
        return self._checkpointer

    async def initialize(self):
        async with self._initialize_lock:
            if self._checkpointer is not None:
                return
            settings = self.settings or MySQLSettings.from_env()
            self.settings = settings
            options = settings.connection_options()
            if settings.auto_create_database:
                async with aiomysql.connect(**options) as conn:
                    async with conn.cursor() as cur:
                        await cur.execute(
                            "SELECT 1 FROM information_schema.SCHEMATA WHERE SCHEMA_NAME = %s",
                            (settings.database,),
                        )
                        if await cur.fetchone() is None:
                            await cur.execute(
                                f"CREATE DATABASE IF NOT EXISTS `{settings.database}` "
                                "CHARACTER SET utf8mb4 COLLATE utf8mb4_bin"
                            )
            self.pool = await aiomysql.create_pool(
                **options, db=settings.database, minsize=1, maxsize=10,
                pool_recycle=1800,
            )
            try:
                saver = CompatibleAIOMySQLSaver(self.pool)
                await saver.setup()
                async with self.cursor() as cur:
                    for table_name, ddl in APPLICATION_DDL.items():
                        await _create_table_if_missing(cur, table_name, ddl)
                    await self._upgrade_application_schema(cur)
                self._checkpointer = saver
            except BaseException:
                await self.close()
                raise

    async def _upgrade_application_schema(self, cur):
        """为已有本地数据库补充会话项目、标题与置顶字段。"""
        await cur.execute(
            "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'chat_threads'"
        )
        columns = {row["COLUMN_NAME"] for row in await cur.fetchall()}
        additions = {
            "project_id": "ALTER TABLE chat_threads ADD COLUMN project_id VARCHAR(64) NULL",
            "custom_title": "ALTER TABLE chat_threads ADD COLUMN custom_title VARCHAR(80) NULL",
            "pinned": "ALTER TABLE chat_threads ADD COLUMN pinned TINYINT(1) NOT NULL DEFAULT 0",
        }
        for name, ddl in additions.items():
            if name not in columns:
                await cur.execute(ddl)

    async def close(self):
        self._checkpointer = None
        if self.pool is not None:
            self.pool.close()
            await self.pool.wait_closed()
            self.pool = None

    @asynccontextmanager
    async def cursor(self, *, transaction=False):
        if self.pool is None:
            raise RuntimeError("MySQL 连接池尚未初始化")
        async with self.pool.acquire() as conn:
            if transaction:
                await conn.begin()
            try:
                async with conn.cursor(aiomysql.DictCursor) as cur:
                    yield cur
                if transaction:
                    await conn.commit()
            except BaseException:
                if transaction:
                    await conn.rollback()
                raise

    async def get_all_thread_ids(self, user_id=None):
        async with self.cursor() as cur:
            if user_id is None:
                # CLI/maintenance callers; Web requests always supply the authenticated user.
                await cur.execute(
                    "SELECT DISTINCT thread_id FROM checkpoints "
                    "WHERE checkpoint_ns = '' AND thread_id NOT IN ('', '*')"
                )
            else:
                await cur.execute(
                    "SELECT thread_id FROM chat_threads WHERE user_id = %s "
                    "ORDER BY created_at DESC", (user_id,),
                )
            return [row["thread_id"] for row in await cur.fetchall()]

    async def get_thread_metadata(self, user_id):
        async with self.cursor() as cur:
            await cur.execute(
                "SELECT thread_id, project_id, custom_title, pinned, created_at "
                "FROM chat_threads WHERE user_id = %s", (user_id,),
            )
            return {row["thread_id"]: row for row in await cur.fetchall()}

    async def update_thread_metadata(self, thread_id, user_id, *, title=None, pinned=None,
                                     project_id_marker=False, project_id=None):
        updates = []
        values = []
        if title is not None:
            updates.append("custom_title = %s")
            values.append(title)
        if pinned is not None:
            updates.append("pinned = %s")
            values.append(1 if pinned else 0)
        if project_id_marker:
            if project_id is not None and not await self.project_belongs_to(project_id, user_id):
                return False
            updates.append("project_id = %s")
            values.append(project_id)
        if not updates:
            return True
        values.extend([thread_id, user_id])
        async with self.cursor() as cur:
            await cur.execute(
                f"UPDATE chat_threads SET {', '.join(updates)} WHERE thread_id = %s AND user_id = %s",
                tuple(values),
            )
            return cur.rowcount == 1

    async def create_project(self, project_id, user_id, name):
        async with self.cursor() as cur:
            await cur.execute(
                "INSERT INTO chat_projects (project_id, user_id, name, created_at, updated_at) "
                "VALUES (%s, %s, %s, UTC_TIMESTAMP(6), UTC_TIMESTAMP(6))",
                (project_id, user_id, name),
            )

    async def list_projects(self, user_id):
        async with self.cursor() as cur:
            await cur.execute(
                "SELECT project_id, name, created_at, updated_at FROM chat_projects "
                "WHERE user_id = %s ORDER BY updated_at DESC, created_at DESC", (user_id,),
            )
            return await cur.fetchall()

    async def update_project(self, project_id, user_id, name):
        async with self.cursor() as cur:
            await cur.execute(
                "UPDATE chat_projects SET name = %s, updated_at = UTC_TIMESTAMP(6) "
                "WHERE project_id = %s AND user_id = %s",
                (name, project_id, user_id),
            )
            return cur.rowcount == 1

    async def delete_project(self, project_id, user_id):
        """删除项目容器；其中的会话保留并转为独立会话。"""
        async with self.cursor(transaction=True) as cur:
            await cur.execute(
                "SELECT 1 FROM chat_projects WHERE project_id = %s AND user_id = %s FOR UPDATE",
                (project_id, user_id),
            )
            if await cur.fetchone() is None:
                return False
            await cur.execute(
                "UPDATE chat_threads SET project_id = NULL "
                "WHERE project_id = %s AND user_id = %s",
                (project_id, user_id),
            )
            await cur.execute(
                "DELETE FROM chat_projects WHERE project_id = %s AND user_id = %s",
                (project_id, user_id),
            )
            return cur.rowcount == 1

    async def project_belongs_to(self, project_id, user_id):
        async with self.cursor() as cur:
            await cur.execute(
                "SELECT 1 FROM chat_projects WHERE project_id = %s AND user_id = %s",
                (project_id, user_id),
            )
            return await cur.fetchone() is not None

    async def get_session_updated_at(self, thread_id):
        async with self.cursor() as cur:
            await cur.execute(
                "SELECT COALESCE("
                "(SELECT MAX(updated_at) FROM session_display_messages WHERE thread_id = %s), "
                "(SELECT JSON_UNQUOTE(JSON_EXTRACT(checkpoint, '$.ts')) FROM checkpoints "
                " WHERE thread_id = %s AND checkpoint_ns = '' ORDER BY checkpoint_id DESC LIMIT 1), "
                "(SELECT created_at FROM chat_threads WHERE thread_id = %s)"
                ") AS ts",
                (thread_id, thread_id, thread_id),
            )
            row = await cur.fetchone()
        if row and row["ts"]:
            if isinstance(row["ts"], datetime):
                return row["ts"].replace(tzinfo=timezone.utc)
            return datetime.fromisoformat(row["ts"].replace("Z", "+00:00"))
        return datetime.now(timezone.utc)

    async def delete_session(self, thread_id):
        # Delete all graph namespaces and their payloads together with UI history.
        async with self.cursor(transaction=True) as cur:
            for table in ("checkpoint_writes", "checkpoint_blobs", "checkpoints",
                          "session_display_messages", "chat_threads"):
                await cur.execute(f"DELETE FROM {table} WHERE thread_id = %s", (thread_id,))

    async def save_display_messages(self, thread_id, messages):
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        rows = []
        for index, original in enumerate(messages):
            msg = dict(original)
            for field_name in ("text", "content", "args"):
                value = msg.get(field_name)
                if isinstance(value, str) and len(value) > 500_000:
                    msg[field_name] = value[:500_000] + "\n\n...(内容过长已截断)"
            rows.append((thread_id, index, json.dumps(
                msg, ensure_ascii=False, default=_json_default,
            ), now))
        # If an insert fails, the previous history remains intact.
        async with self.cursor(transaction=True) as cur:
            await cur.execute(
                "DELETE FROM session_display_messages WHERE thread_id = %s", (thread_id,),
            )
            if rows:
                await cur.executemany(
                    "INSERT INTO session_display_messages "
                    "(thread_id, message_index, message, updated_at) VALUES (%s, %s, %s, %s)",
                    rows,
                )

    async def get_display_messages(self, thread_id):
        async with self.cursor() as cur:
            await cur.execute(
                "SELECT message FROM session_display_messages "
                "WHERE thread_id = %s ORDER BY message_index", (thread_id,),
            )
            rows = await cur.fetchall()
        return [json.loads(row["message"]) for row in rows] if rows else None

    async def get_sandbox(self, user_id):
        async with self.cursor() as cur:
            await cur.execute("SELECT * FROM sandbox_registry WHERE user_id = %s", (user_id,))
            return await cur.fetchone()

    async def bind_sandbox(self, user_id, sandbox_id):
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        async with self.cursor() as cur:
            await cur.execute(
                "INSERT INTO sandbox_registry (user_id, sandbox_id, created_at, updated_at) "
                "VALUES (%s, %s, %s, %s) ON DUPLICATE KEY UPDATE "
                "sandbox_id = %s, updated_at = %s",
                (user_id, sandbox_id, now, now, sandbox_id, now),
            )

    async def delete_sandbox(self, user_id):
        async with self.cursor() as cur:
            await cur.execute("DELETE FROM sandbox_registry WHERE user_id = %s", (user_id,))

    async def create_user(self, username, password_hash):
        async with self.cursor() as cur:
            try:
                await cur.execute(
                    "INSERT INTO app_users (username, password_hash, created_at) "
                    "VALUES (%s, %s, UTC_TIMESTAMP(6))", (username, password_hash),
                )
            except aiomysql.IntegrityError as exc:
                if exc.args[0] == 1062:
                    return False
                raise
        return True

    async def get_user(self, username):
        async with self.cursor() as cur:
            await cur.execute("SELECT username, password_hash FROM app_users WHERE username = %s", (username,))
            return await cur.fetchone()

    async def create_auth_session(self, token_hash, username, expires_at):
        async with self.cursor(transaction=True) as cur:
            await cur.execute("DELETE FROM auth_sessions WHERE expires_at <= UTC_TIMESTAMP(6)")
            await cur.execute(
                "INSERT INTO auth_sessions (token_hash, username, expires_at, created_at) "
                "VALUES (%s, %s, %s, UTC_TIMESTAMP(6))", (token_hash, username, expires_at),
            )

    async def get_auth_user(self, token_hash):
        async with self.cursor() as cur:
            await cur.execute(
                "SELECT username FROM auth_sessions "
                "WHERE token_hash = %s AND expires_at > UTC_TIMESTAMP(6)", (token_hash,),
            )
            return await cur.fetchone()

    async def delete_auth_session(self, token_hash):
        async with self.cursor() as cur:
            await cur.execute("DELETE FROM auth_sessions WHERE token_hash = %s", (token_hash,))

    async def register_thread(self, thread_id, user_id, project_id=None):
        if project_id is not None and not await self.project_belongs_to(project_id, user_id):
            raise ValueError("项目不存在或不属于当前用户")
        async with self.cursor() as cur:
            await cur.execute(
                "INSERT INTO chat_threads (thread_id, user_id, project_id, created_at) "
                "VALUES (%s, %s, %s, UTC_TIMESTAMP(6))", (thread_id, user_id, project_id),
            )

    async def thread_belongs_to(self, thread_id, user_id):
        async with self.cursor() as cur:
            await cur.execute(
                "SELECT 1 FROM chat_threads WHERE thread_id = %s AND user_id = %s", (thread_id, user_id),
            )
            return await cur.fetchone() is not None


mysql_storage = MySQLStorage()
