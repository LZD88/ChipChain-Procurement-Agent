"""Local operator CLI for server-owned prompt-candidate pairs and gates."""
import argparse
import asyncio
import json
from agent.mysql_storage import mysql_storage
from agent.prompt_releases.service import PromptReleaseService
from agent.authorization import PROMPT_MAINTAIN


async def run(args):
    await mysql_storage.initialize()
    try:
        if not await mysql_storage.has_capability(args.actor, PROMPT_MAINTAIN):
            raise ValueError("operator requires prompt:maintain")
        service = PromptReleaseService(mysql_storage)
        if args.command == "pair":
            result = await service.pair(args.candidate, args.actor)
        else:
            result = await service.gate(args.stable_run, args.candidate_run, args.actor)
        print(json.dumps(result, ensure_ascii=False, default=str, indent=2))
        return 0 if result.get("passed", True) else 2
    finally:
        await mysql_storage.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--actor", required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    pair = sub.add_parser("pair"); pair.add_argument("--candidate", required=True)
    gate = sub.add_parser("gate"); gate.add_argument("--stable-run", required=True); gate.add_argument("--candidate-run", required=True)
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
