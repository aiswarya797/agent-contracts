#!/usr/bin/env python3
"""ContextBench adapter for deterministic agent-contracts context selection.

This script emits ContextBench unified prediction trajectories from local,
deterministic selector output. It does not call Codex, Claude, OpenAI, or any
other model, and it does not attempt patch repair.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

try:
    from scripts import swe_explore_agent_contracts
except ImportError:  # pragma: no cover - direct script execution path
    import swe_explore_agent_contracts  # type: ignore[no-redef]


DEFAULT_CONTEXTBENCH_ROOT = Path("/tmp/contextbench-run/ContextBench")
DEFAULT_CONTEXTBENCH_CACHE = Path("/tmp/contextbench-run/repos")
DEFAULT_GOLD = DEFAULT_CONTEXTBENCH_ROOT / "data" / "contextbench_verified.parquet"
DEFAULT_OUTPUT = Path("/tmp/contextbench-run/results/agent_contracts_contextbench/predictions.jsonl")
DEFAULT_SUMMARY = Path("/tmp/contextbench-run/results/agent_contracts_contextbench/summary.json")
ADAPTER_SCHEMA = "agent-contracts-contextbench-deterministic-v1"


@dataclasses.dataclass
class CheckoutResult:
    repo_url: str
    commit: str
    worktree: Path
    actual_commit: str


def load_parquet_rows(path: Path) -> list[dict[str, Any]]:
    try:
        import pyarrow.parquet as pq  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise RuntimeError("pyarrow is required to read ContextBench parquet files") from exc

    parquet_file = pq.ParquetFile(path)
    available = set(parquet_file.schema.names)
    wanted = [
        "instance_id",
        "original_inst_id",
        "repo",
        "repo_url",
        "language",
        "base_commit",
        "problem_statement",
        "source",
    ]
    columns = [name for name in wanted if name in available]
    return parquet_file.read(columns=columns).to_pylist()


def filtered_rows(
    rows: list[dict[str, Any]],
    *,
    language: str | None,
    source: str | None,
    repo: str | None,
    repo_regex: str | None,
    offset: int,
    limit: int | None,
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    repo_pattern = re.compile(repo_regex) if repo_regex else None
    for row in rows:
        row_repo = str(row.get("repo") or "")
        if language and str(row.get("language") or "").lower() != language.lower():
            continue
        if source and str(row.get("source") or "").lower() != source.lower():
            continue
        if repo and row_repo.lower() != repo.lower():
            continue
        if repo_pattern and not repo_pattern.search(row_repo):
            continue
        selected.append(row)
    if offset:
        selected = selected[offset:]
    if limit is not None:
        selected = selected[:limit]
    return selected


def parse_owner_repo(original_inst_id: str, repo_hint: str) -> tuple[str, str] | None:
    value = original_inst_id.strip()
    if value.startswith("instance_"):
        value = value[len("instance_") :]
    match = re.match(r"^([A-Za-z0-9_.-]+)__([A-Za-z0-9_.-]+)-", value)
    if match:
        return match.group(1), match.group(2)
    if "/" in repo_hint:
        owner, repo = repo_hint.split("/", 1)
        if owner and repo:
            return owner, repo
    return None


def resolve_repo_url(row: dict[str, Any]) -> str:
    repo_url = str(row.get("repo_url") or "").strip()
    if repo_url:
        return repo_url
    repo_hint = str(row.get("repo") or "").strip()
    if "/" in repo_hint:
        return f"https://github.com/{repo_hint}.git"
    parsed = parse_owner_repo(str(row.get("original_inst_id") or ""), repo_hint)
    if parsed:
        owner, repo = parsed
        return f"https://github.com/{owner}/{repo}.git"
    return ""


def git_head(repo: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def contextbench_checkout(
    row: dict[str, Any],
    *,
    cache_dir: Path,
    checkout_fn: Any,
    verbose: bool,
) -> CheckoutResult | str:
    repo_url = resolve_repo_url(row)
    commit = str(row.get("base_commit") or "").strip()
    if not repo_url:
        return "missing_repo_url"
    if not commit:
        return "missing_base_commit"
    worktree = checkout_fn(repo_url, commit, cache_dir.as_posix(), verbose=verbose)
    if not worktree:
        return "checkout_failed"
    worktree_path = Path(worktree)
    actual = git_head(worktree_path)
    if actual != commit:
        return "checkout_commit_mismatch"
    return CheckoutResult(repo_url=repo_url, commit=commit, worktree=worktree_path, actual_commit=actual)


def group_spans(regions: list[dict[str, int | str]]) -> dict[str, list[dict[str, int]]]:
    grouped: dict[str, list[dict[str, int]]] = {}
    for region in regions:
        path = str(region["path"])
        grouped.setdefault(path, []).append({"start": int(region["start"]), "end": int(region["end"])})
    return grouped


def unique_region_files(regions: list[dict[str, int | str]]) -> list[str]:
    seen: set[str] = set()
    files: list[str] = []
    for region in regions:
        path = str(region["path"])
        if path in seen:
            continue
        seen.add(path)
        files.append(path)
    return files


def build_prediction(
    row: dict[str, Any],
    checkout_result: CheckoutResult,
    selector_row: dict[str, Any],
    *,
    gold_path: Path,
    strategy: str,
) -> dict[str, Any]:
    regions = list(selector_row.get("regions") or [])
    pred_files = unique_region_files(regions)
    pred_spans = group_spans(regions)
    selector_metadata = selector_row.get("metadata") or {}
    if not pred_files:
        pred_files = list(selector_metadata.get("included_files") or [])
    final_step = {
        "files": pred_files,
        "spans": pred_spans,
        "symbols": {},
    }
    return {
        "instance_id": row.get("instance_id"),
        "original_inst_id": row.get("original_inst_id"),
        "repo_url": checkout_result.repo_url,
        "commit": checkout_result.commit,
        "traj_data": {
            "pred_steps": [final_step],
            "pred_files": pred_files,
            "pred_spans": pred_spans,
            "pred_symbols": {},
        },
        "model_patch": "",
        "metadata": {
            "schema": ADAPTER_SCHEMA,
            "gold": gold_path.as_posix(),
            "selector": f"agent-contracts:{strategy}",
            "repo": row.get("repo"),
            "language": row.get("language"),
            "source": row.get("source"),
            "checkout": {
                "repo_url": checkout_result.repo_url,
                "base_commit": checkout_result.commit,
                "actual_commit": checkout_result.actual_commit,
                "worktree": checkout_result.worktree.as_posix(),
                "faithful": checkout_result.actual_commit == checkout_result.commit,
            },
            "allowed_inputs": [
                "ContextBench problem_statement",
                "base repository snapshot",
                "agent-contracts deterministic analyzer output",
                "base repository local contracts and AGENTS.md files when present",
            ],
            "selector_metadata": selector_metadata,
        },
    }


def import_contextbench_checkout(contextbench_root: Path) -> Any:
    sys.path.insert(0, contextbench_root.as_posix())
    try:
        from contextbench.core import checkout  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(f"Could not import ContextBench from {contextbench_root}") from exc
    return checkout


def run(args: argparse.Namespace) -> int:
    gold_path = args.gold.resolve()
    contextbench_root = args.contextbench_root.resolve()
    cache_dir = args.cache.resolve()
    output_path = args.output.resolve()
    summary_path = args.summary.resolve()

    rows = load_parquet_rows(gold_path)
    selected = filtered_rows(
        rows,
        language=args.language,
        source=args.source,
        repo=args.repo,
        repo_regex=args.repo_regex,
        offset=args.offset,
        limit=args.limit,
    )
    checkout_fn = import_contextbench_checkout(contextbench_root)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.parent.mkdir(parents=True, exist_ok=True)

    predictions: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    for index, row in enumerate(selected, start=1):
        instance_id = str(row.get("instance_id") or "")
        if not instance_id:
            skipped.append({"instance_id": "", "reason": "missing_instance_id"})
            continue
        if not row.get("problem_statement"):
            skipped.append({"instance_id": instance_id, "reason": "missing_problem_statement"})
            continue

        checkout_result = contextbench_checkout(
            row,
            cache_dir=cache_dir,
            checkout_fn=checkout_fn,
            verbose=not args.quiet_checkout,
        )
        if isinstance(checkout_result, str):
            skipped.append({"instance_id": instance_id, "reason": checkout_result})
            continue

        try:
            selector_row = swe_explore_agent_contracts.build_output_row(
                {"instance_id": instance_id},
                str(row.get("problem_statement") or ""),
                checkout_result.worktree,
                args.strategy,
                top_k=args.top_k,
                max_files=args.max_files,
                max_bytes=args.max_bytes,
                line_window=args.line_window,
                line_overlap=args.line_overlap,
                beat_sota1_regions_per_file=args.beat_sota1_regions_per_file,
                beat_sota1_expansion_rounds=args.beat_sota1_expansion_rounds,
                ablations=set(args.ablation or []),
            )
        except Exception as exc:  # pragma: no cover - exercised by real repos
            skipped.append(
                {
                    "instance_id": instance_id,
                    "reason": "selector_error",
                    "detail": f"{type(exc).__name__}: {exc}",
                }
            )
            continue

        prediction = build_prediction(
            row,
            checkout_result,
            selector_row,
            gold_path=gold_path,
            strategy=args.strategy,
        )
        prediction["metadata"]["sample_index"] = index
        predictions.append(prediction)

    with output_path.open("w", encoding="utf-8") as handle:
        for prediction in predictions:
            handle.write(json.dumps(prediction, ensure_ascii=False, sort_keys=True) + "\n")

    faithful = sum(
        1
        for prediction in predictions
        if prediction.get("metadata", {}).get("checkout", {}).get("faithful") is True
    )
    summary = {
        "schema": ADAPTER_SCHEMA,
        "gold": gold_path.as_posix(),
        "contextbench_root": contextbench_root.as_posix(),
        "contextbench_commit": git_head(contextbench_root),
        "output": output_path.as_posix(),
        "strategy": args.strategy,
        "filters": {
            "language": args.language,
            "source": args.source,
            "repo": args.repo,
            "repo_regex": args.repo_regex,
            "offset": args.offset,
            "limit": args.limit,
        },
        "limits": {
            "top_k": args.top_k,
            "max_files": args.max_files,
            "max_bytes": args.max_bytes,
            "line_window": args.line_window,
            "line_overlap": args.line_overlap,
            "beat_sota1_regions_per_file": args.beat_sota1_regions_per_file,
            "beat_sota1_expansion_rounds": args.beat_sota1_expansion_rounds,
            "ablations": sorted(args.ablation or []),
        },
        "dataset_rows": len(rows),
        "attempted": len(selected),
        "succeeded": len(predictions),
        "skipped": len(skipped),
        "faithful_checkouts": faithful,
        "skips": skipped,
        "instances": [prediction["instance_id"] for prediction in predictions],
    }
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summary, sort_keys=True))
    return 0 if predictions else 2


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run deterministic agent-contracts context selection on ContextBench rows."
    )
    parser.add_argument("--contextbench-root", type=Path, default=DEFAULT_CONTEXTBENCH_ROOT)
    parser.add_argument("--gold", type=Path, default=DEFAULT_GOLD)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CONTEXTBENCH_CACHE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--strategy", choices=swe_explore_agent_contracts.STATIC_STRATEGIES, default="beat-sota1")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--language", default=None)
    parser.add_argument("--source", default=None)
    parser.add_argument("--repo", default=None)
    parser.add_argument("--repo-regex", default=None)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--max-files", type=int, default=swe_explore_agent_contracts.DEFAULT_MAX_FILES)
    parser.add_argument("--max-bytes", type=int, default=swe_explore_agent_contracts.DEFAULT_MAX_BYTES)
    parser.add_argument("--line-window", type=int, default=80)
    parser.add_argument("--line-overlap", type=int, default=20)
    parser.add_argument(
        "--beat-sota1-regions-per-file",
        type=int,
        default=swe_explore_agent_contracts.DEFAULT_BEAT_SOTA1_REGIONS_PER_FILE,
    )
    parser.add_argument(
        "--beat-sota1-expansion-rounds",
        type=int,
        default=swe_explore_agent_contracts.DEFAULT_BEAT_SOTA1_EXPANSION_ROUNDS,
    )
    parser.add_argument(
        "--ablation",
        action="append",
        choices=swe_explore_agent_contracts.BEAT_SOTA1_ABLATIONS,
        help="Disable one beatSOTA1 component; repeat for multiple ablations.",
    )
    parser.add_argument("--quiet-checkout", action="store_true")
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be >= 1")
    if args.offset < 0:
        parser.error("--offset must be >= 0")
    if args.top_k < 1:
        parser.error("--top-k must be >= 1")
    if args.max_files < 1:
        parser.error("--max-files must be >= 1")
    if args.max_bytes < 1:
        parser.error("--max-bytes must be >= 1")
    if args.line_window < 1:
        parser.error("--line-window must be >= 1")
    if args.line_overlap < 0 or args.line_overlap >= args.line_window:
        parser.error("--line-overlap must be >= 0 and smaller than --line-window")
    if args.beat_sota1_regions_per_file < 1:
        parser.error("--beat-sota1-regions-per-file must be >= 1")
    if args.beat_sota1_expansion_rounds < 0:
        parser.error("--beat-sota1-expansion-rounds must be >= 0")
    return args


def main(argv: list[str] | None = None) -> int:
    return run(parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
