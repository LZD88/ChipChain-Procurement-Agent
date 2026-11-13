"""Start only the Linux VM integration stack; never invokes local Docker.

Provision secrets and the compose file on the VM first. The Web launcher owns
VirtualBox/OpenSandbox startup; this helper only uses the configured SSH key.
"""
import argparse
from pathlib import Path
import subprocess
import sys
import os
import time
import httpx

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env", override=False)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--status", action="store_true")
    args = parser.parse_args()
    platform_enabled = os.getenv("AI_PLATFORM_VM_ENABLED", "false").lower() == "true"
    model_gateway = os.getenv("LLM_GATEWAY_ENABLED", "false").lower() == "true"
    mcp_gateway = os.getenv("MCP_GATEWAY_ENABLED", "false").lower() == "true"
    gateway_enabled = model_gateway or mcp_gateway
    if not platform_enabled and not gateway_enabled:
        print("AI platform VM startup disabled")
        return 0
    key = Path(os.environ["AI_PLATFORM_SSH_KEY"]).expanduser()
    if not key.is_file():
        raise RuntimeError("AI platform SSH key is unavailable")
    command = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "-i", str(key),
               "-p", str(int(os.getenv("AI_PLATFORM_SSH_PORT", "2223"))),
               os.getenv("AI_PLATFORM_SSH_TARGET", "osb@127.0.0.1")]
    stacks = []
    if platform_enabled:
        stacks.append("/home/osb/chipchain-ai-platform")
    if gateway_enabled:
        stacks.append("/home/osb/chipchain-higress")
    for stack in stacks:
        remote = f"cd {stack} && docker compose -f compose.vm.yml "
        remote += "ps" if args.status else "up -d --pull never --wait --wait-timeout 120"
        result = subprocess.run([*command, remote], timeout=140)
        if result.returncode:
            return result.returncode
    if gateway_enabled and not args.status:
        probes = []
        if model_gateway:
            probes.append(os.environ["LLM_GATEWAY_BASE_URL"].rstrip("/") + "/chat/completions")
        if mcp_gateway:
            probes.append(os.environ["MCP_GATEWAY_BASE_URL"].rstrip("/") + "/mcp-servers/chipchain-erp")
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            try:
                with httpx.Client(timeout=2, trust_env=False) as client:
                    statuses = [client.post(url, json={"model": "deepseek-flash", "messages": []}).status_code
                                for url in probes]
                if all(code == 401 for code in statuses):
                    print("Higress is ready; unauthenticated model/MCP calls are rejected")
                    return 0
            except httpx.HTTPError:
                pass
            time.sleep(1)
        print("Higress authentication/readiness check failed")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
