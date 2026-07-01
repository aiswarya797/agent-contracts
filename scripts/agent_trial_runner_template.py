#!/usr/bin/env python3
"""Template subprocess runner for `agent-trial-context`.

The harness passes JSON on stdin with:
- repo_path: isolated temporary repository path
- task: user-facing task prompt
- strategy: active context strategy
- available_context.selected_files: preloaded context file metadata/content

This template intentionally has no vendor dependency. In echo mode it returns a
deterministic valid response for local plumbing tests. Replace
`run_real_agent_adapter` with a call to your preferred agent runtime, then map
that runtime's trace into files_read, files_edited, commands_run, final_status,
and notes.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


TEST_RE = re.compile(r"(^|/)(tests?|specs?)(/|$)|(^|/).*([_.-](test|spec))\.")


def infer_test_command(path: str) -> str:
    if Path(path).suffix == ".py":
        return f"python -m pytest {path}"
    return f"npm test -- {path}"


def echo_response(payload: dict[str, Any]) -> dict[str, Any]:
    selected = payload.get("available_context", {}).get("selected_files", [])
    files_read = [item.get("path") for item in selected if isinstance(item, dict) and item.get("path")]
    commands_run = [
        {
            "command": infer_test_command(path),
            "exit_code": 0,
            "passed": True,
            "stdout": "template echo mode",
            "stderr": "",
        }
        for path in files_read
        if TEST_RE.search(path)
    ]
    return {
        "files_read": files_read,
        "files_edited": [],
        "commands_run": commands_run,
        "final_status": "success",
        "notes": "Template echo mode returned selected context as reported reads. Replace with a real agent adapter for live trials.",
    }


def run_real_agent_adapter(payload: dict[str, Any]) -> dict[str, Any]:
    """Adapter point for a real agent.

    A production wrapper should call the external agent with payload["task"],
    payload["repo_path"], payload["strategy"], and payload["available_context"],
    then return the normalized JSON contract. Keep manifest ground truth out of
    this layer; the harness scores after the run.
    """

    return {
        "files_read": [],
        "files_edited": [],
        "commands_run": [],
        "final_status": "failed",
        "notes": "No real agent adapter is configured in this template.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Template agent-trial-context subprocess runner")
    parser.add_argument("--mode", choices=["echo", "adapter"], default="echo")
    args = parser.parse_args()

    try:
        payload = json.loads(sys.stdin.read())
    except json.JSONDecodeError as exc:
        print(json.dumps({"files_read": [], "files_edited": [], "commands_run": [], "final_status": "failed", "notes": str(exc)}))
        return 1

    result = echo_response(payload) if args.mode == "echo" else run_real_agent_adapter(payload)
    print(json.dumps(result))
    return 0 if result.get("final_status") != "failed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
