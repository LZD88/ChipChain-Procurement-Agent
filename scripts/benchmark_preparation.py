"""Read-only preparation benchmark against the configured live VM.

Never invokes a graph or writes ERP data. Owns only prepbench-* sandbox bindings.
Run while no other in-process loader uses sandbox_manager (separate web processes
are independent). Credentials come from .env and are never included in reports.
"""
from __future__ import annotations
import argparse
import asyncio
from dataclasses import asdict, replace
import json
import logging
import math
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from api_view.agent_loader import AgentLoader
from api_view import observability
from agent.backends import sandbox_manager, sandbox_healthcheck
from agent.runtime_config import RuntimeConfig


def snapshot():
    return {"/".join([sample.name, *[f"{k}={v}" for k, v in sorted(sample.labels.items())]]): sample.value
            for metric in (observability.RUNTIME_CACHE, observability.PREPARATION_DURATION,
                           observability.PREPARATION_WAIT, observability.PREPARATION_STAGE,
                           observability.POOL_REPLENISH)
            for family in metric.collect() for sample in family.samples
            if sample.name.endswith(("_count", "_total", "_sum"))}


def summary(samples):
    return {"count": len(samples), "seconds": samples,
            "p95_seconds": sorted(samples)[max(0, math.ceil(len(samples) * .95) - 1)]}


def vm_info():
    executable = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Oracle/VirtualBox/VBoxManage.exe"
    vm_name = os.getenv("SANDBOX_VM_NAME")
    if not executable.exists() or not vm_name:
        return {"configuration": "external sandbox; record host capacity separately"}
    result = subprocess.run([str(executable), "showvminfo", vm_name, "--machinereadable"],
                            capture_output=True, text=True, encoding="utf-8")
    values = dict(line.split("=", 1) for line in result.stdout.splitlines()
                  if line.startswith(("memory=", "cpus=", "VMState=")))
    return {"memory_mib": int(values["memory"]), "cpus": int(values["cpus"]),
            "state": values["VMState"].strip('"')}


