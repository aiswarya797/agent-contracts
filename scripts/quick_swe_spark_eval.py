#!/usr/bin/env python3
"""Prepare or run a small paired Spark SWE-Explore evaluation.

The default mode writes a targeted subset and prints the exact commands without
launching Codex. Pass --run only when you are ready to execute the small run.
"""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

try:  # Importable both as `python scripts/...` and from unittest.
    from scripts import swe_explore_agent_contracts as swe
except ImportError:  # pragma: no cover - direct script execution path
    import swe_explore_agent_contracts as swe  # type: ignore[no-redef]


DEFAULT_TARGET_INSTANCE = "psf__requests-5414"
DEFAULT_OUTPUT_DIR = Path("benchmark-results/quick-swe-spark")
DEFAULT_CONDITIONS = (
    "spark_baseline",
    "context_localized_preflight",
    "spark_context_localized",
)
LEGACY_CONDITIONS = ("spark_beat_sota1",)
CONDITIONS: dict[str, dict[str, Any]] = {
    "spark_baseline": {
        "description": "Spark/Codex baseline without Agent Contracts precontext.",
        "strategy": "codex-baseline",
        "agent": True,
        "model_profile": "spark",
    },
    "context_localized_preflight": {
        "description": "Deterministic Agent Contracts localizer with Spark gate limits.",
        "strategy": "context-localized",
        "agent": False,
        "model_profile": "spark",
    },
    "spark_context_localized": {
        "description": "Spark/Codex with context-localized Agent Contracts precontext.",
        "strategy": "codex-context-localized",
        "agent": True,
        "model_profile": "spark",
    },
    "spark_beat_sota1": {
        "description": "Optional previous Agent Contracts beat-sota1 path with the same Spark/Codex command.",
        "strategy": "codex-beat-sota1",
        "agent": True,
        "model_profile": "spark",
    },
}


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def select_records(
    records: list[dict[str, Any]],
    requested_instances: list[str],
    fallback_limit: int,
) -> tuple[list[dict[str, Any]], list[str]]:
    by_id = {str(record.get("instance_id", "")): record for record in records if record.get("instance_id")}
    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()
    missing: list[str] = []
    for instance_id in dict.fromkeys(requested_instances):
        record = by_id.get(instance_id)
        if record is None:
            missing.append(instance_id)
            continue
        selected.append(record)
        selected_ids.add(instance_id)
    if fallback_limit > 0 and len(selected) < fallback_limit:
        for record in records:
            instance_id = str(record.get("instance_id", ""))
            if not instance_id or instance_id in selected_ids:
                continue
            selected.append(record)
            selected_ids.add(instance_id)
            if len(selected) >= fallback_limit:
                break
    return selected, missing


def command_for_condition(
    *,
    repo_root: Path,
    bench: Path,
    repos: Path,
    issue_map: Path,
    output_dir: Path,
    condition_name: str,
    args: argparse.Namespace,
) -> tuple[list[str], list[str], Path]:
    condition = CONDITIONS[condition_name]
    output = output_dir / condition_name / "top5.jsonl"
    relative_script = Path("scripts") / "swe_explore_agent_contracts.py"
    script_path = repo_root / relative_script
    common_args = [
        "--bench",
        bench.as_posix(),
        "--repos",
        repos.as_posix(),
        "--issue-map",
        issue_map.as_posix(),
        "--strategy",
        str(condition["strategy"]),
        "--model-profile",
        str(condition["model_profile"]),
        "--top-k",
        str(args.top_k),
        "--max-files",
        str(args.max_files),
        "--max-bytes",
        str(args.max_bytes),
        "--line-window",
        str(args.line_window),
        "--line-overlap",
        str(args.line_overlap),
        "--output",
        output.as_posix(),
    ]
    if args.evaluate:
        common_args.append("--evaluate")
    if condition["agent"]:
        common_args.extend(
            [
                "--codex-command",
                args.spark_codex_command,
                "--codex-timeout",
                str(args.codex_timeout),
            ]
        )
    run_command = [sys.executable, script_path.as_posix(), *common_args]
    display_command = ["python3", relative_script.as_posix(), *common_args]
    return run_command, display_command, output


def inspection_commands(output_dir: Path, condition_names: list[str]) -> list[str]:
    commands = []
    if "context_localized_preflight" in condition_names:
        commands.append(
            "jq -c '{instance_id, explorer, regions, selected_bytes: .metadata.selected_bytes, "
            "gate: .metadata.context_localized.gate, noisy_path_count: .metadata.context_localized.noisy_path_count, "
            "vendored_noisy_path_count: .metadata.context_localized.vendored_noisy_path_count}' "
            f"{shlex.quote((output_dir / 'context_localized_preflight' / 'top5.jsonl').as_posix())}"
        )
    if "spark_context_localized" in condition_names:
        commands.append(
            "jq -c '{instance_id, explorer, num_regions, codex_returncode: .metadata.codex_returncode, "
            "gate: .metadata.precontext.gate, selected_bytes: .metadata.precontext.selected_bytes, "
            "risk_counts: .metadata.precontext.risk_counts, regions}' "
            f"{shlex.quote((output_dir / 'spark_context_localized' / 'top5.jsonl').as_posix())}"
        )
    if "spark_baseline" in condition_names:
        commands.append(
            "jq -c '{instance_id, explorer, num_regions, codex_returncode: .metadata.codex_returncode, "
            "runner_error: .metadata.runner_error, regions}' "
            f"{shlex.quote((output_dir / 'spark_baseline' / 'top5.jsonl').as_posix())}"
        )
    if "spark_beat_sota1" in condition_names:
        commands.append(
            "jq -c '{instance_id, explorer, num_regions, codex_returncode: .metadata.codex_returncode, "
            "precontext_regions: .metadata.precontext.regions, regions}' "
            f"{shlex.quote((output_dir / 'spark_beat_sota1' / 'top5.jsonl').as_posix())}"
        )
    return commands


