from __future__ import annotations

import json
import re
import sys
from pathlib import Path


def is_test_path(path: str) -> bool:
    return bool(re.search(r"(^|/)(tests?|specs?)(/|$)|(^|/).*([_.-](test|spec))\.", path))


payload = json.loads(sys.stdin.read())
repo = Path(payload["repo_path"])
selected = payload["available_context"]["selected_files"]
files_read = [item["path"] for item in selected]
edit_path = next(
    (
        item["path"]
        for item in selected
        if item.get("role") == "source" and Path(item["path"]).name not in {"__init__.py", "SPEC.md", "AGENTS.md"}
    ),
    None,
)
files_edited = []
if edit_path:
    target = repo / edit_path
    target.write_text(target.read_text(encoding="utf-8") + "\n# fake subprocess edit\n", encoding="utf-8")
    files_edited.append(edit_path)

commands_run = [
    {"command": f"python -m pytest {path}", "exit_code": 0, "passed": True, "stdout": "fake pass", "stderr": ""}
    for path in files_read
    if is_test_path(path)
]

print(
    json.dumps(
        {
            "files_read": files_read,
            "files_edited": files_edited,
            "commands_run": commands_run,
            "final_status": "success",
            "notes": "fake subprocess agent",
        }
    )
)