async def run(args):
    loader = AgentLoader()
    prefix = "prepbench-" + uuid.uuid4().hex[:8]
    users = set()
    report = {"type": "live-vm-no-model", "revision": subprocess.check_output(
        ["git", "rev-parse", "HEAD"], text=True).strip(),
        "working_tree_changes": True, "config": asdict(RuntimeConfig.from_env()),
        "vm": vm_info(), "clock": str(time.get_clock_info("perf_counter")),
        "paths": {}, "errors": []}
    previous_replenish = sandbox_manager.SANDBOX_PREWARM_REPLENISH
    original_ttl = os.environ.get("SANDBOX_HEALTH_CACHE_TTL_SECONDS")
    original_target = os.environ.get("SANDBOX_PREWARM_TARGET")
    selected = set(args.paths.split(","))

    async def acquire(user):
        started = time.perf_counter()
        lease = await loader.get_agent_for_user(user)
        elapsed = time.perf_counter() - started
        await lease.release()
        return elapsed

    async def clear_graphs(user):
        runtime = loader._preparation_runtime()
        runtime.entries[:] = [entry for entry in runtime.entries if entry.user != user]

    async def clean(user):
        await clear_graphs(user)
        if user in sandbox_manager.SANDBOX_BACKENDS:
            await sandbox_manager.cleanup_user(user)

    async def measure(name, action, repetitions):
        samples = []
        outcomes = []
        sample_metrics = []
        before = snapshot()
        for i in range(repetitions):
            started = time.perf_counter()
            sample_before = snapshot()
            try:
                samples.append(await action(i))
                outcomes.append("success")
            except Exception as exc:
                samples.append(time.perf_counter() - started)
                outcomes.append(type(exc).__name__)
            print(json.dumps({"path": name, "sample": i + 1, "seconds": round(samples[-1], 4),
                              "outcome": outcomes[-1]}), flush=True)
            sample_metrics.append({
                key: value - sample_before.get(key, 0) for key, value in snapshot().items()
                if value != sample_before.get(key, 0)})
            successful = [value for value, outcome in zip(samples, outcomes) if outcome == "success"]
            report["paths"][name] = summary(samples) | {"outcomes": outcomes,
                "sample_counter_deltas": sample_metrics,
                "success_count": len(successful), "failure_count": len(samples) - len(successful),
                "success_p95_seconds": summary(successful)["p95_seconds"] if successful else None}
            report["paths"][name]["counter_delta"] = {
                key: value - before.get(key, 0) for key, value in snapshot().items()
                if value != before.get(key, 0)}
            report["paths"][name]["pool_after"] = {
                "ready": len(sandbox_manager._ready), "creating": sandbox_manager._creating}
            Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        after = snapshot()
        report["paths"][name]["counter_delta"] = {
            key: value - before.get(key, 0) for key, value in after.items() if value != before.get(key, 0)}
        Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    try:
        await loader.initialize()
        if loader._prewarm_task:
            await loader._prewarm_task
        runtime = loader._preparation_runtime()
        sandbox_manager.SANDBOX_PREWARM_REPLENISH = False
        report["initial_pool"] = {"ready": len(sandbox_manager._ready), "creating": sandbox_manager._creating}

        user = prefix + "-a"
        users.add(user)
        await acquire(user)
        if "hot" in selected:
            await measure("hot_cache", lambda _: acquire(user), 30)
        original = runtime.config
        runtime.config = replace(original, graph_enabled=False, background_enabled=False)
        os.environ["SANDBOX_HEALTH_CACHE_TTL_SECONDS"] = "0"
        await clear_graphs(user)
        if "disabled" in selected:
            await measure("cache_disabled_same_vm", lambda _: acquire(user), args.scenario_samples)
        runtime.config = original
        os.environ["SANDBOX_HEALTH_CACHE_TTL_SECONDS"] = "45"

        async def cold(_):
            await clean(user)
            # Cold creation deliberately bypasses existing ready entries.
            async with sandbox_manager._warm_lock:
                held = list(sandbox_manager._ready)
                sandbox_manager._ready.clear()
            try:
                return await acquire(user)
            finally:
                async with sandbox_manager._warm_lock:
                    sandbox_manager._ready.extend(held)
                    sandbox_manager._pool_metrics()
        if "cold" in selected:
            await measure("cold_foreground", cold, args.scenario_samples)

        async def prepared(_):
            await clear_graphs(user)
            runtime.schedule(user)
            task = runtime.pending.get(user)
            if task:
                await asyncio.shield(task)
            return await acquire(user)
        if "auth" in selected:
            await measure("auth_prepared_existing_sandbox", prepared, args.scenario_samples)

        async def recovery(_):
            if user not in sandbox_manager.SANDBOX_BACKENDS:
                await acquire(user)
            proxy = sandbox_manager.SANDBOX_BACKENDS[user]
            try:
                await sandbox_manager._delete_sandbox(proxy.id)
            except Exception as exc:
                # A failed rebuild may leave the original dead proxy. Retrying
                # recovery must reach acquisition even if it is already gone.
                if getattr(exc, "status_code", None) != 404:
                    raise
            return await acquire(user)
        if "recovery" in selected:
            await measure("sandbox_failure_recovery", recovery, args.scenario_samples)

        await clean(user)
        other = prefix + "-b"
        users.add(other)
        if "dual" in selected:
            await sandbox_manager.pre_warm()
            started = time.perf_counter()
            leases = await asyncio.gather(loader.get_agent_for_user(user), loader.get_agent_for_user(other))
            report["dual_user_pool"] = {"seconds": time.perf_counter() - started,
                "distinct_sandboxes": leases[0]._entry.proxy.id != leases[1]._entry.proxy.id,
                "ready_after": len(sandbox_manager._ready)}
            for lease in leases:
                await lease.release()

        # Exercise the rollback flags against live dependencies, not fake timings.
        if "rollback" in selected:
            runtime.config = replace(original, graph_enabled=False, background_enabled=False, pool_target=1, health_ttl=0)
            os.environ["SANDBOX_HEALTH_CACHE_TTL_SECONDS"] = "0"
            rollback = [await acquire(user), await acquire(user)]
            report["rollback"] = {"acquisition_seconds": rollback, "config": asdict(runtime.config),
                "health_ttl": 0, "pool_target": 1}
            while sandbox_manager._ready:
                await sandbox_manager._discard(sandbox_manager._ready.popleft().id)
            os.environ["SANDBOX_PREWARM_TARGET"] = "1"
            await sandbox_manager.pre_warm()
            report["rollback"]["pool_ready"] = len(sandbox_manager._ready)
    except Exception as exc:
        report["errors"].append({"type": type(exc).__name__, "message": "Live preparation benchmark failed; inspect local log"})
        raise
    finally:
        for user in users:
            await clean(user)
        await loader.shutdown()
        sandbox_manager.SANDBOX_PREWARM_REPLENISH = previous_replenish
        if original_ttl is None:
            os.environ.pop("SANDBOX_HEALTH_CACHE_TTL_SECONDS", None)
        else:
            os.environ["SANDBOX_HEALTH_CACHE_TTL_SECONDS"] = original_ttl
        if original_target is None:
            os.environ.pop("SANDBOX_PREWARM_TARGET", None)
        else:
            os.environ["SANDBOX_PREWARM_TARGET"] = original_target
        Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="runtime-logs/preparation-benchmark.json")
    parser.add_argument("--paths", default="hot,disabled,cold,auth,recovery,dual,rollback",
                        help="Comma-separated phases; permits resuming missing live measurements")
    parser.add_argument("--scenario-samples", type=int, default=5,
                        help="Attempts for each non-hot scenario (default 5)")
    args = parser.parse_args()
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(run(args))
