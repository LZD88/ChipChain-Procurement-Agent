"""Transactional release registry and durable request attribution."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
import secrets
from uuid import uuid4

import aiomysql

from .bundles import PromptBundleSnapshot, builtin_bundle, canonical, digest


class ReleaseConflict(ValueError):
    pass


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def decode(value):
    return value if isinstance(value, (dict, list)) else json.loads(value)


def enabled():
    return os.getenv("AGENT_PROMPT_RELEASES_ENABLED", "false").lower() in {"true", "1", "yes"}


def bucket(user, salt):
    return int(digest([salt, user.lower()])[:16], 16) % 10000


class PromptRepository:
    def __init__(self, storage, environment="local"):
        self.storage = storage
        self.environment = environment

    async def audit(self, actor, action, outcome, metadata=None, *, cur=None):
        if cur is None:
            async with self.storage.cursor(transaction=True) as cursor:
                return await self.audit(actor, action, outcome, metadata, cur=cursor)
        await cur.execute("INSERT INTO prompt_management_audit(actor,action,outcome,metadata_json) VALUES(%s,%s,%s,%s)",
                          (actor, action, outcome, canonical(metadata or {})))

    async def create_version(self, roles, author, note, *, version=None):
        snapshot = PromptBundleSnapshot.create(version or str(uuid4()), roles)
        if not note.strip() or len(note) > 1000:
            raise ValueError("请填写 1–1000 字变更说明")
        try:
            async with self.storage.cursor(transaction=True) as cur:
                await cur.execute("INSERT INTO prompt_bundle_versions(version,fingerprint,roles_json,author,change_note) VALUES(%s,%s,%s,%s,%s)",
                                  (snapshot.version, snapshot.fingerprint, canonical(snapshot.roles), author, note))
                await self.audit(author, "VERSION_CREATE", "SUCCESS", {"version": snapshot.version}, cur=cur)
        except aiomysql.IntegrityError as exc:
            raise ReleaseConflict("提示词版本或内容已存在，请修改内容后创建新版本") from exc
        return snapshot

    async def get_version(self, version, *, cur=None):
        if cur is None:
            async with self.storage.cursor() as cursor:
                return await self.get_version(version, cur=cursor)
        await cur.execute("SELECT * FROM prompt_bundle_versions WHERE version=%s", (version,))
        row = await cur.fetchone()
        if not row:
            raise ValueError("提示词版本不存在")
        if row["schema_version"] != 1:
            raise ValueError("不支持的提示词 schema")
        snapshot = PromptBundleSnapshot.create(row["version"], decode(row["roles_json"]))
        if snapshot.fingerprint != row["fingerprint"]:
            raise ValueError("提示词内容指纹不一致")
        return snapshot

    async def list_versions(self, *, limit=50, offset=0):
        if not 1 <= limit <= 100 or offset < 0:
            raise ValueError("无效分页参数")
        async with self.storage.cursor() as cur:
            await cur.execute("SELECT version,fingerprint,schema_version,author,change_note,created_at FROM prompt_bundle_versions ORDER BY created_at DESC,version LIMIT %s OFFSET %s", (limit, offset))
            return list(await cur.fetchall())

    async def initialize(self, actor):
        builtin = builtin_bundle()
        async with self.storage.cursor() as cur:
            await cur.execute("SELECT version FROM prompt_bundle_versions WHERE version=%s", (builtin.version,))
            exists = await cur.fetchone()
        if not exists:
            try:
                await self.create_version(builtin.roles, actor, "代码内置稳定基线", version=builtin.version)
            except ReleaseConflict:
                pass
        stored = await self.get_version(builtin.version)
        if stored.fingerprint != builtin.fingerprint:
            raise ReleaseConflict("代码内置提示词已变更，必须显式迁移基线")
        state = {"stable": builtin.version, "canary": None, "previousStable": None,
                 "percentage": 0, "allowlist": [], "salt": secrets.token_hex(16), "releaseId": None,
                 "status": "STABLE", "revoked": [], "initializedAt": now().isoformat()}
        async with self.storage.cursor(transaction=True) as cur:
            await cur.execute("INSERT IGNORE INTO prompt_release_state(environment,state_json) VALUES(%s,%s)",
                              (self.environment, canonical(state)))
            if cur.rowcount:
                await self.audit(actor, "INITIALIZE", "SUCCESS", {"stable": builtin.version}, cur=cur)
        return await self.state()

    async def state(self, *, cur=None, lock=False):
        if cur is None:
            async with self.storage.cursor() as cursor:
                return await self.state(cur=cursor, lock=lock)
        await cur.execute("SELECT * FROM prompt_release_state WHERE environment=%s" + (" FOR UPDATE" if lock else ""), (self.environment,))
        row = await cur.fetchone()
        if not row:
            raise ReleaseConflict("提示词发布尚未初始化，请先导入内置基线")
        return {**decode(row["state_json"]), "revision": row["revision"], "environment": self.environment}

    async def history(self, kind="events", limit=50):
        table, order = {"events": ("prompt_release_events", "created_at"),
                        "audit": ("prompt_management_audit", "id"), "gates": ("prompt_release_gates", "created_at")}[kind]
        async with self.storage.cursor() as cur:
            where = " WHERE environment=%s" if kind == "events" else ""
            await cur.execute(f"SELECT * FROM {table}{where} ORDER BY {order} DESC LIMIT %s",
                              (self.environment, limit) if where else (limit,))
            rows = list(await cur.fetchall())
        for row in rows:
            for key in list(row):
                if key.endswith("_json"):
                    row[key[:-5]] = decode(row.pop(key))
        return rows

    async def admit(self, thread, user, route, *, resume=False, legacy=False):
        request_id = str(uuid4())
        async with self.storage.cursor(transaction=True) as cur:
            state = await self.state(cur=cur, lock=True)
            await cur.execute("SELECT user_id AS username,created_at FROM chat_threads WHERE thread_id=%s FOR UPDATE", (thread,))
            owner = await cur.fetchone()
            if not owner or owner["username"] != user:
                raise ValueError("会话不存在")
            await cur.execute("SELECT request_id FROM prompt_request_samples WHERE thread_id=%s AND status='admitted' LIMIT 1", (thread,))
            if await cur.fetchone():
                raise ReleaseConflict("当前会话已有请求执行中，请等待完成")
            legacy = legacy or owner["created_at"] < datetime.fromisoformat(state["initializedAt"])
            await cur.execute("SELECT * FROM thread_prompt_bindings WHERE thread_id=%s", (thread,))
            row = await cur.fetchone()
            binding = decode(row["binding_json"]) if row else None
            version = row["version"] if row else None
            if binding and binding.get("pending") and not resume:
                raise ReleaseConflict("当前会话有待处理的确认事项，请先完成审批或补充信息")
            if route == "direct":
                snapshot, cohort = None, "direct"
            else:
                if resume and not row:
                    # Only approvals originating before activation can legitimately lack a binding.
                    version = "builtin-v1"
                elif not version or (version in state["revoked"] and not resume):
                    eligible = (not legacy and not row and state.get("canary") and
                                (not state["allowlist"] or user.lower() in state["allowlist"]) and
                                bucket(user, state["salt"]) < state["percentage"] * 100)
                    version = state["canary"] if eligible else state["stable"]
                snapshot = await self.get_version(version, cur=cur)
                cohort = "canary" if version == state.get("canary") else "stable" if version == state["stable"] else "historical"
                if legacy:
                    cohort = "legacy"
                binding = {**(binding or {}), "version": version, "pending": bool(resume),
                           "assignmentRelease": (binding or {}).get("assignmentRelease", state["releaseId"]),
                           "assignmentCohort": (binding or {}).get("assignmentCohort", cohort), "revision": state["revision"]}
                if row and row["version"] != version:
                    await self.audit(user, "BINDING_MIGRATE", "SUCCESS", {"threadId": thread, "before": row["version"], "after": version}, cur=cur)
                await cur.execute("INSERT INTO thread_prompt_bindings(thread_id,username,version,binding_json) VALUES(%s,%s,%s,%s) ON DUPLICATE KEY UPDATE version=VALUES(version),binding_json=VALUES(binding_json),updated_at=UTC_TIMESTAMP(6)",
                                  (thread, user, version, canonical(binding)))
            sample = {"assignment": binding, "timingScope": "stream_excluding_input_guard", "usageState": "unavailable"}
            await cur.execute("INSERT INTO prompt_request_samples(request_id,thread_id,version,release_id,revision,cohort,route,status,sample_json) VALUES(%s,%s,%s,%s,%s,%s,%s,'admitted',%s)",
                              (request_id, thread, snapshot.version if snapshot else None, state["releaseId"], state["revision"], cohort, route, canonical(sample)))
        return {"requestId": request_id, "snapshot": snapshot, "releaseId": state["releaseId"], "revision": state["revision"], "cohort": cohort}

    async def finalize(self, admission, status, data):
        async with self.storage.cursor(transaction=True) as cur:
            await cur.execute("SELECT thread_id,sample_json,route FROM prompt_request_samples WHERE request_id=%s AND status='admitted' FOR UPDATE", (admission["requestId"],))
            row = await cur.fetchone()
            if not row:
                return
            await cur.execute("UPDATE prompt_request_samples SET status=%s,route=%s,sample_json=%s,finished_at=UTC_TIMESTAMP(6) WHERE request_id=%s",
                              (status, data.get("route", row["route"]), canonical({**decode(row["sample_json"]), **data}), admission["requestId"]))
            if row["route"] != "direct":
                await cur.execute("UPDATE thread_prompt_bindings SET binding_json=JSON_SET(binding_json,'$.pending',CAST(%s AS JSON)) WHERE thread_id=%s",
                                  ("true" if status == "interrupted" else "false", row["thread_id"]))

    async def binding(self, thread):
        async with self.storage.cursor() as cur:
            await cur.execute("SELECT version,binding_json FROM thread_prompt_bindings WHERE thread_id=%s", (thread,))
            row = await cur.fetchone()
            return {"version": row["version"], **decode(row["binding_json"])} if row else None

    async def observations(self, hours=24, *, release_id=None):
        from .observation import summarize
        if not 1 <= hours <= 720:
            raise ValueError("明细仅保留最近 30 天，超出窗口的样本已过期")
        state = await self.state()
        release_id = release_id or state["releaseId"]
        since = now() - timedelta(hours=hours)
        if state.get("activatedAt") and release_id == state["releaseId"]:
            since = max(since, datetime.fromisoformat(state["activatedAt"]))
        async with self.storage.cursor() as cur:
            await cur.execute("SELECT * FROM prompt_request_samples WHERE release_id <=> %s AND created_at>=%s ORDER BY created_at", (release_id, since))
            rows = [{**r, **decode(r["sample_json"])} for r in await cur.fetchall()]
            await cur.execute("SELECT version,COUNT(*) AS count FROM thread_prompt_bindings WHERE JSON_EXTRACT(binding_json,'$.pending')=true GROUP BY version")
            pending = list(await cur.fetchall())
        return {"releaseId": release_id, "hours": hours, "since": since, "until": now(),
                "cohorts": {arm: summarize([r for r in rows if r["cohort"] == arm]) for arm in ("stable", "canary", "historical", "direct", "legacy")},
                "versions": {version: summarize([r for r in rows if r["version"] == version]) for version in sorted({r["version"] for r in rows if r["version"]})},
                "pendingApprovals": pending, "retentionDays": 30, "timingScope": "stream_excluding_input_guard"}

    async def cleanup(self):
        async with self.storage.cursor(transaction=True) as cur:
            await cur.execute("UPDATE prompt_request_samples SET status='abandoned',finished_at=UTC_TIMESTAMP(6) WHERE status='admitted' AND created_at<%s", (now() - timedelta(hours=6),))
            await cur.execute("DELETE FROM prompt_request_samples WHERE finished_at<%s LIMIT 1000", (now() - timedelta(days=30),))
            return cur.rowcount
