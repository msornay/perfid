"""Pretty-print a live stream of Claude session JSONL lines from stdin."""
import json
import sys

for line in sys.stdin:
    line = line.strip()
    if line.startswith("==>"):
        print(f"\n{line}")
        continue
    try:
        d = json.loads(line)
    except Exception:
        continue

    msg = d.get("message", {})
    role = msg.get("role", "")
    content = msg.get("content", "")

    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text":
                print(f"[{role}] {block['text']}")
            elif btype == "tool_use":
                tool_name = block.get("name", "?")
                inp = str(block.get("input", ""))
                if len(inp) > 400:
                    inp = inp[:400] + "..."
                print(f"[{role}:tool] {tool_name}: {inp}")
            elif btype == "tool_result":
                c = block.get("content", "")
                if isinstance(c, list):
                    c = c[0].get("text", "") if c else ""
                c = str(c)
                if len(c) > 400:
                    c = c[:400] + "..."
                print(f"[result] {c}")
    elif isinstance(content, str) and content:
        print(f"[{role}] {content}")
