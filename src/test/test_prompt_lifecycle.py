"""Synthetic evidence is restricted to disposable MySQL databases in this module."""
import asyncio
from copy import deepcopy
from datetime import timedelta
from uuid import uuid4

import pytest
from test_prompt_repository_mysql import prompt_db
from agent.evaluation.repository import EvaluationRepository, EvaluationConflictError
from agent.prompt_releases.bundles import builtin_bundle, bundle_diff, canonical, deployment_fingerprint, digest
from agent.prompt_releases.candidate_suite import candidate_suite
from agent.prompt_releases.gates import evaluate_pair
from agent.prompt_releases.repository import PromptRepository, ReleaseConflict, bucket, now
from agent.prompt_releases.service import PromptReleaseService


def synthetic_case(scenario, repetition):
    return {"scenarioId": scenario.id, "repetition": repetition, "status": "PASSED", "durationMs": 1000,
            "result": {"rolesExecuted": [scenario.fixture["role"]]},
            "assertions": [{"type": a.type, "expected": a.expected, "actual": a.expected, "status": "PASSED"} for a in scenario.assertions]}


async def complete_synthetic_pair(storage, pair):
    # This helper must NEVER target agent_db or the application runtime.
    async with storage.cursor() as cur:
        await cur.execute("SELECT DATABASE() AS name")
        assert (await cur.fetchone())["name"].startswith("prompt_test_")
    evaluations = EvaluationRepository(storage)
    for run_id in pair.values():
        token = uuid4().hex
        async with storage.cursor(transaction=True) as cur:
            await cur.execute("UPDATE evaluation_runs SET status='RUNNING',lease_owner=%s,lease_expires_at=%s WHERE run_id=%s", (token, now() + timedelta(seconds=120), run_id))
        run = await evaluations.get_run(run_id)
        bundle = await PromptRepository(storage).get_version(run["manifest"]["promptVersion"])
        for scenario in candidate_suite("alice").scenarios:
            for repetition in range(1, 4):
                await evaluations.persist_candidate(run_id, token, case=synthetic_case(scenario, repetition))
        report = await evaluations.get_report(run_id)
        await evaluations.persist_candidate(run_id, token, status="COMPLETED", report={
            "workerConfirmed": True, "provenance": "real-provider", "loadedPromptFingerprint": bundle.fingerprint,
            "loadedPromptVersion": bundle.version, "productionMutations": 0, "evidenceFingerprint": digest(report["cases"]),
            "contractEvidence": {k: {"cases": [{"status": "PASSED"}]} for k in ("rule", "safety")}})


