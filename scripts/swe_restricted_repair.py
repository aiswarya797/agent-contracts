#!/usr/bin/env python3
"""Restricted-context repair harness for SWE-Explore predictions.

The harness feeds issue text plus predicted file/line snippets to a fixed repair
runner and records pairable per-instance outcomes. It intentionally does not
claim full SWE-bench correctness; it is a bridge from localization quality to
downstream repair behavior.
"""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import tempfile
from pathlib import Path
from typing import Any

try:
    from scripts import swe_explore_agent_contracts as swe
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    import swe_explore_agent_contracts as swe  # type: ignore[no-redef]


DEFAULT_TIMEOUT = 900


def load_predictions(path: Path) -> list[dict[str, Any]]:
    return swe.load_jsonl(path)


def context_snippets(repo: Path, regions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    snippets = []
    for region in regions:
        path = region.get("path")
        start = region.get("start")
        end = region.get("end")
        if not isinstance(path, str):
            continue
        try:
            start_int = int(start)
            end_int = int(end)
        except (TypeError, ValueError):
            continue
        snippet = swe.snippet_for_region(
            repo,
            {"path": path, "start": start_int, "end": end_int},
            max_chars=8_000,
        )
        if snippet:
            snippets.append(
                {
                    "path": path,
                    "start": start_int,
                    "end": end_int,
                    "content": snippet,
                    "bytes": len(snippet.encode("utf-8")),
                }
            )
    return snippets


def build_repair_input(
    record: dict[str, Any],
    prediction: dict[str, Any],
    issue_text: str,
    repo: Path,
) -> dict[str, Any]:
    snippets = context_snippets(repo, prediction.get("regions", []))
    return {
        "schema_version": "restricted-repair-v1",
        "instance_id": record["instance_id"],
        "repo_path": repo.as_posix(),
        "issue": issue_text,
        "prediction_explorer": prediction.get("explorer"),
        "regions": [
            {
                "path": item["path"],
                "start": item["start"],
                "end": item["end"],
                "content": item["content"],
            }
            for item in snippets
        ],
        "limits": {
            "region_count": len(snippets),
            "context_bytes": sum(int(item["bytes"]) for item in snippets),
        },
        "instructions": [
            "Use only the provided issue and restricted region snippets as starting context.",
            "Return JSON with resolved, patch, commands_run, files_edited, and notes.",
        ],
    }


def run_mock_repair(payload: dict[str, Any]) -> dict[str, Any]:
    regions = payload.get("regions", [])
    files = []
    if isinstance(regions, list):
        files = [item.get("path") for item in regions if isinstance(item, dict) and isinstance(item.get("path"), str)]
    return {
        "resolved": bool(files),
        "patch": "",
        "commands_run": [],
        "files_edited": sorted(set(files))[:1],
        "notes": "mock restricted repair succeeds when at least one region is available.",
    }


def run_subprocess_repair(command_template: str, repo: Path, payload: dict[str, Any], timeout: int) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="agent-contracts-repair-") as tmp:
        response_file = Path(tmp) / "repair-response.json"
        values = {
            "repo": repo.as_posix(),
            "response_file": response_file.as_posix(),
            "instance_id": str(payload.get("instance_id", "")),
        }
        command = shlex.split(command_template.format(**values))
        try:
            result = subprocess.run(
                command,
                cwd=repo,
                input=json.dumps(payload),
                text=True,
                capture_output=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            return {
                "resolved": False,
                "patch": "",
                "commands_run": [],
                "files_edited": [],
                "notes": "",
                "failure_reason": "repair-timeout",
                "stdout": exc.stdout or "",
                "stderr": exc.stderr or "",
            }
        response_text = response_file.read_text(encoding="utf-8") if response_file.is_file() else result.stdout
        try:
            parsed = json.loads(response_text)
        except json.JSONDecodeError as exc:
            return {
                "resolved": False,
                "patch": "",
                "commands_run": [],
                "files_edited": [],
                "notes": "",
                "failure_reason": f"invalid-json-output: {exc.msg}",
                "stdout": result.stdout,
                "stderr": result.stderr,
                "returncode": result.returncode,
            }
        if not isinstance(parsed, dict):
            parsed = {"resolved": False, "failure_reason": "top-level JSON must be an object"}
        parsed.setdefault("returncode", result.returncode)
        parsed.setdefault("stdout", result.stdout)
        parsed.setdefault("stderr", result.stderr)
        if result.returncode != 0:
            parsed.setdefault("resolved", False)
            parsed.setdefault("failure_reason", f"repair-command-exited-{result.returncode}")
        return parsed


def normalize_repair_result(raw: dict[str, Any]) -> dict[str, Any]:
    commands = raw.get("commands_run")
    files_edited = raw.get("files_edited")
    patch = str(raw.get("patch", "") or "")
    normalized_files_edited = files_edited if isinstance(files_edited, list) else []
    return {
        "resolved": bool(raw.get("resolved")),
        "patch": patch,
        "patch_bytes": len(patch.encode("utf-8")),
        "patch_lines": len(patch.splitlines()),
        "commands_run": commands if isinstance(commands, list) else [],
        "files_edited": normalized_files_edited,
        "files_edited_count": len(normalized_files_edited),
        "notes": str(raw.get("notes", "") or ""),
        "failure_reason": str(raw.get("failure_reason", "") or ""),
        "returncode": raw.get("returncode"),
        "stdout_excerpt": str(raw.get("stdout", "") or "")[-4_000:],
        "stderr_excerpt": str(raw.get("stderr", "") or "")[-4_000:],
    }


def run(args: argparse.Namespace) -> int:
    bench_path = args.bench.resolve()
    repos_root = args.repos.resolve()
    output_path = args.output.resolve()
    records = {str(row.get("instance_id")): row for row in swe.load_jsonl(bench_path)}
    issue_map = swe.load_issue_map(args.issue_map.resolve())
    predictions = load_predictions(args.predictions.resolve())
    output_path.parent.mkdir(parents=True, exist_ok=True)

    rows = []
    skipped_missing_repo = 0
    for prediction in predictions:
        instance_id = str(prediction.get("instance_id", ""))
        record = records.get(instance_id)
        if not record:
            continue
        repo = swe.resolve_repo_dir(record, repos_root)
        if repo is None:
            skipped_missing_repo += 1
            continue
        issue_text = issue_map.get(instance_id) or str(record.get("problem_statement", ""))
        payload = build_repair_input(record, prediction, issue_text, repo)
        if args.mode == "mock":
            repair_result = run_mock_repair(payload)
        else:
            if not args.repair_command:
                raise SystemExit("--repair-command is required in subprocess mode")
            repair_result = run_subprocess_repair(args.repair_command, repo, payload, args.timeout)
        normalized = normalize_repair_result(repair_result)
        rows.append(
            {
                "instance_id": instance_id,
                "prediction_explorer": prediction.get("explorer"),
                "mode": args.mode,
                "region_count": len(payload["regions"]),
                "context_bytes": payload["limits"]["context_bytes"],
                **normalized,
            }
        )

    with output_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    resolved = sum(1 for row in rows if row["resolved"])
    print(
        json.dumps(
            {
                "output": output_path.as_posix(),
                "predictions": args.predictions.resolve().as_posix(),
                "mode": args.mode,
                "written": len(rows),
                "resolved": resolved,
                "resolve_rate": resolved / len(rows) if rows else 0.0,
                "skipped_missing_repo": skipped_missing_repo,
            },
            sort_keys=True,
        )
    )
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run restricted-context repair over SWE-Explore predictions.")
    parser.add_argument("--bench", required=True, type=Path)
    parser.add_argument("--repos", required=True, type=Path)
    parser.add_argument("--issue-map", required=True, type=Path)
    parser.add_argument("--predictions", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--mode", choices=["mock", "subprocess"], default="mock")
    parser.add_argument("--repair-command", help="Subprocess repair command. Supports {repo}, {response_file}, {instance_id}.")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    args = parser.parse_args(argv)
    if args.timeout < 1:
        parser.error("--timeout must be >= 1")
    return args


def main(argv: list[str] | None = None) -> int:
    return run(parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
