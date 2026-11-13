"""Server-owned paired evaluation and transactional rollout operations."""
from datetime import timedelta
from uuid import uuid4

from agent.evaluation.manifest import EvaluationRunManifest
from agent.evaluation.repository import EvaluationRepository, EvaluationConflictError
from agent.evaluation.schemas import publish_suite, EvaluationSuite
from .bundles import canonical, digest, deployment_fingerprint, model_config
from .candidate_suite import candidate_suite, suite_signature
from .gates import evaluate_pair
from .repository import PromptRepository, ReleaseConflict, decode, now


class PromptReleaseService(PromptRepository):
    async def pair(self, candidate_version, actor):
        state = await self.state()
        stable, candidate = await self.get_version(state["stable"]), await self.get_version(candidate_version)
        if stable.version == candidate.version:
            raise ValueError("候选版本必须与稳定版本不同")
        evaluations = EvaluationRepository(self.storage)
        suite = candidate_suite(actor)
        published = publish_suite(suite, version=1)
        try:
            await evaluations.publish(published)
        except EvaluationConflictError:
            stored = await evaluations.get_suite(suite.suiteId, 1)
            if suite_signature(EvaluationSuite.model_validate(stored["suite"])) != suite_signature(suite):
                raise ReleaseConflict("固定套件内容改变，必须升级套件版本")
            suite = EvaluationSuite.model_validate(stored["suite"])
            published = publish_suite(suite, version=1)
        from agent.evaluation.rule_runner import load_default_inputs
        from agent.policies.procurement import POLICY_VERSION
        _, fixtures = load_default_inputs()
        deployment = deployment_fingerprint()
        common = dict(suiteId=suite.suiteId, suiteVersion=1, suiteFingerprint=published.contentFingerprint,
            fixtureVersion=suite.fixtureVersion, fixtureFingerprint=digest(fixtures),
            policyVersion=POLICY_VERSION, policyFingerprint=digest(POLICY_VERSION),
            codeRevision=deployment, skillFingerprint=deployment, runnerVersion="prompt-runner-v1",
            modelId=model_config()["model"], providerId="deepseek", modelConfigFingerprint=digest(model_config()),
            environment="isolated-test", executionMode="prompt-candidate", deploymentFingerprint=deployment,
            startedAt=now(), repetitionCount=3)
        pair = {}
        async with self.storage.cursor(transaction=True) as cur:
            for arm, bundle in (("stable", stable), ("candidate", candidate)):
                manifest = EvaluationRunManifest(**common, promptVersion=bundle.version, promptFingerprint=bundle.fingerprint)
                run_id = str(uuid4())
                await cur.execute("INSERT INTO evaluation_runs(run_id,suite_id,suite_version,requested_by,execution_mode,status,manifest_json,created_at) VALUES(%s,%s,1,%s,'prompt-candidate','QUEUED',%s,UTC_TIMESTAMP(6))",
                    (run_id, suite.suiteId, actor, canonical({**manifest.model_dump(mode="json"), "manifestFingerprint": manifest.fingerprint()})))
                pair[arm + "RunId"] = run_id
            await self.audit(actor, "PAIR_CREATE", "SUCCESS", pair, cur=cur)
        return pair

    async def gate(self, stable_run, candidate_run, actor):
        evaluations = EvaluationRepository(self.storage)
        reports = [await evaluations.get_report(i) for i in (stable_run, candidate_run)]
        if any(r is None for r in reports):
            raise ValueError("评测运行不存在")
        state = await self.state()
        stable = await self.get_version(state["stable"])
        candidate = await self.get_version(reports[1]["manifest"].get("promptVersion"))
        evidence = evaluate_pair(*reports, stable, candidate, deployment_fingerprint())
        evidence["reportFingerprints"] = [digest(r) for r in reports]
        gate_id, expires = str(uuid4()), now() + timedelta(hours=24)
        async with self.storage.cursor(transaction=True) as cur:
            await cur.execute("INSERT INTO prompt_release_gates(gate_id,candidate_version,stable_version,passed,evidence_json,expires_at) VALUES(%s,%s,%s,%s,%s,%s)",
                (gate_id, candidate.version, stable.version, evidence["passed"], canonical(evidence), expires))
            await self.audit(actor, "GATE_CREATE", "SUCCESS", {"gateId": gate_id, "passed": evidence["passed"]}, cur=cur)
        return {"gateId": gate_id, "expiresAt": expires, "stableVersion": stable.version, "candidateVersion": candidate.version, **evidence}

    async def validate_gate(self, gate_id, state, *, cur):
        await cur.execute("SELECT * FROM prompt_release_gates WHERE gate_id=%s", (gate_id,))
        gate = await cur.fetchone()
        if not gate or not gate["passed"] or gate["expires_at"] <= now() or gate["stable_version"] != state["stable"]:
            raise ReleaseConflict("门禁未通过、已过期或稳定版本已改变，请重新评测")
        evidence = decode(gate["evidence_json"])
        if evidence["deploymentFingerprint"] != deployment_fingerprint():
            raise ReleaseConflict("部署已改变，请重新评测")
        reports = [await EvaluationRepository(self.storage).get_report(evidence[k]) for k in ("stableRunId", "candidateRunId")]
        if [digest(r) for r in reports] != evidence["reportFingerprints"]:
            raise ReleaseConflict("评测证据已改变")
        recheck = evaluate_pair(*reports, await self.get_version(state["stable"], cur=cur),
                               await self.get_version(gate["candidate_version"], cur=cur), deployment_fingerprint())
        if not recheck["passed"]:
            raise ReleaseConflict("离线门禁重验失败")
        return gate

    async def transition(self, action, actor, *, expected_revision, idempotency_key, **parameters):
        if action not in {"canary", "promote", "rollback"}:
            raise ValueError("不支持的发布操作")
        if not idempotency_key or len(idempotency_key) > 128:
            raise ValueError("需要有效幂等键")
        request_hash = digest([actor, action, expected_revision, parameters])
        async with self.storage.cursor(transaction=True) as cur:
            state = await self.state(cur=cur, lock=True)
            await cur.execute("SELECT request_fingerprint,event_json FROM prompt_release_events WHERE environment=%s AND idempotency_key=%s", (self.environment, idempotency_key))
            prior = await cur.fetchone()
            if prior:
                if prior["request_fingerprint"] != request_hash:
                    raise ReleaseConflict("幂等键已用于其他请求")
                return decode(prior["event_json"])["after"]
            if state["revision"] != expected_revision:
                raise ReleaseConflict("发布状态已更新，请刷新后重试")
            before = dict(state)
            if action == "canary":
                percentage = parameters.get("percentage", 10)
                if not 1 <= percentage <= 25:
                    raise ValueError("试用比例必须在 1–25%")
                gate = await self.validate_gate(parameters.get("gateId"), state, cur=cur)
                candidate = gate["candidate_version"]
                if candidate in state["revoked"]:
                    raise ReleaseConflict("已撤销版本不能重新试用，请创建新版本")
                if state.get("canary") and state["canary"] != candidate:
                    raise ReleaseConflict("已有试用版本，请先回滚")
                if not state.get("canary"):
                    state.update(releaseId=str(uuid4()), activatedAt=now().isoformat(), gateId=gate["gate_id"])
                state.update(canary=candidate, percentage=percentage,
                    allowlist=sorted({u.lower() for u in parameters.get("allowlist", [])}), status="CANARY")
            elif action == "promote":
                if not state.get("canary"):
                    raise ReleaseConflict("当前没有试用版本")
                await self.validate_gate(state["gateId"], state, cur=cur)
                observations = await self.observations(release_id=state["releaseId"])
                if any(not observations["cohorts"][a]["sufficient"] for a in ("stable", "canary")):
                    raise ReleaseConflict("过去 24 小时稳定版和试用版各需至少 20 个完整有效请求")
                state.update(previousStable=state["stable"], stable=state["canary"], canary=None, percentage=0, status="PROMOTED")
            else:
                revoked = state.get("canary") or (state["stable"] if state.get("previousStable") else None)
                if not revoked:
                    raise ReleaseConflict("当前没有可回滚版本")
                if not state.get("canary"):
                    state["stable"] = state["previousStable"]
                    state["previousStable"] = None
                state.update(canary=None, percentage=0, status="ROLLED_BACK", revoked=sorted(set(state["revoked"] + [revoked])))
            state["revision"] += 1
            event = {"before": before, "after": state, "parameters": parameters}
            await cur.execute("UPDATE prompt_release_state SET revision=%s,state_json=%s,updated_at=UTC_TIMESTAMP(6) WHERE environment=%s",
                (state["revision"], canonical(state), self.environment))
            await cur.execute("INSERT INTO prompt_release_events(event_id,environment,revision,action,actor,idempotency_key,request_fingerprint,event_json) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
                (str(uuid4()), self.environment, state["revision"], action, actor, idempotency_key, request_hash, canonical(event)))
            await self.audit(actor, action.upper(), "SUCCESS", {"revision": state["revision"], **parameters}, cur=cur)
            return state
