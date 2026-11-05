"""Replace only existing board values in a freshly downloaded Yuque Lake body.

Usage: python docs/diagrams/prepare_yuque_board_update.py READ_RESPONSE OUTPUT
The output is a PUT payload for the document endpoint; no credentials are read.
All non-board document content is preserved byte for byte.
"""
from pathlib import Path
import html
import json
import re
import sys
from urllib.parse import quote, unquote

ROOT = Path(__file__).parent


def prepare(response_path, output_path):
    data = json.loads(Path(response_path).read_text())["data"]
    before = data["body_lake"]
    manifest = json.loads((ROOT / "yuque-boards.json").read_text())
    expected = {b["resource_id"]: b for b in manifest}
    updated = []
    pattern = re.compile(r'<card\b[^>]*\bname="board"[^>]*>')

    def replace(match):
        tag = match.group()
        value_match = re.search(r'\bvalue="([^"]*)"', tag)
        raw = unquote(html.unescape(value_match.group(1)))
        value = json.loads(raw.removeprefix("data:"))
        resource_id = value["id"]
        if resource_id not in expected:
            return tag  # Preserve any board the user added independently.
        name = expected[resource_id]["name"]
        styled = json.loads((ROOT / "styled" / (name + ".json")).read_text())
        value.update(styled)
        assert value["id"] == resource_id
        encoded = "data:" + quote(json.dumps(value, ensure_ascii=False, separators=(",", ":")), safe="")
        updated.append(resource_id)
        return tag[:value_match.start(1)] + encoded + tag[value_match.end(1):]

    after = pattern.sub(replace, before)
    assert len(updated) == len(set(updated)) == len(expected), "Expected boards missing or duplicated; re-read before writing"
    assert pattern.sub("<BOARD>", before) == pattern.sub("<BOARD>", after)
    Path(output_path).write_text(json.dumps({"format": "lake", "body": after}, ensure_ascii=False))
    print(json.dumps({"doc_id": data["id"], "source_updated_at": data["updated_at"],
                      "boards_updated": len(updated), "non_board_content_unchanged": True}, ensure_ascii=False))


if __name__ == "__main__":
    prepare(*sys.argv[1:])