@pytest.mark.asyncio
async def test_full_lifecycle_and_fenced_frozen_evidence(prompt_db):
    service = PromptReleaseService(prompt_db)
    await service.initialize("alice")
    v2 = await service.create_version({**builtin_bundle().roles, "lightweight": builtin_bundle().roles["lightweight"] + "\n优先给出结论。"}, "alice", "测试版本")
    assert bundle_diff(builtin_bundle(), v2)["lightweight"]
    assert not bundle_diff(builtin_bundle(), v2)["main"]
    pair = await service.pair(v2.version, "alice")
    rejected = await service.gate(pair["stableRunId"], pair["candidateRunId"], "alice")
    assert not rejected["passed"]
    await complete_synthetic_pair(prompt_db, pair)
    evaluations = EvaluationRepository(prompt_db)
    with pytest.raises(EvaluationConflictError):
        await evaluations.persist_candidate(pair["candidateRunId"], "stale", status="FAILED")
    gate = await service.gate(pair["stableRunId"], pair["candidateRunId"], "alice")
    assert gate["passed"], gate["reasons"]
    state = await service.state()
    salt = state["salt"]
    chosen = {True: [], False: []}
    for i in range(1000):
        name = f"user{i}"
        chosen[bucket(name, salt) < 1000].append(name)
    async with prompt_db.cursor(transaction=True) as cur:
        for name in chosen[True][:22] + chosen[False][:22]:
            await cur.execute("INSERT INTO app_users VALUES(%s,'test',UTC_TIMESTAMP(6))", (name,))
    canary = await service.transition("canary", "alice", expected_revision=1, idempotency_key="start", gateId=gate["gateId"])
    assert canary["percentage"] == 10 and canary["salt"] == salt
    assert await service.transition("canary", "alice", expected_revision=1, idempotency_key="start", gateId=gate["gateId"]) == canary
    with pytest.raises(ReleaseConflict, match="幂等"):
        await service.transition("canary", "alice", expected_revision=1, idempotency_key="start", gateId="other")
    with pytest.raises(ReleaseConflict, match="20"):
        await service.transition("promote", "alice", expected_revision=2, idempotency_key="early")
    for expected, names in chosen.items():
        for index, user in enumerate(names[:20]):
            thread = str(uuid4())
            await prompt_db.register_thread(thread, user)
            admitted = await service.admit(thread, user, "lightweight")
            assert admitted["cohort"] == ("canary" if expected else "stable")
            await service.finalize(admitted, "success", {"durationSeconds": 1})
    pending_thread, pending_user = str(uuid4()), chosen[True][20]
    await prompt_db.register_thread(pending_thread, pending_user)
    pending = await service.admit(pending_thread, pending_user, "sandbox")
    await service.finalize(pending, "interrupted", {})
    promoted = await service.transition("promote", "alice", expected_revision=2, idempotency_key="promote")
    assert promoted["stable"] == v2.version
    races = await asyncio.gather(*[service.transition("rollback", "alice", expected_revision=3, idempotency_key=f"rollback{i}") for i in range(2)], return_exceptions=True)
    assert sum(isinstance(r, ReleaseConflict) for r in races) == 1
    state = await service.state()
    assert state["stable"] == "builtin-v1" and v2.version in state["revoked"]
    with pytest.raises(ReleaseConflict):
        await service.admit(pending_thread, pending_user, "sandbox")
    resume = await service.admit(pending_thread, pending_user, "sandbox", resume=True)
    assert resume["snapshot"].version == v2.version
    await service.finalize(resume, "success", {})
    migrated = await service.admit(pending_thread, pending_user, "lightweight")
    assert migrated["snapshot"].version == "builtin-v1"


@pytest.mark.asyncio
async def test_expired_claim_cannot_write_or_renew(prompt_db):
    service = PromptReleaseService(prompt_db)
    await service.initialize("alice")
    version = await service.create_version({**builtin_bundle().roles, "main": "测试提示词"}, "alice", "变更")
    pair = await service.pair(version.version, "alice")
    repo = EvaluationRepository(prompt_db)
    run = await repo.claim_next_run(worker_id="old-claim", lease_seconds=120)
    async with prompt_db.cursor(transaction=True) as cur:
        await cur.execute("UPDATE evaluation_runs SET lease_expires_at=%s WHERE run_id=%s", (now()-timedelta(seconds=1), run))
    assert not await repo.renew_lease(run, worker_id="old-claim")
    with pytest.raises(EvaluationConflictError):
        await repo.persist_candidate(run, "old-claim", case=synthetic_case(candidate_suite("alice").scenarios[0], 1))
    assert run in await repo.recover_expired_leases()
    await repo.claim_next_run(worker_id="new-claim", lease_seconds=120)
    with pytest.raises(EvaluationConflictError):
        await repo.persist_candidate(run, "old-claim", status="COMPLETED", report={"passed": True})


