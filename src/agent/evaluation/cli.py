"""CI-friendly command line interface for procurement evaluation contracts."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from agent.evaluation.comparison import compare_runs, evaluate_regression_gates
from agent.evaluation.metrics import calculate_metrics
from agent.evaluation.reporting import portable_report, render_markdown
from agent.evaluation.rule_runner import load_default_inputs, run_rule_contract
from agent.evaluation.schemas import EvaluationSuite


def _read_json(path: str):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _emit(value: str, output: str | None) -> None:
    if output:
        Path(output).write_text(value, encoding="utf-8")
    else:
        print(value)


def _thresholds(values: list[str]) -> dict[str, float]:
    parsed = {}
    for value in values:
        name, separator, raw_threshold = value.partition("=")
        if not separator or not name:
            raise ValueError(f"invalid threshold {value!r}; expected NAME=NUMBER")
        parsed[name] = float(raw_threshold)
    return parsed


def _validate_suite(args) -> int:
    suite = EvaluationSuite.model_validate(_read_json(args.suite))
    result = {
        "valid": True,
        "suiteId": suite.suiteId,
        "scenarioCount": len(suite.scenarios),
        "contentFingerprint": suite.content_fingerprint(),
    }
    _emit(json.dumps(result, ensure_ascii=False, indent=2), args.output)
    return 0


def _run_rule(args) -> int:
    if args.suite or args.fixtures:
        if not args.suite or not args.fixtures:
            raise ValueError("--suite and --fixtures must be supplied together")
        suite = EvaluationSuite.model_validate(_read_json(args.suite))
        fixtures = _read_json(args.fixtures)
    else:
        suite, fixtures = load_default_inputs()
    report = run_rule_contract(suite, fixtures, policy_version=args.policy)
    report["metrics"] = calculate_metrics(report)["metrics"]
    body = portable_report(report)
    rendered = render_markdown(body) if args.format == "markdown" else json.dumps(
        body, ensure_ascii=False, indent=2,
    )
    _emit(rendered, args.output)
    return 0 if all(case["status"] == "PASSED" for case in report["cases"]) else 2


def _compare(args) -> int:
    baseline = _read_json(args.baseline)
    candidate = _read_json(args.candidate)
    comparison = compare_runs(baseline, candidate)
    gates = evaluate_regression_gates(comparison, _thresholds(args.threshold))
    result = {"comparison": comparison, "gates": gates}
    _emit(json.dumps(result, ensure_ascii=False, indent=2), args.output)
    return 0 if gates["passed"] else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="procurement-eval")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate-suite", help="validate a declarative suite")
    validate.add_argument("suite")
    validate.add_argument("--output")
    validate.set_defaults(handler=_validate_suite)

    run = subparsers.add_parser("run-rule-contract", help="execute isolated deterministic cases")
    run.add_argument("--suite")
    run.add_argument("--fixtures")
    run.add_argument("--policy", default="supplier-score-v1")
    run.add_argument("--format", choices=("json", "markdown"), default="json")
    run.add_argument("--output")
    run.set_defaults(handler=_run_rule)

    compare = subparsers.add_parser("compare", help="compare reports and enforce gates")
    compare.add_argument("baseline")
    compare.add_argument("candidate")
    compare.add_argument("--threshold", action="append", default=[], metavar="NAME=NUMBER")
    compare.add_argument("--output")
    compare.set_defaults(handler=_compare)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except Exception as exc:  # concise CI error; full trace remains available under pytest/debugger
        print(f"procurement-eval: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
