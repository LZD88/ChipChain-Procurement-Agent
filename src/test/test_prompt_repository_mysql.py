import asyncio
from datetime import timedelta
from uuid import uuid4

import aiomysql
import pytest
import pytest_asyncio

from agent.mysql_storage import APPLICATION_DDL, MySQLSettings, MySQLStorage
from agent.prompt_releases.bundles import builtin_bundle
from agent.prompt_releases.ddl import ensure_immutable_versions
from agent.prompt_releases.repository import PromptRepository, ReleaseConflict, now


@pytest_asyncio.fixture
async def prompt_db():
    base = MySQLSettings.from_env()
    database = "prompt_test_" + uuid4().hex[:12]
    admin = await aiomysql.connect(**base.connection_options())
    pool = None
    try:
        async with admin.cursor() as cur:
            await cur.execute(f"CREATE DATABASE `{database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_bin")
        pool = await aiomysql.create_pool(**base.connection_options(), db=database, minsize=2, maxsize=5)
        storage = MySQLStorage(base)
        storage.pool = pool
        async with storage.cursor() as cur:
            for ddl in APPLICATION_DDL.values():
                await cur.execute(ddl)
            await ensure_immutable_versions(cur)
            await cur.execute("INSERT INTO app_users VALUES ('alice','test',UTC_TIMESTAMP(6))")
        yield storage
    finally:
        if pool:
            pool.close()
            await pool.wait_closed()
        async with admin.cursor() as cur:
            await cur.execute(f"DROP DATABASE `{database}`")
        admin.close()
        await admin.ensure_closed()


@pytest.mark.asyncio
async def test_version_immutability_initialization_and_concurrent_creation(prompt_db):
    repo = PromptRepository(prompt_db)
    state = await repo.initialize("alice")
    assert (await repo.initialize("alice"))["revision"] == state["revision"]
    assert (await repo.get_version("builtin-v1")).roles == builtin_bundle().roles
    roles = {**builtin_bundle().roles, "main": "这是一个新版本。"}
    results = await asyncio.gather(repo.create_version(roles, "alice", "改版"), repo.create_version(roles, "alice", "改版"), return_exceptions=True)
    assert sum(isinstance(r, ReleaseConflict) for r in results) == 1
    assert len(await repo.list_versions()) == 2
    assert len(await repo.list_versions(limit=1, offset=1)) == 1
    assert await repo.list_versions(limit=1, offset=2) == []
    for sql in ("UPDATE prompt_bundle_versions SET change_note='bad' WHERE version='builtin-v1'",
                "DELETE FROM prompt_bundle_versions WHERE version='builtin-v1'"):
        with pytest.raises(aiomysql.Error, match="immutable"):
            async with prompt_db.cursor(transaction=True) as cur:
                await cur.execute(sql)


@pytest.mark.asyncio
async def test_request_attribution_approval_and_finalization(prompt_db):
    repo = PromptRepository(prompt_db)
    await repo.initialize("alice")
    for thread in ("thread-a", "thread-b"):
        await prompt_db.register_thread(thread, "alice")
    first = await repo.admit("thread-a", "alice", "lightweight")
    assert first["snapshot"].version == "builtin-v1"
    await repo.finalize(first, "interrupted", {"durationSeconds": 1})
    with pytest.raises(ReleaseConflict):
        await repo.admit("thread-a", "alice", "sandbox")
    resumed = await repo.admit("thread-a", "alice", "sandbox", resume=True)
    assert resumed["snapshot"].version == "builtin-v1"
    await repo.finalize(resumed, "success", {"durationSeconds": 2})
    await repo.finalize(resumed, "failure", {"durationSeconds": 100})
    direct = await repo.admit("thread-b", "alice", "direct")
    await repo.finalize(direct, "success", {})
    assert direct["snapshot"] is None
    obs = await repo.observations()
    assert obs["cohorts"]["stable"]["statusCounts"] == {"interrupted": 1, "success": 1}
    assert obs["cohorts"]["stable"]["p95Seconds"] == 2
    assert obs["cohorts"]["stable"]["estimatedCostUsd"] is None
    assert obs["cohorts"]["direct"]["sampleCount"] == 1
    with pytest.raises(ValueError, match="会话"):
        await repo.admit("thread-a", "other", "sandbox")


@pytest.mark.asyncio
async def test_concurrent_admission_and_legacy_assignment(prompt_db):
    repo = PromptRepository(prompt_db)
    await repo.initialize('alice')
    await prompt_db.register_thread('concurrent', 'alice')
    results = await asyncio.gather(repo.admit('concurrent', 'alice', 'sandbox'), repo.admit('concurrent', 'alice', 'lightweight'), return_exceptions=True)
    assert sum(isinstance(r, ReleaseConflict) for r in results) == 1
    await prompt_db.register_thread('legacy', 'alice')
    async with prompt_db.cursor(transaction=True) as cur:
        await cur.execute("UPDATE chat_threads SET created_at=%s WHERE thread_id='legacy'", (now()-timedelta(days=1),))
    old = await repo.admit('legacy', 'alice', 'lightweight')
    assert old['cohort'] == 'legacy' and old['snapshot'].version == 'builtin-v1'


def test_observation_denominators_routes_and_missing_usage():
    from agent.prompt_releases.observation import summarize
    rows = [dict(status=status, route=route, durationSeconds=duration, usageState='unavailable') for status, route, duration in (
        ('success', 'sandbox', 1), ('failure', 'lightweight', 9), ('interrupted', 'sandbox', 100), ('cancelled', 'sandbox', 10), ('admitted', 'direct', 0))]
    result = summarize(rows)
    assert result['completedCount'] == 2 and result['successRate'] == .5
    assert (result['p50Seconds'], result['p95Seconds']) == (1, 9)
    assert result['estimatedCostUsd'] is None and result['inputTokens'] is None
    assert result['usageExcluded'] == 2 and not result['sufficient']
    assert result['routes']['sandbox']['count'] == 3
