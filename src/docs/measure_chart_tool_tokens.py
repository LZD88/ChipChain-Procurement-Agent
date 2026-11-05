"""Compare model-visible schemas, offline; no remote MCP/model calls.

Run from the project root with its Python environment. Set TIKTOKEN_CACHE_DIR
to an existing tokenizer cache if network access is unavailable. Token counts
use cl100k_base as a reproducible proxy, not DeepSeek billing measurements.
"""
from pathlib import Path
import hashlib
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "test"))

from chart_test_support import make_tools
from agent.tools.chart_generator import create_unified_chart_tool
from langchain_core.utils.function_calling import convert_to_openai_tool


def measure():
    # Conversion needs a non-null session; no tool is invoked.
    raw = make_tools(object())
    unified, excluded = create_unified_chart_tool(raw)
    charts = [tool for tool in raw if tool not in excluded]
    serialize = lambda value: json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    values = {
        "raw_chart_tools": serialize([convert_to_openai_tool(tool) for tool in charts]),
        "unified_chart_tool": serialize([convert_to_openai_tool(unified)]),
        "compact_description": unified.description,
        "reference_file": (ROOT / "skills/analyis_chart_params.md").read_text(),
    }
    result = {
        "scope": "26 chart tools versus one wrapper; other tools excluded on both sides",
        "tokenizer_note": "cl100k_base proxy; not DeepSeek usage or API framing tokens",
        "raw_tools_count": len(charts),
        "unified_tools_count": 1,
        "excluded_tools": [tool.name for tool in excluded],
        "measurements": {
            name: {"characters": len(text), "utf8_bytes": len(text.encode())}
            for name, text in values.items()
        },
        "source_sha256": {
            str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in [ROOT / "test/fixtures/chart_mcp_tools.json",
                         ROOT / "agent/tools/chart_generator.py",
                         ROOT / "skills/analyis_chart_params.md"]
        },
    }
    try:
        import tiktoken
        encoder = tiktoken.get_encoding("cl100k_base")
        for name, text in values.items():
            result["measurements"][name]["cl100k_base_tokens"] = len(encoder.encode(text))
    except Exception as exc:
        result["tokenizer_error"] = type(exc).__name__
    for unit in result["measurements"]["raw_chart_tools"]:
        before = result["measurements"]["raw_chart_tools"][unit]
        after = result["measurements"]["unified_chart_tool"][unit]
        result.setdefault("schema_reduction", {})[unit] = {
            "saved": before - after,
            "percent": round((before - after) / before * 100, 2),
        }
    return result


if __name__ == "__main__":
    result = measure()
    text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    (ROOT / "docs/chart-tool-token-comparison.json").write_text(text)
    print(text)
