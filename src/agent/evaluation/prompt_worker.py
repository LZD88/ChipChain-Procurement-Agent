"""Only server-constructed manifests may invoke the real provider."""
import asyncio
import os
from agent.prompt_releases.bundles import deployment_fingerprint, digest, model_config
from agent.prompt_releases.candidate_suite import candidate_suite
from agent.prompt_releases.repository import PromptRepository
from agent.evaluation.schemas import EvaluationSuite
from agent.evaluation.repository import EvaluationConflictError
from agent.evaluation.prompt_candidate_runner import run_prompt_candidate
from agent.evaluation.workflow_runner import assert_isolated_environment


async def execute_candidate(repository, run, *, lease_seconds=120, environment=None):
    environment = os.environ if environment is None else environment
    token, run_id = run.get("lease_owner"), run["run_id"]
    if not token:
        raise ValueError("candidate evaluation requires an active worker claim")
    async def finish(status, report):
        try:
            return await repository.persist_candidate(run_id, token, status=status, report=report)
        except EvaluationConflictError:
            # Cancellation may already have frozen the run during case persistence;
            # a lost claim must not stop this worker from serving subsequent jobs.
            return False
    async def execute():
        assert_isolated_environment(environment)
        manifest = run["manifest"]
        experiment = run["execution_mode"] == "experiment"
        if manifest.get("deploymentFingerprint") != deployment_fingerprint():
            raise ValueError("deployment changed since evaluation was queued")
        bundle = await PromptRepository(repository.storage).get_version(manifest["promptVersion"])
        if bundle.fingerprint != manifest["promptFingerprint"]:
            raise ValueError("prompt fingerprint mismatch")
        suite_row = await repository.get_suite(run["suite_id"], run["suite_version"])
        suite = EvaluationSuite.model_validate(suite_row["suite"])
        fixed = candidate_suite(suite.owner)
        if manifest["suiteFingerprint"] != suite.content_fingerprint():
            raise ValueError("suite fingerprint mismatch")
        if not experiment and suite.content_fingerprint() != fixed.content_fingerprint():
            raise ValueError("candidate suites must match server-owned fixtures")
        configuration = model_config()
        if experiment:
            from agent.evaluation.workbench import Variant, configuration_for
            configuration = configuration_for(Variant(modelId=manifest["modelId"]))
            from agent.evaluation.rule_runner import load_default_inputs
            if manifest["fixtureFingerprint"] != digest(load_default_inputs()[1]):
                raise ValueError("fixtures changed since evaluation was queued")
        if manifest["modelConfigFingerprint"] != digest(configuration):
            raise ValueError("provider configuration changed")
        from langchain_openai import ChatOpenAI
        from agent.platform.gateway import model_endpoint
        endpoint = model_endpoint(environment, purpose="evaluation")
        key = endpoint.api_key
        if not key:
            raise ValueError("server provider key unavailable")
        if configuration["baseUrl"] != endpoint.base_url:
            raise ValueError("worker model transport differs from queued configuration")
        model = ChatOpenAI(model=configuration["model"], api_key=key,
            base_url=configuration["baseUrl"], temperature=configuration["temperature"], max_tokens=configuration["maxTokens"],
            timeout=45, max_retries=0, extra_body={"thinking": {"type": "disabled"}})
        async def persist(case):
            if not await repository.persist_candidate(run_id, token, case=case):
                raise asyncio.CancelledError()
        report = await run_prompt_candidate(bundle, suite, model, environment=environment,
                                            provenance="real-provider", on_case=persist,
                                            skill_variant=manifest.get("skillVariant", "current") if experiment else "current")
        report["executionMode"] = run["execution_mode"]
        persisted = await repository.get_report(run_id)
        from agent.evaluation.rule_runner import run_rule_contract
        from agent.evaluation.workflow_runner import run_workflow_safety
        rules = run_rule_contract() if not experiment else None
        safety = await run_workflow_safety(environment=environment) if not experiment else None
        report.update(workerConfirmed=True, evidenceFingerprint=digest(persisted["cases"]),
                      contractEvidence={"rule": rules, "safety": safety},
                      budgetVersion="prompt-budget-v1", caseTimeoutSeconds=60,
                      maxModelCalls=8, maxToolCalls=12, maxOutputTokens=4000,
                      maxRunTokens=200000, maxRunSeconds=1800)
        from agent.evaluation.metrics import calculate_metrics
        report["metrics"] = calculate_metrics(report, input_cost_per_1k=manifest.get("inputPricePer1k"),
            output_cost_per_1k=manifest.get("outputPricePer1k"))["metrics"]
        for metric in report["metrics"]:
            if metric["name"] == "estimated_model_cost" and manifest.get("priceCurrency"):
                metric["unit"] = manifest["priceCurrency"]
        report.pop("cases", None)
        return await repository.persist_candidate(run_id, token, status="COMPLETED", report=report)
    async def heartbeat():
        try:
            while True:
                await asyncio.sleep(min(2 if run["execution_mode"] == "experiment" else 30, lease_seconds / 3))
                if not await repository.renew_lease(run_id, worker_id=token, lease_seconds=lease_seconds):
                    task.cancel()
                    return
        except Exception:
            task.cancel()
    task = asyncio.create_task(execute())
    pulse = asyncio.create_task(heartbeat())
    try:
        return await task
    except asyncio.CancelledError:
        return await finish("CANCELLED", {"incomplete": True, "reason": "cancelled_or_claim_lost"})
    except Exception as exc:
        from agent.evaluation.prompt_candidate_runner import EvaluationBudgetExceeded
        from agent.evaluation.metrics import calculate_metrics
        partial = await repository.get_report(run_id)
        return await finish("FAILED", {
            "incomplete": True, "reason": "budget_exceeded" if isinstance(exc, EvaluationBudgetExceeded) else "timeout" if isinstance(exc, TimeoutError) else "candidate_runner_failure",
            "errorType": type(exc).__name__, "metrics": calculate_metrics(partial)["metrics"]})
    finally:
        pulse.cancel()
        await asyncio.gather(pulse, return_exceptions=True)
