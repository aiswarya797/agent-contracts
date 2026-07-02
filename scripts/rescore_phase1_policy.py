#!/usr/bin/env python3
"""Rescore Phase 1 policy rows with frozen baseline fallback predictions."""

from __future__ import annotations

import argparse
import copy
import csv
import json
import math
import random
from pathlib import Path
from typing import Any

try:
    from . import swe_explore_agent_contracts as swe
except ImportError:  # pragma: no cover - direct script execution path
    import swe_explore_agent_contracts as swe  # type: ignore[no-redef]


LOWER_IS_BETTER = {"noise_file_rate", "noise_region_rate"}
FALLBACK_DECISIONS = {"tool_only", "abstain"}
BOOTSTRAP_ITERATIONS = 1000
BOOTSTRAP_SEED = 20260702


def load_rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def rows_by_id(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(row["instance_id"]): row for row in rows}


def write_rows(path: Path, rows: list[dict[str, Any]], *, include_metrics: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            payload = copy.deepcopy(row)
            if not include_metrics:
                payload.pop("metrics", None)
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def gate_decision(row: dict[str, Any]) -> str:
    metadata = row.get("metadata", {})
    precontext = metadata.get("precontext") or {}
    gate = precontext.get("gate") or metadata.get("phase1_hybrid", {}).get("gate") or {}
    return str(gate.get("decision", "unknown"))


def effective_policy_gate(row: dict[str, Any]) -> str:
    metadata = row.get("metadata", {})
    precontext = metadata.get("precontext") or {}
    gate = precontext.get("gate") or metadata.get("phase1_hybrid", {}).get("gate") or {}
    decision = str(gate.get("decision", "unknown"))
    metrics = gate.get("metrics") or {}
    top_provider_count = metrics.get("top_provider_count")
    if decision == "advisory_paths":
        return "tool_only"
    if decision in {"advisory_regions", "advisory_snippets"} and top_provider_count != 2:
        return "tool_only"
    return decision


def prompt_strategy(row: dict[str, Any]) -> str:
    return str(row.get("metadata", {}).get("prompt_strategy", row.get("metadata", {}).get("condition", "")))


def policy_row(
    instance_id: str,
    baseline: dict[str, Any],
    treatment: dict[str, Any],
    *,
    policy_explorer: str,
) -> dict[str, Any]:
    decision = gate_decision(treatment)
    effective_decision = effective_policy_gate(treatment)
    fallback = effective_decision in FALLBACK_DECISIONS
    row = copy.deepcopy(baseline if fallback else treatment)
    row["explorer"] = policy_explorer
    metadata = copy.deepcopy(row.get("metadata", {}))
    treatment_metadata = treatment.get("metadata", {})
    metadata["policy_row_source"] = "frozen_baseline_fallback" if fallback else "phase1_treatment"
    metadata["baseline_reused"] = fallback
    metadata["original_treatment_gate"] = decision
    metadata["effective_policy_gate"] = effective_decision
    metadata["original_treatment_prompt_strategy"] = prompt_strategy(treatment)
    if fallback:
        metadata["frozen_baseline_explorer"] = baseline.get("explorer")
        metadata["frozen_baseline_num_regions"] = baseline.get("num_regions")
        metadata["condition"] = treatment_metadata.get("condition", treatment.get("explorer"))
        metadata["model_profile"] = treatment_metadata.get("model_profile", metadata.get("model_profile"))
        metadata["limits"] = treatment_metadata.get("limits", metadata.get("limits"))
        metadata["precontext"] = treatment_metadata.get("precontext")
        metadata["prompt_strategy"] = "codex-baseline"
    row["metadata"] = metadata
    row["instance_id"] = instance_id
    return row


def mean_metrics(rows: list[dict[str, Any]]) -> dict[str, float]:
    totals = {metric: 0.0 for metric in swe.SWE_METRICS}
    if not rows:
        return dict(totals)
    for row in rows:
        metrics = row.get("metrics", {})
        for metric in swe.SWE_METRICS:
            totals[metric] += float(metrics.get(metric, 0.0) or 0.0)
    return {metric: totals[metric] / len(rows) for metric in swe.SWE_METRICS}


def metric_delta(left: dict[str, Any], right: dict[str, Any], metric: str) -> float:
    return float(left.get("metrics", {}).get(metric, 0.0) or 0.0) - float(right.get("metrics", {}).get(metric, 0.0) or 0.0)


def oriented(delta: float, metric: str) -> float:
    return -delta if metric in LOWER_IS_BETTER else delta


def exact_two_sided_sign_test_p_value(wins: int, losses: int) -> float:
    n = wins + losses
    if n == 0:
        return 1.0
    k = min(wins, losses)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / (2**n)
    return min(1.0, 2.0 * tail)


def bootstrap_ci(deltas: list[float], *, seed: int) -> tuple[float, float]:
    if not deltas:
        return (0.0, 0.0)
    rng = random.Random(seed)
    samples: list[float] = []
    for _ in range(BOOTSTRAP_ITERATIONS):
        total = 0.0
        for _ in deltas:
            total += rng.choice(deltas)
        samples.append(total / len(deltas))
    samples.sort()
    lo = samples[int(0.025 * (len(samples) - 1))]
    hi = samples[int(0.975 * (len(samples) - 1))]
    return (lo, hi)


def paired_stats(baseline_rows: list[dict[str, Any]], policy_rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    baseline_by_id = rows_by_id(baseline_rows)
    result: dict[str, dict[str, Any]] = {}
    for metric in swe.SWE_METRICS:
        deltas = [metric_delta(row, baseline_by_id[str(row["instance_id"])], metric) for row in policy_rows]
        oriented_deltas = [oriented(delta, metric) for delta in deltas]
        wins = sum(1 for delta in oriented_deltas if delta > 1e-12)
        losses = sum(1 for delta in oriented_deltas if delta < -1e-12)
        ties = len(oriented_deltas) - wins - losses
        ci = bootstrap_ci(deltas, seed=BOOTSTRAP_SEED + sum(ord(ch) for ch in metric))
        means = mean_metrics(policy_rows)
        baseline_means = mean_metrics([baseline_by_id[str(row["instance_id"])] for row in policy_rows])
        result[metric] = {
            "baseline": baseline_means[metric],
            "policy": means[metric],
            "delta": means[metric] - baseline_means[metric],
            "oriented_delta": oriented(means[metric] - baseline_means[metric], metric),
            "higher_is_better": metric not in LOWER_IS_BETTER,
            "bootstrap_ci_95": list(ci),
            "wins": wins,
            "losses": losses,
            "ties": ties,
            "sign_test_p_two_sided": exact_two_sided_sign_test_p_value(wins, losses),
        }
    return result


def no_harm_counts(baseline_rows: list[dict[str, Any]], policy_rows: list[dict[str, Any]], metric: str) -> dict[str, int]:
    baseline_by_id = rows_by_id(baseline_rows)
    improved = harmed = neutral = 0
    for row in policy_rows:
        delta = oriented(metric_delta(row, baseline_by_id[str(row["instance_id"])], metric), metric)
        if delta > 1e-12:
            improved += 1
        elif delta < -1e-12:
            harmed += 1
        else:
            neutral += 1
    return {"improved": improved, "harmed": harmed, "neutral": neutral, "n": len(policy_rows)}


def guardrails(rows: list[dict[str, Any]]) -> dict[str, Any]:
    returncodes: dict[str, int] = {}
    precontext_bytes: list[float] = []
    runner_errors = 0
    for row in rows:
        metadata = row.get("metadata", {})
        returncode = metadata.get("codex_returncode")
        returncodes[str(returncode)] = returncodes.get(str(returncode), 0) + 1
        if metadata.get("runner_error"):
            runner_errors += 1
        precontext = metadata.get("precontext") or {}
        if isinstance(precontext.get("selected_bytes"), (int, float)):
            precontext_bytes.append(float(precontext["selected_bytes"]))
    return {
        "rows": len(rows),
        "avg_regions": sum(len(row.get("regions", [])) for row in rows) / len(rows) if rows else 0.0,
        "runner_errors": runner_errors,
        "codex_returncodes": returncodes,
        "precontext_selected_bytes_available_rows": len(precontext_bytes),
        "avg_precontext_selected_bytes": sum(precontext_bytes) / len(precontext_bytes) if precontext_bytes else 0.0,
    }


def write_paired_deltas(path: Path, baseline_rows: list[dict[str, Any]], treatment_rows: list[dict[str, Any]], policy_rows: list[dict[str, Any]]) -> None:
    baseline_by_id = rows_by_id(baseline_rows)
    treatment_by_id = rows_by_id(treatment_rows)
    fieldnames = [
        "instance_id",
        "policy_row_source",
        "original_treatment_gate",
        "original_treatment_prompt_strategy",
        "baseline_reused",
    ]
    for metric in swe.SWE_METRICS:
        fieldnames.extend([f"baseline_{metric}", f"original_treatment_{metric}", f"policy_{metric}", f"policy_delta_{metric}", f"original_treatment_delta_{metric}"])
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in policy_rows:
            instance_id = str(row["instance_id"])
            baseline = baseline_by_id[instance_id]
            treatment = treatment_by_id[instance_id]
            payload: dict[str, Any] = {
                "instance_id": instance_id,
                "policy_row_source": row.get("metadata", {}).get("policy_row_source"),
                "original_treatment_gate": row.get("metadata", {}).get("original_treatment_gate"),
                "original_treatment_prompt_strategy": row.get("metadata", {}).get("original_treatment_prompt_strategy"),
                "baseline_reused": row.get("metadata", {}).get("baseline_reused"),
            }
            for metric in swe.SWE_METRICS:
                baseline_value = float(baseline.get("metrics", {}).get(metric, 0.0) or 0.0)
                treatment_value = float(treatment.get("metrics", {}).get(metric, 0.0) or 0.0)
                policy_value = float(row.get("metrics", {}).get(metric, 0.0) or 0.0)
                payload[f"baseline_{metric}"] = baseline_value
                payload[f"original_treatment_{metric}"] = treatment_value
                payload[f"policy_{metric}"] = policy_value
                payload[f"policy_delta_{metric}"] = policy_value - baseline_value
                payload[f"original_treatment_delta_{metric}"] = treatment_value - baseline_value
            writer.writerow(payload)


def markdown_table(stats: dict[str, dict[str, Any]], metrics: list[str]) -> str:
    lines = ["| metric | baseline | policy | delta | 95% CI | wins/losses/ties | sign p |", "|---|---:|---:|---:|---:|---:|---:|"]
    for metric in metrics:
        item = stats[metric]
        ci = item["bootstrap_ci_95"]
        lines.append(
            f"| {metric} | {item['baseline']:.6f} | {item['policy']:.6f} | {item['delta']:+.6f} | "
            f"[{ci[0]:+.6f}, {ci[1]:+.6f}] | {item['wins']}/{item['losses']}/{item['ties']} | {item['sign_test_p_two_sided']:.4f} |"
        )
    return "\n".join(lines)


def write_summary_md(path: Path, summary: dict[str, Any]) -> None:
    metrics = ["f1_score", "weighted_core_coverage", "recall", "hit_region_rate", "hit_file_rate", "precision", "context_efficiency", "noise_file_rate", "noise_region_rate", "ndcg_at_500", "first_useful_hit"]
    lines = [
        "# Phase 1 Rescored Policy Summary",
        "",
        f"- Rows: {summary['row_counts']['total']}",
        f"- Frozen-baseline fallback rows: {summary['row_counts']['fallback']}",
        f"- Treatment rows kept: {summary['row_counts']['treatment']}",
        f"- Gate counts: {summary['gate_counts']}",
        "",
        "## Full Scorecard",
        markdown_table(summary["paired_stats_all"], metrics),
        "",
        "## Advisory Rows Only",
        markdown_table(summary["paired_stats_advisory_only"], metrics) if summary["row_counts"]["treatment"] else "_No advisory rows._",
        "",
        "## Frozen Baseline Fallback Rows Only",
        markdown_table(summary["paired_stats_fallback_only"], metrics) if summary["row_counts"]["fallback"] else "_No fallback rows._",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--treatment", required=True, type=Path)
    parser.add_argument("--bench", required=True, type=Path)
    parser.add_argument("--repos", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--eval-bench", type=Path)
    parser.add_argument("--policy-name", default="codex-phase1-hybrid-policy")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    baseline_rows = load_rows(args.baseline)
    treatment_rows = load_rows(args.treatment)
    baseline_by_id = rows_by_id(baseline_rows)
    treatment_by_id = rows_by_id(treatment_rows)
    missing = sorted(set(baseline_by_id) ^ set(treatment_by_id))
    if missing:
        raise SystemExit(f"baseline/treatment instance mismatch: {missing[:8]}")

    policy_explorer = f"agent-contracts:{args.policy_name}"
    policy_rows = [
        policy_row(instance_id, baseline_by_id[instance_id], treatment_by_id[instance_id], policy_explorer=policy_explorer)
        for instance_id in baseline_by_id
    ]
    output_root = args.output_dir
    policy_dir = output_root / args.policy_name
    top5_path = policy_dir / "top5.jsonl"
    metrics_path = policy_dir / "top5.metrics.jsonl"
    comparison_dir = output_root / "comparison"
    write_rows(top5_path, policy_rows, include_metrics=False)
    records = swe.load_jsonl(args.bench)
    swe.add_metrics(args.bench, records, args.repos, policy_rows, eval_bench_path=args.eval_bench)
    write_rows(metrics_path, policy_rows, include_metrics=True)

    gate_counts: dict[str, int] = {}
    original_gate_counts: dict[str, int] = {}
    for row in treatment_rows:
        original_gate = gate_decision(row)
        original_gate_counts[original_gate] = original_gate_counts.get(original_gate, 0) + 1
        gate = effective_policy_gate(row)
        gate_counts[gate] = gate_counts.get(gate, 0) + 1
    fallback_rows = [row for row in policy_rows if row.get("metadata", {}).get("baseline_reused")]
    advisory_rows = [row for row in policy_rows if not row.get("metadata", {}).get("baseline_reused")]
    summary = {
        "inputs": {
            "baseline": args.baseline.as_posix(),
            "treatment": args.treatment.as_posix(),
            "benchmark": args.bench.as_posix(),
            "repos": args.repos.as_posix(),
        },
        "outputs": {
            "top5": top5_path.as_posix(),
            "top5_metrics": metrics_path.as_posix(),
            "paired_deltas_csv": (comparison_dir / "paired-deltas.csv").as_posix(),
            "paired_summary_json": (comparison_dir / "paired-summary.json").as_posix(),
            "paired_summary_md": (comparison_dir / "paired-summary.md").as_posix(),
        },
        "row_counts": {"total": len(policy_rows), "fallback": len(fallback_rows), "treatment": len(advisory_rows)},
        "gate_counts": gate_counts,
        "original_treatment_gate_counts": original_gate_counts,
        "metrics": {
            "baseline": mean_metrics(baseline_rows),
            "original_treatment": mean_metrics(treatment_rows),
            "rescored_policy": mean_metrics(policy_rows),
        },
        "paired_stats_all": paired_stats(baseline_rows, policy_rows),
        "paired_stats_advisory_only": paired_stats(baseline_rows, advisory_rows),
        "paired_stats_fallback_only": paired_stats(baseline_rows, fallback_rows),
        "no_harm": {
            "all_policy_rows": {
                "f1_score": no_harm_counts(baseline_rows, policy_rows, "f1_score"),
                "weighted_core_coverage": no_harm_counts(baseline_rows, policy_rows, "weighted_core_coverage"),
            },
            "advisory_rows_only": {
                "f1_score": no_harm_counts(baseline_rows, advisory_rows, "f1_score"),
                "weighted_core_coverage": no_harm_counts(baseline_rows, advisory_rows, "weighted_core_coverage"),
            },
            "frozen_baseline_fallback_rows_only": {
                "f1_score": no_harm_counts(baseline_rows, fallback_rows, "f1_score"),
                "weighted_core_coverage": no_harm_counts(baseline_rows, fallback_rows, "weighted_core_coverage"),
            },
        },
        "operational_guardrails": {
            "baseline": guardrails(baseline_rows),
            "original_treatment": guardrails(treatment_rows),
            "policy": guardrails(policy_rows),
            "policy_advisory_only": guardrails(advisory_rows),
            "policy_fallback_only": guardrails(fallback_rows),
        },
    }
    comparison_dir.mkdir(parents=True, exist_ok=True)
    write_paired_deltas(comparison_dir / "paired-deltas.csv", baseline_rows, treatment_rows, policy_rows)
    (comparison_dir / "paired-summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_summary_md(comparison_dir / "paired-summary.md", summary)
    print(json.dumps({"outputs": summary["outputs"], "row_counts": summary["row_counts"], "gate_counts": gate_counts}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