def run(args: argparse.Namespace) -> int:
    repo_root = Path(__file__).resolve().parents[1]
    bench = args.bench.resolve()
    repos = args.repos.resolve()
    source_issue_map = args.issue_map.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    records = swe.load_jsonl(bench)
    requested_instances = args.instance or [DEFAULT_TARGET_INSTANCE]
    selected, missing = select_records(records, requested_instances, args.fallback_limit)
    if not selected:
        raise SystemExit("No requested or fallback instances were found in the bench file.")

    source_issues = swe.load_issue_map(source_issue_map)
    subset_bench = output_dir / "bench.quick-spark.jsonl"
    subset_issue_map = output_dir / "issue_map.quick-spark.json"
    selected_issue_map = {
        str(record["instance_id"]): source_issues.get(str(record["instance_id"]), str(record.get("problem_statement", "")))
        for record in selected
    }
    write_jsonl(subset_bench, selected)
    subset_issue_map.write_text(json.dumps(selected_issue_map, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (output_dir / "instances.quick-spark.txt").write_text(
        "\n".join(str(record["instance_id"]) for record in selected) + "\n",
        encoding="utf-8",
    )

    condition_names = list(args.condition or DEFAULT_CONDITIONS)
    if args.include_legacy:
        condition_names.extend(name for name in LEGACY_CONDITIONS if name not in condition_names)

    commands = []
    for condition_name in condition_names:
        run_command, display_command, output = command_for_condition(
            repo_root=repo_root,
            bench=subset_bench,
            repos=repos,
            issue_map=subset_issue_map,
            output_dir=output_dir,
            condition_name=condition_name,
            args=args,
        )
        commands.append(
            {
                "condition": condition_name,
                "description": CONDITIONS[condition_name]["description"],
                "output": output.as_posix(),
                "command": shlex.join(display_command),
                "run_command": run_command,
            }
        )

    summary = {
        "mode": "run" if args.run else "dry-run",
        "subset_bench": subset_bench.as_posix(),
        "subset_issue_map": subset_issue_map.as_posix(),
        "instances": [str(record["instance_id"]) for record in selected],
        "missing_requested_instances": missing,
        "conditions": [
            {key: value for key, value in item.items() if key != "run_command"}
            for item in commands
        ],
        "inspect": inspection_commands(output_dir, condition_names),
    }
    print(json.dumps(summary, indent=2, sort_keys=True))

    if not args.run:
        return 0

    returncode = 0
    for item in commands:
        output = Path(str(item["output"]))
        output.parent.mkdir(parents=True, exist_ok=True)
        completed = subprocess.run(item["run_command"], cwd=repo_root, check=False)
        if completed.returncode != 0:
            returncode = completed.returncode
            break
    return returncode


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare or run a targeted Spark SWE-Explore evaluation.")
    parser.add_argument("--bench", required=True, type=Path, help="SWE-Explore benchmark JSONL.")
    parser.add_argument("--repos", required=True, type=Path, help="Repository snapshot root.")
    parser.add_argument("--issue-map", required=True, type=Path, help="JSON {instance_id: issue_text}.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--instance", action="append", help="Instance id to include. Repeat for a 2-5 case run.")
    parser.add_argument("--fallback-limit", type=int, default=1, help="Small fallback count when requested ids are absent.")
    parser.add_argument("--condition", action="append", choices=tuple(CONDITIONS), help="Condition to run. Repeat to override defaults.")
    parser.add_argument("--include-legacy", action="store_true", help="Also run the previous beat-sota1 Agent Contracts path.")
    parser.add_argument("--run", action="store_true", help="Execute commands. Omit for dry-run command generation.")
    parser.add_argument("--no-evaluate", dest="evaluate", action="store_false", default=True, help="Skip SWE-Explore evaluator metrics.")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--max-files", type=int, default=swe.DEFAULT_MAX_FILES)
    parser.add_argument("--max-bytes", type=int, default=swe.DEFAULT_MAX_BYTES)
    parser.add_argument("--line-window", type=int, default=80)
    parser.add_argument("--line-overlap", type=int, default=20)
    parser.add_argument("--codex-timeout", type=int, default=swe.DEFAULT_CODEX_TIMEOUT)
    parser.add_argument(
        "--spark-codex-command",
        default=swe.DEFAULT_CODEX_COMMAND,
        help=(
            "Spark/Codex command template for agent conditions. Supports {repo}, "
            "{response_file}, and {instance_id} placeholders."
        ),
    )
    args = parser.parse_args(argv)
    if args.fallback_limit < 0:
        parser.error("--fallback-limit must be >= 0")
    if args.top_k < 1:
        parser.error("--top-k must be >= 1")
    if args.max_files < 1:
        parser.error("--max-files must be >= 1")
    if args.max_bytes < 1:
        parser.error("--max-bytes must be >= 1")
    if args.line_window < 1:
        parser.error("--line-window must be >= 1")
    if args.line_overlap < 0:
        parser.error("--line-overlap must be >= 0")
    if args.line_overlap >= args.line_window:
        parser.error("--line-overlap must be smaller than --line-window")
    if args.codex_timeout < 1:
        parser.error("--codex-timeout must be >= 1")
    return args


def main(argv: list[str] | None = None) -> int:
    return run(parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
