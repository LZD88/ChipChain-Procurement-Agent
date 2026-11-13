"""Background-only configuration and prompt projection synchronization."""
import asyncio
import json
import os
from contextlib import suppress

from agent.platform.runtime import runtime_configuration, is_enabled
from agent.platform.telemetry import langfuse_integration
from agent.prompt_releases.repository import PromptRepository


class PlatformIntegrations:
    def __init__(self):
        self.tasks = []
        self.mirrored = None
        self.mirror_status = "disabled"

    async def start(self, storage):
        runtime_configuration.load_cache()
        langfuse_integration.initialize()
        if is_enabled("NACOS_ENABLED"):
            self.tasks.append(asyncio.create_task(self._watch()))
        if is_enabled("NACOS_ENABLED") or is_enabled("LANGFUSE_ENABLED"):
            self.tasks.append(asyncio.create_task(self._mirror(storage)))

    async def _watch(self):
        while True:
            await runtime_configuration.refresh()
            await asyncio.sleep(max(3, float(os.getenv("NACOS_POLL_SECONDS", "10"))))

    async def _mirror(self, storage):
        while True:
            try:
                repository = PromptRepository(storage)
                state = await repository.state()
                versions = list(dict.fromkeys(v for v in (state.get("stable"), state.get("canary")) if v))
                bundles = [await repository.get_version(v) for v in versions]
                projection = json.dumps({"authority": "project-mysql", "readOnlyProjection": True,
                    "revision": state["revision"], "stable": state["stable"], "canary": state.get("canary"),
                    "percentage": state.get("percentage", 0), "bundles": [b.public() for b in bundles]},
                    ensure_ascii=False, sort_keys=True)
                if is_enabled("NACOS_ENABLED") and runtime_configuration.client:
                    try:
                        # Repair external edits to this explicitly read-only projection.
                        data_id = os.getenv("NACOS_PROMPT_DATA_ID", "chipchain-published-prompts.json")
                        try:
                            current = await runtime_configuration.client.config(data_id)
                        except Exception as exc:
                            if getattr(getattr(exc, "response", None), "status_code", None) != 404:
                                raise
                            current = None
                        if current != projection:
                            await runtime_configuration.client.config(data_id, projection)
                        self.mirror_status = "connected"
                        self.mirrored = state["revision"]
                    except Exception:
                        self.mirror_status = "degraded"
                for bundle in bundles:
                    await asyncio.to_thread(langfuse_integration.sync_bundle, bundle)
            except asyncio.CancelledError:
                raise
            except Exception:
                self.mirror_status = "degraded"
            await asyncio.sleep(15)

    async def stop(self):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks.clear()
        await runtime_configuration.close()
        await asyncio.to_thread(langfuse_integration.shutdown)


platform_integrations = PlatformIntegrations()
