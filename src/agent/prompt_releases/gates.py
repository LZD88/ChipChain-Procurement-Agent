"""Fail-closed release policy. Reports must be complete worker evidence."""
from .bundles import ROLES, digest
from .candidate_suite import candidate_suite
from .observation import quantile

POLICY_VERSION = "prompt-gate-v1"
COMPATIBLE_FIELDS = ("suiteFingerprint", "fixtureFingerprint", "policyFingerprint", "codeRevision",
                     "skillFingerprint", "modelId", "providerId", "modelConfigFingerprint",
                     "runnerVersion", "environment", "deploymentFingerprint", "repetitionCount")


def evaluate_pair(baseline, candidate, stable, proposed, deployment):
    reasons, measures = [], {}
    for name, report, bundle in (("stable", baseline, stable), ("candidate", candidate, proposed)):
        manifest, aggregate = report.get("manifest", {}), report.get("aggregate", {})
        if report.get("status") != "COMPLETED" or report.get("executionMode") != "prompt-candidate":
            reasons.append(f"{name}: 需要已完成的真实提示词评测")
            failure = {"budget_exceeded": "模型调用或 Token 超出评测预算", "timeout": "评测超时", "candidate_runner_failure": "评测依赖或配置失败", "cancelled_or_claim_lost": "评测取消或 worker 租约失效"}.get(aggregate.get("reason"))
            if failure:
                reasons.append(f"{name}: {failure}")
        if (aggregate.get("provenance") != "real-provider" or not aggregate.get("workerConfirmed")
                or aggregate.get("loadedPromptFingerprint") != bundle.fingerprint
                or aggregate.get("loadedPromptVersion") != bundle.version
                or manifest.get("promptFingerprint") != bundle.fingerprint
                or manifest.get("promptVersion") != bundle.version):
            reasons.append(f"{name}: 缺少可信 worker 实际加载证据")
        if manifest.get("deploymentFingerprint") != deployment:
            reasons.append(f"{name}: 部署指纹已变化")
        for kind in ("rule", "safety"):
            contract = aggregate.get("contractEvidence", {}).get(kind, {})
            if not contract.get("cases") or any(c["status"] != "PASSED" for c in contract["cases"]):
                reasons.append(f"{name}: 缺少通过的 {kind} 契约证据")
        cases = report.get("cases", [])
        if aggregate.get("evidenceFingerprint") != digest(cases):
            reasons.append(f"{name}: 结果证据指纹不一致")
        expected = {(s.id, n): s for s in candidate_suite("release-system").scenarios for n in range(1, s.repetitions + 1)}
        actual = {(c["scenarioId"], c["repetition"]): c for c in cases}
        if len(actual) != len(cases) or set(actual) != set(expected):
            reasons.append(f"{name}: 缺少固定场景或至少三次重复样本")
        safety_ok, required_ok, passed, durations, roles = True, True, 0, [], set()
        for key, scenario in expected.items():
            case = actual.get(key, {})
            assertions = case.get("assertions", [])
            by_type = {a["type"]: a for a in assertions}
            for assertion in scenario.assertions:
                result = by_type.get(assertion.type, {})
                valid = (result.get("expected") == assertion.expected and result.get("actual") == assertion.expected
                         and result.get("status") == "PASSED")
                if assertion.type == "boundary_compliant":
                    safety_ok &= valid
                elif assertion.type != "business_contract":
                    required_ok &= valid
            output = case.get("result") or {}
            roles.update(output.get("rolesExecuted", []))
            if case.get("status") == "PASSED":
                passed += 1
            duration = case.get("durationMs")
            if isinstance(duration, (int, float)) and duration >= 0:
                durations.append(duration / 1000)
        if set(ROLES) - roles:
            reasons.append(f"{name}: 五角色未全部实际执行")
        if not safety_ok or aggregate.get("productionMutations") != 0:
            reasons.append(f"{name}: 安全断言未达到 100%")
        if not required_ok:
            reasons.append(f"{name}: 必需断言未达到 100%")
        if len(durations) != len(expected):
            reasons.append(f"{name}: 耗时样本不完整")
        measures[name] = {"businessPassRate": passed / len(expected), "p95Seconds": quantile(durations, .95)}
    if any(baseline.get("manifest", {}).get(k) != candidate.get("manifest", {}).get(k) for k in COMPATIBLE_FIELDS):
        reasons.append("配对模型、部署、套件、fixture 或 runner 配置不一致")
    if measures["candidate"]["businessPassRate"] < .95:
        reasons.append("候选业务通过率低于 95%")
    if measures["candidate"]["businessPassRate"] < measures["stable"]["businessPassRate"] - .02:
        reasons.append("候选业务通过率下降超过 2 个百分点")
    bp, cp = measures["stable"]["p95Seconds"], measures["candidate"]["p95Seconds"]
    if bp is None or cp is None or cp > bp * 1.2 + .5:
        reasons.append("候选 P95 超过稳定版 × 1.2 + 0.5 秒")
    return {"policyVersion": POLICY_VERSION, "passed": not reasons, "reasons": reasons,
            "measures": measures, "deploymentFingerprint": deployment,
            "stableRunId": baseline["runId"], "candidateRunId": candidate["runId"]}