@pytest.mark.asyncio
async def test_expiry_tampering_deployment_and_incomplete_samples_fail_closed(prompt_db, monkeypatch):
    service = PromptReleaseService(prompt_db)
    await service.initialize("alice")
    version = await service.create_version({**builtin_bundle().roles, "main": "测试提示词"}, "alice", "变更")
    pair = await service.pair(version.version, "alice")
    await complete_synthetic_pair(prompt_db, pair)
    gate = await service.gate(*pair.values(), "alice")
    async with prompt_db.cursor(transaction=True) as cur:
        await cur.execute("UPDATE prompt_release_gates SET expires_at=%s WHERE gate_id=%s", (now()-timedelta(seconds=1), gate["gateId"]))
    with pytest.raises(ReleaseConflict, match="过期"):
        await service.transition("canary", "alice", expected_revision=1, idempotency_key="expired", gateId=gate["gateId"])
    report = await EvaluationRepository(prompt_db).get_report(pair["candidateRunId"])
    stable = await EvaluationRepository(prompt_db).get_report(pair["stableRunId"])
    tolerated = deepcopy(report)
    case = tolerated['cases'][0]
    case['status'] = 'FAILED'
    for assertion in case['assertions']:
        if assertion['type'] == 'business_contract':
            assertion.update(actual=False, status='FAILED')
    tolerated['aggregate']['evidenceFingerprint'] = digest(tolerated['cases'])
    tolerance = evaluate_pair(stable, tolerated, builtin_bundle(), version, deployment_fingerprint())
    assert not any('必需断言' in reason for reason in tolerance['reasons'])
    assert any('下降超过 2' in reason for reason in tolerance['reasons'])
    baseline_bundle = builtin_bundle()
    for mutate in (lambda r:r["aggregate"].update(provenance="fake"), lambda r:r["cases"].pop(),
                   lambda r:r["manifest"].update(modelId="other"), lambda r:r["aggregate"].update(productionMutations=1)):
        bad = deepcopy(report); mutate(bad)
        assert not evaluate_pair(stable, bad, baseline_bundle, version, deployment_fingerprint())["passed"]
    with pytest.raises(ValueError, match="过期"):
        await service.observations(721)


@pytest.mark.asyncio
async def test_only_one_candidate_worker_claim_and_frozen_legacy_mutations(prompt_db):
    service = PromptReleaseService(prompt_db)
    await service.initialize("alice")
    version = await service.create_version({**builtin_bundle().roles, "main": "测试并发。"}, "alice", "并发")
    pair = await service.pair(version.version, "alice")
    repo = EvaluationRepository(prompt_db)
    claims = await asyncio.gather(*[repo.claim_next_run(worker_id=f"claim-{i}", lease_seconds=120) for i in range(2)])
    assert sum(c is not None for c in claims) == 1
    active = next(c for c in claims if c)
    with pytest.raises(EvaluationConflictError):
        await repo.finish_run(active, status="COMPLETED", result={"passed": True})
    with pytest.raises(EvaluationConflictError):
        await repo.write_case(run_id=active, scenario_id="light-chat", repetition=1, status="PASSED", input_data={}, output={})
    claim = (await repo.get_run(active))["lease_owner"]
    assert await repo.renew_lease(active, worker_id=claim, lease_seconds=120)
    await repo.persist_candidate(active, claim, status="FAILED", report={"reason": "test"})
    second = next(i for i in pair.values() if i != active)
    assert await repo.request_cancel(second)
    cancelled = await repo.get_run(second)
    assert cancelled['status'] == 'CANCELLED' and cancelled['finished_at'] is not None
    assert await repo.claim_next_run(worker_id="second") is None


@pytest.mark.asyncio
async def test_retention_abandoned_and_audit_survive(prompt_db):
    repo = PromptRepository(prompt_db)
    await repo.initialize("alice")
    admissions = []
    for label in ("old", "active", "abandoned", "recent"):
        await prompt_db.register_thread(label, "alice")
        admissions.append(await repo.admit(label, "alice", "lightweight"))
    await repo.finalize(admissions[0], "failure", {})
    await repo.finalize(admissions[3], "success", {})
    async with prompt_db.cursor(transaction=True) as cur:
        await cur.execute("UPDATE prompt_request_samples SET finished_at=%s WHERE request_id=%s", (now()-timedelta(days=31), admissions[0]["requestId"]))
        await cur.execute("UPDATE prompt_request_samples SET created_at=%s WHERE request_id=%s", (now()-timedelta(hours=7), admissions[2]["requestId"]))
    assert await repo.cleanup() == 1
    async with prompt_db.cursor() as cur:
        await cur.execute("SELECT thread_id,status FROM prompt_request_samples")
        statuses = {r["thread_id"]: r["status"] for r in await cur.fetchall()}
        await cur.execute("SELECT COUNT(*) AS n FROM prompt_management_audit")
        assert (await cur.fetchone())["n"] > 0
    assert statuses == {"active": "admitted", "abandoned": "abandoned", "recent": "success"}
    assert await repo.cleanup() == 0
