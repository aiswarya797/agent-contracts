from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from scripts import agent_contracts, agent_contracts_mcp, swe_explore_agent_contracts


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "agent_contracts.py"
SWE_SCRIPT = ROOT / "scripts" / "swe_explore_agent_contracts.py"
REPAIR_SCRIPT = ROOT / "scripts" / "swe_restricted_repair.py"


def run_cli(*args: str, repo: Path | None = None) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, str(SCRIPT), *args]
    if repo is not None:
        command.extend(["--repo", str(repo)])
    return subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)


def fake_agent_command(name: str) -> str:
    return f"{shlex.quote(sys.executable)} {shlex.quote(str(ROOT / 'tests' / 'fixtures' / name))}"


def write_tiny_swe_explore_fixture(base: Path) -> tuple[Path, Path, Path, Path]:
    repo = base / "repos" / "demo__repo-1"
    source = repo / "src" / "billing" / "api.py"
    tests = repo / "tests" / "test_billing.py"
    source.parent.mkdir(parents=True)
    tests.parent.mkdir(parents=True)
    source.write_text(
        "\n".join(
            [
                "from auth.token import validate_token",
                "",
                "class PaymentStatus:",
                "    pass",
                "",
                "def helper():",
                "    return 'unchanged'",
                "",
                "def payment_status(user_token):",
                "    token_state = validate_token(user_token)",
                "    if token_state.renewal_required:",
                "        return 'renewal-required'",
                "    return 'paid'",
                "",
                "def unrelated_report():",
                "    return 'ok'",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (repo / "src" / "auth").mkdir(parents=True)
    (repo / "src" / "auth" / "token.py").write_text(
        "def validate_token(value):\n    return value\n",
        encoding="utf-8",
    )
    tests.write_text(
        "\n".join(
            [
                "from billing.api import payment_status",
                "",
                "def test_payment_status_renewal_required():",
                "    assert payment_status('needs-renewal') == 'renewal-required'",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (repo / "README.md").write_text("Billing service demo.\n", encoding="utf-8")

    bench = base / "bench.jsonl"
    bench.write_text(
        json.dumps(
            {
                "instance_id": "demo__repo-1",
                "repo_dir": "repos/demo__repo-1",
                "ground_truth": {
                    "read_core_files": ["src/billing/ground_truth_only.py"],
                    "read_core_regions": [{"path": "src/billing/ground_truth_only.py", "start": 1, "end": 2}],
                    "read_optional_files_map": {},
                    "read_optional_regions_map": {},
                    "main_files": ["src/billing/ground_truth_only.py"],
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    issue_map = base / "issue_map.json"
    issue_map.write_text(
        json.dumps(
            {
                "demo__repo-1": "Payment status should validate the user token and report renewal-required state."
            }
        ),
        encoding="utf-8",
    )
    output = base / "results.jsonl"
    return bench, base, issue_map, output


class AgentContractsTests(unittest.TestCase):
    def test_doctor_reports_plugin_ready(self) -> None:
        result = run_cli("doctor", "--format", "json", repo=ROOT)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data["summary"]["blockers"], 0)
        self.assertTrue(data["local_only"])

    def test_utc_now_uses_python_310_compatible_timezone(self) -> None:
        self.assertRegex(agent_contracts.utc_now(), r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

    def test_map_detects_python_modules(self) -> None:
        result = run_cli("map", "--format", "json", repo=ROOT / "fixtures" / "python-service")
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        modules = {module["name"]: module for module in data["modules"]}
        names = set(modules)
        self.assertIn("billing", names)
        self.assertIn("auth", names)
        self.assertIn("tests/test_billing.py", modules["billing"]["test_files"])

    def test_map_detects_mixed_monorepo_dependency(self) -> None:
        result = run_cli("map", "--format", "json", repo=ROOT / "fixtures" / "mixed-monorepo")
        self.assertEqual(result.returncode, 0, result.stderr)
        modules = {module["name"]: module for module in json.loads(result.stdout)["modules"]}
        self.assertIn("shared", modules["web"]["dependencies"])
        self.assertIn("tests/web.test.ts", modules["web"]["test_files"])

    def test_map_deduplicates_ambiguous_module_names(self) -> None:
        result = run_cli("map", "--format", "json", repo=ROOT / "fixtures" / "ambiguous-modules")
        self.assertEqual(result.returncode, 0, result.stderr)
        modules = json.loads(result.stdout)["modules"]
        names = [module["name"] for module in modules]
        roots = {module["root"] for module in modules}
        self.assertEqual(len(names), len(set(names)))
        self.assertIn("apps/api", roots)
        self.assertIn("packages/api", roots)

    def test_check_reports_broken_fixture_drift(self) -> None:
        result = run_cli("check", "--format", "json", repo=ROOT / "fixtures" / "broken-drift")
        self.assertEqual(result.returncode, 0, result.stderr)
        codes = {finding["code"] for finding in json.loads(result.stdout)["findings"]}
        self.assertIn("internal-import", codes)
        self.assertIn("undeclared-dependency", codes)
        self.assertIn("undeclared-public-surface", codes)

    def test_init_writes_new_contract_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "repo"
            shutil.copytree(ROOT / "fixtures" / "python-service", target)
            result = run_cli("init", "--write", "--yes", repo=target)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((target / "ARCHITECTURE.md").exists())
            self.assertTrue((target / "AGENTS.md").exists())
            self.assertTrue((target / "src" / "billing" / "SPEC.md").exists())
            self.assertTrue((target / ".agent-contracts" / "module-map.json").exists())

    def test_init_preserves_existing_docs_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "repo"
            shutil.copytree(ROOT / "fixtures" / "existing-docs", target)
            original_agents = (target / "AGENTS.md").read_text(encoding="utf-8")
            original_architecture = (target / "ARCHITECTURE.md").read_text(encoding="utf-8")

            result = run_cli("init", "--write", "--yes", repo=target)
            self.assertEqual(result.returncode, 0, result.stderr)

            self.assertEqual((target / "AGENTS.md").read_text(encoding="utf-8"), original_agents)
            self.assertEqual((target / "ARCHITECTURE.md").read_text(encoding="utf-8"), original_architecture)
            self.assertTrue((target / "src" / "catalog" / "SPEC.md").exists())
            self.assertTrue((target / "src" / "catalog" / "AGENTS.md").exists())

    def test_init_overwrites_existing_docs_only_when_requested(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "repo"
            shutil.copytree(ROOT / "fixtures" / "existing-docs", target)

            result = run_cli("init", "--write", "--yes", "--overwrite-existing", repo=target)
            self.assertEqual(result.returncode, 0, result.stderr)

            self.assertIn("Generated by agent-contracts", (target / "AGENTS.md").read_text(encoding="utf-8"))
            self.assertIn("Generated by agent-contracts", (target / "ARCHITECTURE.md").read_text(encoding="utf-8"))

    def test_context_pack_writes_bounded_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "repo"
            shutil.copytree(ROOT / "fixtures" / "python-service", target)
            init_result = run_cli("init", "--write", "--yes", repo=target)
            self.assertEqual(init_result.returncode, 0, init_result.stderr)
            output = Path(tmp) / "pack"
            result = run_cli("context-pack", "billing", "--output", str(output), repo=target)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((output / "README.md").exists())
            manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["module"]["name"], "billing")
            self.assertIn("src/billing/api.py", manifest["included_files"])

    def test_context_discover_json_includes_compact_catalog(self) -> None:
        result = run_cli("context-discover", "--format", "json", repo=ROOT / "fixtures" / "python-service")
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        modules = {module["name"]: module for module in data["modules"]}
        billing = modules["billing"]
        self.assertEqual(billing["root"], "src/billing")
        self.assertEqual(billing["kind"], "component")
        self.assertIn("billing-module", billing["capabilities"])
        self.assertIn("auth", billing["dependencies"])
        self.assertIn("tests/test_billing.py", billing["tests"])
        self.assertIn("confidence", billing)
        self.assertIn("estimated_context_size", billing)
        self.assertIsInstance(billing["estimated_context_size"]["bytes"], int)
        self.assertNotIn("owned_files", billing)

    def test_module_map_cache_is_reused_and_invalidated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "repo"
            shutil.copytree(ROOT / "fixtures" / "python-service", target)
            agent_contracts.clear_module_map_cache()

            first = agent_contracts.build_module_map(target)
            cache_path = agent_contracts.module_map_cache_path(target)
            self.assertTrue(cache_path.exists())

            with mock.patch.object(
                agent_contracts,
                "build_module_map_from_files",
                side_effect=AssertionError("expected cached module map"),
            ):
                cached = agent_contracts.build_module_map(target)
            self.assertEqual(cached["modules"], first["modules"])

            api_path = target / "src" / "billing" / "api.py"
            api_path.write_text(api_path.read_text(encoding="utf-8") + "\n# cache invalidation\n", encoding="utf-8")

            with mock.patch.object(
                agent_contracts,
                "build_module_map_from_files",
                wraps=agent_contracts.build_module_map_from_files,
            ) as builder:
                refreshed = agent_contracts.build_module_map(target)
            self.assertGreater(builder.call_count, 0)
            self.assertGreaterEqual({module["name"] for module in refreshed["modules"]}, {"auth", "billing"})

    def test_module_map_cache_stays_out_of_git_worktree_status(self) -> None:
        if not agent_contracts.git_available():
            self.skipTest("git is not available")
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "repo"
            shutil.copytree(ROOT / "fixtures" / "python-service", target)
            self.assertTrue(agent_contracts.initialize_trial_git_repo(target))
            agent_contracts.clear_module_map_cache()

            agent_contracts.build_module_map(target)

            cache_path = agent_contracts.module_map_cache_path(target)
            self.assertTrue(cache_path.is_file())
            self.assertTrue(cache_path.is_relative_to(target.resolve() / ".git"))
            self.assertEqual(agent_contracts.git_changed_files(target), [])

    def test_mcp_context_read_reuses_discover_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "repo"
            shutil.copytree(ROOT / "fixtures" / "python-service", target)
            agent_contracts.clear_module_map_cache()

            discover = agent_contracts_mcp.call_tool("context_discover", {"repo": str(target)})
            self.assertGreaterEqual(discover["summary"]["module_count"], 2)

            with mock.patch.object(
                agent_contracts,
                "build_module_map_from_files",
                side_effect=AssertionError("expected MCP context_read to reuse cached map"),
            ):
                payload = agent_contracts_mcp.call_tool(
                    "context_read",
                    {"repo": str(target), "target": "billing", "section": "summary"},
                )
            self.assertEqual(payload["module"], "billing")

    def test_context_read_json_sections(self) -> None:
        repo = ROOT / "fixtures" / "python-service"

        summary = run_cli("context-read", "billing", "--section", "summary", "--format", "json", repo=repo)
        self.assertEqual(summary.returncode, 0, summary.stderr)
        summary_data = json.loads(summary.stdout)
        self.assertEqual(summary_data["module"], "billing")
        self.assertIn("function payment_status", summary_data["public_surfaces"])

        tests = run_cli("context-read", "billing", "--section", "tests", "--format", "json", repo=repo)
        self.assertEqual(tests.returncode, 0, tests.stderr)
        self.assertEqual(json.loads(tests.stdout)["tests"], ["tests/test_billing.py"])

        dependencies = run_cli("context-read", "billing", "--section", "dependencies", "--format", "json", repo=repo)
        self.assertEqual(dependencies.returncode, 0, dependencies.stderr)
        deps = json.loads(dependencies.stdout)["dependencies"]
        self.assertEqual([dep["name"] for dep in deps], ["auth"])
        self.assertEqual(deps[0]["imports"][0]["source"], "src/billing/api.py")

        source_list = run_cli("context-read", "billing", "--section", "source-list", "--format", "json", repo=repo)
        self.assertEqual(source_list.returncode, 0, source_list.stderr)
        sources = json.loads(source_list.stdout)["source_files"]
        self.assertIn("src/billing/api.py", sources)
        self.assertNotIn("src/auth/token.py", sources)

    def test_context_read_reports_unknown_module_and_section(self) -> None:
        repo = ROOT / "fixtures" / "python-service"

        unknown_module = run_cli("context-read", "missing-module", "--section", "summary", repo=repo)
        self.assertEqual(unknown_module.returncode, 2)
        self.assertIn("Unknown module or target", unknown_module.stderr)

        unknown_section = run_cli("context-read", "billing", "--section", "not-a-section", repo=repo)
        self.assertEqual(unknown_section.returncode, 2)
        self.assertIn("invalid choice", unknown_section.stderr)

    def test_context_pack_excludes_unrelated_sibling_modules(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "repo"
            shutil.copytree(ROOT / "fixtures" / "python-service", target)
            analytics_dir = target / "src" / "analytics"
            analytics_dir.mkdir()
            (analytics_dir / "metrics.py").write_text("def summarize() -> int:\n    return 1\n", encoding="utf-8")

            init_result = run_cli("init", "--write", "--yes", repo=target)
            self.assertEqual(init_result.returncode, 0, init_result.stderr)

            output = Path(tmp) / "pack"
            result = run_cli("context-pack", "billing", "--output", str(output), repo=target)
            self.assertEqual(result.returncode, 0, result.stderr)

            manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
            included = set(manifest["included_files"])
            self.assertIn("src/billing/api.py", included)
            self.assertIn("src/auth/SPEC.md", included)
            self.assertNotIn("src/analytics/metrics.py", included)
            self.assertNotIn("src/analytics/SPEC.md", included)

    def test_context_selection_manifest_loads(self) -> None:
        rows = agent_contracts.load_context_selection_manifest(ROOT / "validation" / "context-selection" / "manifest.jsonl")
        self.assertGreaterEqual(len(rows), 4)
        first = rows[0]
        self.assertEqual(first["task_id"], "python-service-billing-status")
        self.assertEqual(first["repo_path"], "fixtures/python-service")
        self.assertIn("src/billing/api.py", first["target_files"])
        contract_guided = next(row for row in rows if row["task_id"] == "contract-guided-payments-refund")
        self.assertIn("src/payments/SPEC.md", contract_guided["required_context"])
        self.assertIn("src/ledger/SPEC.md", contract_guided["required_context"])

    def test_readme_describes_verifier_scope_accurately(self) -> None:
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("shared context-pack planning path", text)
        self.assertIn("does not directly invoke `context-discover` or `context-read`", text)
        self.assertNotIn("checks whether `context-discover`, `context-read`", text)

    def test_scores_selected_files_against_manifest_sets(self) -> None:
        repo = ROOT / "fixtures" / "noisy-context"
        row = {
            "task_id": "score-test",
            "expected_module": "payments",
            "target_files": ["src/payments/refund.py"],
            "relevant_tests": ["tests/test_payments_refund.py"],
            "allowed_context": ["src/payments/__init__.py"],
            "misleading_files": ["docs/refunds.md"],
        }
        selected = [
            agent_contracts.ContextFile("src/payments/refund.py", "source"),
            agent_contracts.ContextFile("tests/test_payments_refund.py", "test"),
            agent_contracts.ContextFile("src/payments/__init__.py", "source"),
            agent_contracts.ContextFile("docs/refunds.md", "docs"),
            agent_contracts.ContextFile("docs/deploy.md", "docs"),
        ]
        selection = {
            "files": selected,
            "included_files": [item.path for item in selected],
            "omitted_files": [],
            "selected_bytes": sum((repo / item.path).stat().st_size for item in selected),
            "resolved_module": "payments",
        }

        score = agent_contracts.score_selected_context_files(repo, row, "unit", selection)

        classifications = {item["path"]: item["classification"] for item in score["file_classifications"]}
        self.assertEqual(classifications["src/payments/refund.py"], "target")
        self.assertEqual(classifications["tests/test_payments_refund.py"], "relevant_test")
        self.assertEqual(classifications["src/payments/__init__.py"], "allowed_context")
        self.assertEqual(classifications["docs/refunds.md"], "misleading")
        self.assertEqual(classifications["docs/deploy.md"], "irrelevant")
        self.assertEqual(score["target_file_recall"], 1.0)
        self.assertEqual(score["relevant_tests_found"], 1.0)
        self.assertEqual(score["misleading_files_included"], 1)
        self.assertEqual(score["irrelevant_files_read"], 1)
        self.assertFalse(score["passed"])

    def test_expected_module_match_affects_module_strategy_pass_fail(self) -> None:
        repo = ROOT / "fixtures" / "noisy-context"
        row = {
            "task_id": "module-match-test",
            "expected_module": "payments",
            "target_files": ["src/payments/refund.py"],
            "relevant_tests": ["tests/test_payments_refund.py"],
            "allowed_context": [],
            "misleading_files": [],
        }
        selected = [
            agent_contracts.ContextFile("src/payments/refund.py", "source"),
            agent_contracts.ContextFile("tests/test_payments_refund.py", "test"),
        ]
        selection = {
            "files": selected,
            "included_files": [item.path for item in selected],
            "omitted_files": [],
            "selected_bytes": sum((repo / item.path).stat().st_size for item in selected),
            "resolved_module": "legacy",
        }

        module_score = agent_contracts.score_selected_context_files(repo, row, "module", selection)
        self.assertFalse(module_score["expected_module_match"])
        self.assertFalse(module_score["passed"])

        naive_selection = dict(selection)
        naive_selection["resolved_module"] = None
        naive_score = agent_contracts.score_selected_context_files(repo, row, "naive", naive_selection)
        self.assertIsNone(naive_score["expected_module_match"])
        self.assertTrue(naive_score["passed"])

    def test_verify_context_json_cli(self) -> None:
        result = run_cli("verify-context", "validation/context-selection/manifest.jsonl", "--format", "json", repo=ROOT)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data["schema_version"], agent_contracts.CONTEXT_VERIFIER_SCHEMA_VERSION)
        self.assertIn("module", data["aggregate"])
        rows = agent_contracts.load_context_selection_manifest(ROOT / "validation" / "context-selection" / "manifest.jsonl")
        self.assertEqual(data["aggregate"]["module"]["task_count"], len(rows))
        self.assertEqual(data["aggregate"]["module"]["pass_rate"], 1.0)
        self.assertLess(data["aggregate"]["module-no-contracts"]["pass_rate"], data["aggregate"]["module"]["pass_rate"])
        contract_guided = next(task for task in data["tasks"] if task["task_id"] == "contract-guided-payments-refund")
        self.assertFalse(contract_guided["strategies"]["module-no-contracts"]["passed"])

    def test_module_strategy_beats_naive_on_noisy_fixture(self) -> None:
        report = agent_contracts.run_context_verification(
            ROOT / "validation" / "context-selection" / "manifest.jsonl",
            ROOT,
            max_files=80,
            max_bytes=700_000,
        )
        noisy = next(task for task in report["tasks"] if task["task_id"] == "noisy-payments-refund")
        naive = noisy["strategies"]["naive"]
        module = noisy["strategies"]["module"]

        self.assertGreater(naive["misleading_files_included"], module["misleading_files_included"])
        self.assertGreater(naive["irrelevant_files_read"], module["irrelevant_files_read"])
        self.assertGreater(naive["context_bloat"], module["context_bloat"])

    def test_contract_guided_fixture_proves_contract_context_beats_source_only_module(self) -> None:
        report = agent_contracts.run_context_verification(
            ROOT / "validation" / "context-selection" / "manifest.jsonl",
            ROOT,
            max_files=80,
            max_bytes=700_000,
        )
        task = next(item for item in report["tasks"] if item["task_id"] == "contract-guided-payments-refund")
        naive = task["strategies"]["naive"]
        module = task["strategies"]["module"]
        no_contracts = task["strategies"]["module-no-contracts"]

        self.assertTrue(module["passed"])
        self.assertFalse(no_contracts["passed"])
        self.assertEqual(module["required_context_found"], 1.0)
        self.assertEqual(no_contracts["required_context_found"], 0.0)
        self.assertIn("src/payments/SPEC.md", module["required_context_files_found"])
        self.assertIn("src/payments/AGENTS.md", module["required_context_files_found"])
        self.assertIn("src/ledger/SPEC.md", module["required_context_files_found"])
        self.assertIn("src/legacy/refunds.py", naive["misleading_file_paths"])
        self.assertGreater(naive["misleading_files_included"], module["misleading_files_included"])

    def test_graph_like_strategy_returns_plausible_bounded_selection_and_trace(self) -> None:
        rows = agent_contracts.load_context_selection_manifest(ROOT / "validation" / "context-selection" / "manifest.jsonl")
        row = next(item for item in rows if item["task_id"] == "noisy-payments-refund")
        repo = ROOT / row["repo_path"]

        selection = agent_contracts.select_context_files_for_strategy(
            repo,
            row,
            "graph-like",
            max_files=80,
            max_bytes=700_000,
        )

        self.assertLessEqual(len(selection["included_files"]), 80)
        self.assertLessEqual(selection["selected_bytes"], 700_000)
        self.assertIn("src/payments/refund.py", selection["included_files"])
        self.assertIn("tests/test_payments_refund.py", selection["included_files"])
        self.assertTrue(selection["trace"])
        self.assertTrue(all(item["strategy"] == "graph-like" for item in selection["trace"]))
        self.assertTrue(any("task-keyword-match" in ",".join(item["reasons"]) for item in selection["trace"]))

    def test_progressive_mcp_strategy_emits_discover_read_pack_steps(self) -> None:
        rows = agent_contracts.load_context_selection_manifest(ROOT / "validation" / "context-selection" / "manifest.jsonl")
        row = next(item for item in rows if item["task_id"] == "contract-guided-payments-refund")
        repo = ROOT / row["repo_path"]

        selection = agent_contracts.select_context_files_for_strategy(
            repo,
            row,
            "progressive-mcp",
            max_files=80,
            max_bytes=700_000,
        )

        tool_steps = [item for item in selection["trace"] if "tool" in item]
        tools = [item["tool"] for item in tool_steps]
        self.assertEqual(tools[0], "context_discover")
        self.assertIn("context_read", tools)
        self.assertEqual(tools[-1], "context_pack")
        read_sections = {item["section"] for item in tool_steps if item["tool"] == "context_read"}
        self.assertGreaterEqual(read_sections, {"summary", "contract", "tests", "dependencies"})

    def test_benchmark_context_json_cli_includes_all_strategies(self) -> None:
        result = run_cli("benchmark-context", "validation/context-selection/manifest.jsonl", "--format", "json", repo=ROOT)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        expected = set(agent_contracts.CONTEXT_BENCHMARK_STRATEGIES)
        self.assertEqual(data["schema_version"], agent_contracts.CONTEXT_BENCHMARK_SCHEMA_VERSION)
        self.assertEqual({item["name"] for item in data["strategies"]}, expected)
        self.assertEqual(set(data["aggregate"]), expected)
        self.assertTrue(all(set(task["strategies"]) == expected for task in data["tasks"]))

    def test_benchmark_comparison_metrics_are_calculated_correctly(self) -> None:
        report = agent_contracts.run_context_benchmark(
            ROOT / "validation" / "context-selection" / "manifest.jsonl",
            ROOT,
            max_files=80,
            max_bytes=700_000,
        )
        module = report["aggregate"]["module"]
        naive = report["aggregate"]["naive"]
        deltas = report["comparisons"]["aggregate"]["module_vs_naive"]["deltas"]

        self.assertEqual(deltas["pass_rate"], round(module["pass_rate"] - naive["pass_rate"], 6))
        self.assertEqual(deltas["selected_bytes_savings"], round(naive["selected_bytes"] - module["selected_bytes"], 6))
        self.assertEqual(
            deltas["misleading_files_reduction"],
            round(naive["misleading_files_included"] - module["misleading_files_included"], 6),
        )

    def test_benchmark_module_still_beats_naive_on_noisy_fixture(self) -> None:
        report = agent_contracts.run_context_benchmark(
            ROOT / "validation" / "context-selection" / "manifest.jsonl",
            ROOT,
            max_files=80,
            max_bytes=700_000,
        )
        noisy = next(task for task in report["tasks"] if task["task_id"] == "noisy-payments-refund")
        naive = noisy["strategies"]["naive"]
        module = noisy["strategies"]["module"]

        self.assertFalse(naive["passed"])
        self.assertTrue(module["passed"])
        self.assertGreater(naive["misleading_files_included"], module["misleading_files_included"])
        self.assertGreater(naive["context_bloat"], module["context_bloat"])

    def test_benchmark_module_beats_no_contracts_on_contract_guided_fixture(self) -> None:
        report = agent_contracts.run_context_benchmark(
            ROOT / "validation" / "context-selection" / "manifest.jsonl",
            ROOT,
            max_files=80,
            max_bytes=700_000,
        )
        task = next(item for item in report["tasks"] if item["task_id"] == "contract-guided-payments-refund")
        module = task["strategies"]["module"]
        no_contracts = task["strategies"]["module-no-contracts"]

        self.assertTrue(module["passed"])
        self.assertFalse(no_contracts["passed"])
        self.assertGreater(module["required_context_recall"], no_contracts["required_context_recall"])

    def test_benchmark_compares_module_and_progressive_against_graph_like(self) -> None:
        report = agent_contracts.run_context_benchmark(
            ROOT / "validation" / "context-selection" / "manifest.jsonl",
            ROOT,
            max_files=80,
            max_bytes=700_000,
        )
        aggregate = report["comparisons"]["aggregate"]

        self.assertIn("module_vs_graph_like", aggregate)
        self.assertIn("progressive_mcp_vs_graph_like", aggregate)
        self.assertGreater(aggregate["module_vs_graph_like"]["deltas"]["pass_rate"], 0)
        self.assertGreater(aggregate["progressive_mcp_vs_graph_like"]["deltas"]["required_context_recall"], 0)

    def test_benchmark_json_output_has_stable_machine_readable_shape(self) -> None:
        result = run_cli("benchmark-context", "validation/context-selection/manifest.jsonl", "--format", "json", repo=ROOT)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)

        self.assertRegex(data["created_at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        self.assertEqual(data["limits"], {"max_files": 80, "max_bytes": 700_000})
        self.assertEqual([item["name"] for item in data["strategies"]], list(agent_contracts.CONTEXT_BENCHMARK_STRATEGIES))
        self.assertIn("aggregate", data["comparisons"])
        self.assertIn("tasks", data["comparisons"])
        self.assertIn("module_vs_naive", data["comparisons"]["aggregate"])

    def test_trial_context_json_cli_includes_all_strategies(self) -> None:
        result = run_cli("trial-context", "validation/context-selection/manifest.jsonl", "--format", "json", repo=ROOT)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        expected = set(agent_contracts.CONTEXT_TRIAL_STRATEGIES)

        self.assertEqual(data["schema_version"], agent_contracts.CONTEXT_TRIAL_SCHEMA_VERSION)
        self.assertEqual(data["trial_mode"], "deterministic-simulated-agent-trial")
        self.assertFalse(data["limits"]["live_llm_calls"])
        self.assertEqual({item["name"] for item in data["strategies"]}, expected)
        self.assertEqual(set(data["aggregate"]), expected)
        self.assertTrue(all(set(task["strategies"]) == expected for task in data["tasks"]))
        self.assertIn("module_vs_naive", data["comparisons"]["aggregate"])

    def test_trial_context_text_output_summarizes_outcomes_and_deltas(self) -> None:
        result = run_cli("trial-context", "validation/context-selection/manifest.jsonl", "--format", "text", repo=ROOT)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("deterministic simulated agent trial", result.stdout)
        self.assertIn("## Aggregate By Strategy", result.stdout)
        self.assertIn("## Per Task Trial Outcomes", result.stdout)
        self.assertIn("## Key Deltas", result.stdout)
        self.assertIn("progressive-mcp vs graph-like", result.stdout)

    def test_trial_rules_capture_misleading_edit_and_required_context_failures(self) -> None:
        report = agent_contracts.run_context_trial(
            ROOT / "validation" / "context-selection" / "manifest.jsonl",
            ROOT,
            max_files=80,
            max_bytes=700_000,
        )
        noisy = next(task for task in report["tasks"] if task["task_id"] == "noisy-payments-refund")
        contract_guided = next(task for task in report["tasks"] if task["task_id"] == "contract-guided-payments-refund")

        self.assertFalse(noisy["strategies"]["naive"]["trial_success"])
        self.assertIn("misleading-file-edited", noisy["strategies"]["naive"]["failure_reason"])
        self.assertGreater(noisy["strategies"]["graph-like"]["misleading_files_edited"], 0)

        no_contracts = contract_guided["strategies"]["module-no-contracts"]
        self.assertFalse(no_contracts["trial_success"])
        self.assertIn("missing-required-context", no_contracts["failure_reason"])
        self.assertEqual(no_contracts["required_context_read"], 0)
        self.assertGreater(no_contracts["misleading_files_edited"], 0)

        self.assertFalse(contract_guided["strategies"]["graph-like"]["trial_success"])
        self.assertTrue(contract_guided["strategies"]["progressive-mcp"]["trial_success"])
        self.assertEqual(contract_guided["strategies"]["progressive-mcp"]["required_context_read"], 3)

    def test_trial_aggregate_comparisons_show_progressive_beats_graph_like(self) -> None:
        report = agent_contracts.run_context_trial(
            ROOT / "validation" / "context-selection" / "manifest.jsonl",
            ROOT,
            max_files=80,
            max_bytes=700_000,
        )
        aggregate = report["aggregate"]
        comparison = report["comparisons"]["aggregate"]["progressive_mcp_vs_graph_like"]["deltas"]

        self.assertEqual(aggregate["module"]["trial_success_rate"], 1.0)
        self.assertEqual(aggregate["progressive-mcp"]["trial_success_rate"], 1.0)
        self.assertGreater(comparison["trial_success_rate"], 0)
        self.assertGreater(comparison["misleading_files_edited_reduction"], 0)

    def test_agent_trial_context_mock_json_output_includes_all_strategies(self) -> None:
        result = run_cli(
            "agent-trial-context",
            "validation/context-selection/manifest.jsonl",
            "--mode",
            "mock",
            "--format",
            "json",
            repo=ROOT,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        expected = set(agent_contracts.AGENT_CONTEXT_TRIAL_STRATEGIES)

        self.assertEqual(data["schema_version"], agent_contracts.AGENT_CONTEXT_TRIAL_SCHEMA_VERSION)
        self.assertEqual(data["mode"], "mock")
        self.assertEqual(data["runs"], 1)
        self.assertEqual({item["name"] for item in data["strategies"]}, expected)
        self.assertEqual(set(data["aggregate"]), expected)
        self.assertTrue(all(set(task["strategies"]) == expected for task in data["tasks"]))
        self.assertEqual(len(data["raw_runs"]), len(data["tasks"]) * len(expected))
        self.assertIn("progressive_mcp_vs_naive", data["comparisons"]["aggregate"])
        self.assertIn("statistical_analysis", data)

    def test_agent_trial_context_mock_text_output_summarizes_results(self) -> None:
        result = run_cli(
            "agent-trial-context",
            "validation/context-selection/manifest.jsonl",
            "--mode",
            "mock",
            "--format",
            "text",
            repo=ROOT,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("real/simulated agent context-recall trial", result.stdout)
        self.assertIn("Warning: mock mode", result.stdout)
        self.assertIn("## Aggregate Success By Strategy", result.stdout)
        self.assertIn("## Statistical Significance", result.stdout)
        self.assertIn("## Per Task Result Summary", result.stdout)
        self.assertIn("## Key Deltas", result.stdout)
        self.assertIn("progressive-mcp vs graph-like", result.stdout)
        self.assertIn("module vs module-no-contracts", result.stdout)
        self.assertIn("directional_only", result.stdout)

    def test_agent_trial_runs_two_repeated_isolated_runs_per_task_strategy(self) -> None:
        rows = agent_contracts.load_context_selection_manifest(ROOT / "validation" / "context-selection" / "manifest.jsonl")
        report = agent_contracts.run_agent_context_trial(
            ROOT / "validation" / "context-selection" / "manifest.jsonl",
            ROOT,
            strategies=("module", "progressive-mcp"),
            runs=2,
            max_files=80,
            max_bytes=700_000,
            timeout_seconds=120,
            mode="mock",
            agent_command=None,
        )

        self.assertEqual(len(report["raw_runs"]), len(rows) * 2 * 2)
        self.assertEqual({run["run_index"] for run in report["raw_runs"]}, {1, 2})
        self.assertEqual(len({run["run_id"] for run in report["raw_runs"]}), len(report["raw_runs"]))
        self.assertTrue(all(run["run_id"].endswith(f"::{run['run_index']}") for run in report["raw_runs"]))
        by_task_strategy: dict[tuple[str, str], list[int]] = {}
        temp_paths: set[str] = set()
        for run in report["raw_runs"]:
            by_task_strategy.setdefault((run["task_id"], run["strategy"]), []).append(run["run_index"])
            temp_paths.add(run["temp_repo_path"])
            self.assertIn("selected_context", run)
            self.assertIn("agent_reported_files_read_paths", run)
            self.assertIn("git_diff", run)
        self.assertTrue(all(sorted(indices) == [1, 2] for indices in by_task_strategy.values()))
        self.assertEqual(len(temp_paths), len(report["raw_runs"]))

    def test_agent_trial_statistical_analysis_json_shape_for_runs_two(self) -> None:
        result = run_cli(
            "agent-trial-context",
            "validation/context-selection/manifest.jsonl",
            "--mode",
            "mock",
            "--runs",
            "2",
            "--format",
            "json",
            repo=ROOT,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        stats = data["statistical_analysis"]["comparisons"]

        self.assertIn("module_vs_naive", stats)
        progressive = stats["progressive_mcp_vs_naive"]
        self.assertEqual(progressive["n_pairs"], len(data["tasks"]) * 2)
        self.assertIn("success_rate_delta", progressive)
        self.assertIn("misleading_read_delta", progressive)
        self.assertIn("misleading_edit_delta", progressive)
        self.assertIn("files_read_delta", progressive)
        self.assertIn("read_bytes_delta", progressive)
        self.assertIn("required_context_recall_delta", progressive)
        self.assertIn("target_read_recall_delta", progressive)
        self.assertIn("confidence_intervals", progressive)
        self.assertIn("success_rate_delta", progressive["confidence_intervals"])
        self.assertIn(progressive["sample_label"], {"directional_only", "statistical_test_ready"})

    def test_agent_trial_bootstrap_ci_is_deterministic(self) -> None:
        first = agent_contracts.bootstrap_mean_ci([1.0, 0.0, -1.0, 1.0], seed=12345, iterations=200)
        second = agent_contracts.bootstrap_mean_ci([1.0, 0.0, -1.0, 1.0], seed=12345, iterations=200)

        self.assertEqual(first, second)
        self.assertEqual(first["seed"], 12345)
        self.assertLessEqual(first["lower"], first["upper"])

    def test_agent_trial_sign_test_and_small_sample_labels(self) -> None:
        self.assertEqual(agent_contracts.exact_two_sided_sign_test_p_value(3, 1), 0.625)
        self.assertIsNone(agent_contracts.exact_two_sided_sign_test_p_value(0, 0))
        self.assertEqual(agent_contracts.sample_strength_label(1, 1), "insufficient_sample")
        self.assertEqual(agent_contracts.sample_strength_label(4, 4), "directional_only")
        self.assertEqual(agent_contracts.significance_label(0.01, 4, 4), "directional_only")

    def test_agent_trial_does_not_mutate_original_fixtures(self) -> None:
        fixture_file = ROOT / "fixtures" / "noisy-context" / "src" / "payments" / "refund.py"
        before = fixture_file.read_text(encoding="utf-8")

        result = run_cli(
            "agent-trial-context",
            "validation/context-selection/manifest.jsonl",
            "--mode",
            "mock",
            "--strategies",
            "naive,module",
            "--runs",
            "2",
            "--format",
            "json",
            repo=ROOT,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(fixture_file.read_text(encoding="utf-8"), before)
        self.assertNotIn("agent-trial mock edit", before)

    def test_agent_trial_input_omits_manifest_ground_truth_keys(self) -> None:
        rows = agent_contracts.load_context_selection_manifest(ROOT / "validation" / "context-selection" / "manifest.jsonl")
        row = next(item for item in rows if item["task_id"] == "contract-guided-payments-refund")
        repo = ROOT / row["repo_path"]
        selection = agent_contracts.select_context_files_for_strategy(
            repo,
            row,
            "progressive-mcp",
            max_files=80,
            max_bytes=700_000,
        )

        payload = agent_contracts.build_agent_trial_input(
            repo,
            row,
            "progressive-mcp",
            selection,
            run_index=1,
            max_files=80,
            max_bytes=700_000,
        )
        serialized = json.dumps(payload)

        for forbidden in [
            "target_files",
            "relevant_tests",
            "required_context",
            "misleading_files",
            "expected_module",
            "verification_command",
        ]:
            self.assertNotIn(f'"{forbidden}"', serialized)

    def test_agent_trial_runner_template_echo_mode_returns_valid_json(self) -> None:
        payload = {
            "repo_path": str(ROOT / "fixtures" / "python-service"),
            "task": "Update billing payment status behavior and the billing tests.",
            "strategy": "module",
            "available_context": {
                "selected_files": [
                    {"path": "src/billing/api.py", "role": "source"},
                    {"path": "tests/test_billing.py", "role": "test"},
                ]
            },
        }
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "agent_trial_runner_template.py"), "--mode", "echo"],
            cwd=ROOT,
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data["files_read"], ["src/billing/api.py", "tests/test_billing.py"])
        self.assertEqual(data["files_edited"], [])
        self.assertEqual(data["final_status"], "success")
        self.assertTrue(data["commands_run"])

    def test_agent_trial_progressive_trace_contains_mcp_equivalent_steps(self) -> None:
        report = agent_contracts.run_agent_context_trial(
            ROOT / "validation" / "context-selection" / "manifest.jsonl",
            ROOT,
            strategies=("progressive-mcp",),
            runs=1,
            max_files=80,
            max_bytes=700_000,
            timeout_seconds=120,
            mode="mock",
            agent_command=None,
        )
        run = next(item for item in report["raw_runs"] if item["task_id"] == "contract-guided-payments-refund")
        operations = [step["operation"] for step in run["trace"]]

        self.assertIn("context_discover", operations)
        self.assertIn("context_read", operations)
        self.assertIn("context_pack", operations)
        self.assertLess(operations.index("context_discover"), operations.index("context_read"))
        self.assertLess(operations.index("context_read"), operations.index("context_pack"))

    def test_agent_trial_contract_guided_mock_scores_contract_strategies(self) -> None:
        report = agent_contracts.run_agent_context_trial(
            ROOT / "validation" / "context-selection" / "manifest.jsonl",
            ROOT,
            strategies=agent_contracts.AGENT_CONTEXT_TRIAL_STRATEGIES,
            runs=1,
            max_files=80,
            max_bytes=700_000,
            timeout_seconds=120,
            mode="mock",
            agent_command=None,
        )
        runs = {
            item["strategy"]: item
            for item in report["raw_runs"]
            if item["task_id"] == "contract-guided-payments-refund"
        }

        self.assertTrue(runs["module"]["agent_success"])
        self.assertTrue(runs["progressive-mcp"]["agent_success"])
        self.assertFalse(runs["module-no-contracts"]["agent_success"])
        self.assertIn("missing-required-context", runs["module-no-contracts"]["failure_reason"])
        self.assertGreater(
            runs["module"]["required_context_recall_from_reads"],
            runs["module-no-contracts"]["required_context_recall_from_reads"],
        )

    def test_agent_trial_broad_mock_strategies_read_and_edit_misleading_files(self) -> None:
        report = agent_contracts.run_agent_context_trial(
            ROOT / "validation" / "context-selection" / "manifest.jsonl",
            ROOT,
            strategies=agent_contracts.AGENT_CONTEXT_TRIAL_STRATEGIES,
            runs=1,
            max_files=80,
            max_bytes=700_000,
            timeout_seconds=120,
            mode="mock",
            agent_command=None,
        )
        noisy_naive = next(
            item
            for item in report["raw_runs"]
            if item["task_id"] == "noisy-payments-refund" and item["strategy"] == "naive"
        )
        contract_graph = next(
            item
            for item in report["raw_runs"]
            if item["task_id"] == "contract-guided-payments-refund" and item["strategy"] == "graph-like"
        )

        self.assertGreater(noisy_naive["misleading_files_read"], 0)
        self.assertGreater(noisy_naive["misleading_files_edited"], 0)
        self.assertGreater(contract_graph["misleading_files_read"], 0)
        self.assertGreater(contract_graph["misleading_files_edited"], 0)

    def test_agent_trial_subprocess_valid_json_mode(self) -> None:
        result = run_cli(
            "agent-trial-context",
            "validation/context-selection/manifest.jsonl",
            "--mode",
            "subprocess",
            "--agent-command",
            fake_agent_command("fake_agent_valid.py"),
            "--strategies",
            "module",
            "--format",
            "json",
            repo=ROOT,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)

        self.assertEqual(data["mode"], "subprocess")
        self.assertEqual(set(data["aggregate"]), {"module"})
        self.assertTrue(all(run["returncode"] == 0 for run in data["raw_runs"]))
        self.assertTrue(all(run["agent_success"] for run in data["raw_runs"]))

    def test_agent_trial_subprocess_invalid_json_is_scored_as_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            one_row_manifest = Path(tmp) / "manifest.jsonl"
            first_row = (ROOT / "validation" / "context-selection" / "manifest.jsonl").read_text(encoding="utf-8").splitlines()[0]
            one_row_manifest.write_text(first_row + "\n", encoding="utf-8")

            result = run_cli(
                "agent-trial-context",
                str(one_row_manifest),
                "--mode",
                "subprocess",
                "--agent-command",
                fake_agent_command("fake_agent_invalid.py"),
                "--strategies",
                "module",
                "--format",
                "json",
                repo=ROOT,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        run = json.loads(result.stdout)["raw_runs"][0]
        self.assertFalse(run["agent_success"])
        self.assertIn("invalid-json-output", run["runner_failure_reason"])
        self.assertIn("not-json", run["stdout"])

    def test_agent_trial_subprocess_timeout_is_scored_as_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            one_row_manifest = Path(tmp) / "manifest.jsonl"
            first_row = (ROOT / "validation" / "context-selection" / "manifest.jsonl").read_text(encoding="utf-8").splitlines()[0]
            one_row_manifest.write_text(first_row + "\n", encoding="utf-8")

            result = run_cli(
                "agent-trial-context",
                str(one_row_manifest),
                "--mode",
                "subprocess",
                "--agent-command",
                fake_agent_command("fake_agent_sleep.py"),
                "--strategies",
                "module",
                "--timeout-seconds",
                "1",
                "--format",
                "json",
                repo=ROOT,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        run = json.loads(result.stdout)["raw_runs"][0]
        self.assertFalse(run["agent_success"])
        self.assertEqual(run["runner_failure_reason"], "agent-timeout")

    def test_context_pack_and_verifier_use_same_file_planning_helper(self) -> None:
        rows = agent_contracts.load_context_selection_manifest(ROOT / "validation" / "context-selection" / "manifest.jsonl")
        row = next(item for item in rows if item["task_id"] == "python-service-billing-status")
        repo = ROOT / row["repo_path"]

        verifier_selection = agent_contracts.select_context_files_for_strategy(
            repo,
            row,
            "module",
            max_files=80,
            max_bytes=700_000,
        )
        pack_plan = agent_contracts.plan_context_pack_files(
            repo,
            row["task"],
            max_files=80,
            max_bytes=700_000,
        )

        self.assertEqual(verifier_selection["included_files"], pack_plan["included_files"])

    def test_mcp_tool_registration(self) -> None:
        tools = agent_contracts_mcp.tool_definitions()
        names = {tool["name"] for tool in tools}
        self.assertEqual(
            names,
            {"context_discover", "context_read", "context_pack", "context_verify"},
        )
        self.assertNotIn("read_whole_repo", names)

        response = agent_contracts_mcp.handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        self.assertIsNotNone(response)
        self.assertEqual(response["result"]["tools"][0]["name"], "context_discover")

    def test_mcp_context_discover_smoke(self) -> None:
        payload = agent_contracts_mcp.call_tool(
            "context_discover",
            {"repo": str(ROOT / "fixtures" / "python-service")},
        )
        modules = {module["name"] for module in payload["modules"]}
        self.assertIn("billing", modules)

    def test_mcp_context_read_smoke(self) -> None:
        payload = agent_contracts_mcp.call_tool(
            "context_read",
            {"repo": str(ROOT / "fixtures" / "python-service"), "target": "billing", "section": "tests"},
        )
        self.assertEqual(payload["module"], "billing")
        self.assertEqual(payload["tests"], ["tests/test_billing.py"])

    def test_mcp_context_pack_smoke(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "repo"
            shutil.copytree(ROOT / "fixtures" / "python-service", target)
            payload = agent_contracts_mcp.call_tool(
                "context_pack",
                {"repo": str(target), "task": "billing", "max_files": 20, "max_bytes": 100_000},
            )

            self.assertEqual(payload["module"]["name"], "billing")
            self.assertTrue(Path(payload["manifest_path"]).exists())
            selected_paths = {item["path"] for item in payload["selected_files"]}
            self.assertIn("src/billing/api.py", selected_paths)
            self.assertNotIn("content", payload["selected_files"][0])

    def test_mcp_context_verify_smoke(self) -> None:
        payload = agent_contracts_mcp.call_tool(
            "context_verify",
            {
                "repo": str(ROOT),
                "manifest": "validation/context-selection/manifest.jsonl",
                "max_files": 80,
                "max_bytes": 700_000,
            },
        )
        self.assertEqual(payload["aggregate"]["module"]["pass_rate"], 1.0)
        contract_guided = next(task for task in payload["tasks"] if task["task_id"] == "contract-guided-payments-refund")
        self.assertTrue(contract_guided["strategies"]["module"]["passed"])
        self.assertFalse(contract_guided["strategies"]["module-no-contracts"]["passed"])

    def test_verify_context_reports_bad_manifest_and_unknown_fixture(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            malformed = Path(tmp) / "malformed.jsonl"
            malformed.write_text('{"task_id": "bad"}\n', encoding="utf-8")
            malformed_result = run_cli("verify-context", str(malformed), "--format", "json", repo=ROOT)
            self.assertEqual(malformed_result.returncode, 2)
            self.assertIn("missing required field", malformed_result.stderr)

            unknown = Path(tmp) / "unknown.jsonl"
            unknown.write_text(
                json.dumps(
                    {
                        "task_id": "missing-fixture",
                        "repo_path": "fixtures/does-not-exist",
                        "task": "Find missing fixture files.",
                        "target_files": ["src/missing.py"],
                        "relevant_tests": [],
                        "allowed_context": [],
                        "misleading_files": [],
                        "expected_module": "missing",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            unknown_result = run_cli("verify-context", str(unknown), "--format", "json", repo=ROOT)
            self.assertEqual(unknown_result.returncode, 2)
            self.assertIn("repo_path", unknown_result.stderr)

    def test_swe_explore_adapter_contract_ranked_outputs_valid_regions(self) -> None:
        self.assertEqual(
            swe_explore_agent_contracts.windows_for_line_count(10, line_window=6, line_overlap=2),
            [(1, 6), (5, 10)],
        )
        with tempfile.TemporaryDirectory() as tmp:
            bench, repos, issue_map, output = write_tiny_swe_explore_fixture(Path(tmp))

            result = subprocess.run(
                [
                    sys.executable,
                    str(SWE_SCRIPT),
                    "--bench",
                    str(bench),
                    "--repos",
                    str(repos),
                    "--issue-map",
                    str(issue_map),
                    "--strategy",
                    "contract-ranked",
                    "--top-k",
                    "2",
                    "--line-window",
                    "6",
                    "--line-overlap",
                    "2",
                    "--output",
                    str(output),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            summary = json.loads(result.stdout)
            self.assertEqual(summary["written"], 1)
            rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(rows), 1)
            row = rows[0]
            self.assertEqual(row["instance_id"], "demo__repo-1")
            self.assertEqual(row["explorer"], "agent-contracts:contract-ranked")
            self.assertLessEqual(row["num_regions"], 2)
            self.assertEqual(row["num_regions"], len(row["regions"]))
            self.assertIn("metadata", row)
            self.assertIn("included_files", row["metadata"])
            self.assertIn("trace", row["metadata"])
            self.assertTrue(any(item.get("operation") == "region_rank" for item in row["metadata"]["trace"]))

            repo = repos / "repos" / "demo__repo-1"
            for region in row["regions"]:
                path = repo / region["path"]
                self.assertTrue(path.is_file(), region)
                total_lines = len(path.read_text(encoding="utf-8").splitlines())
                self.assertGreaterEqual(region["start"], 1)
                self.assertLessEqual(region["start"], region["end"])
                self.assertLessEqual(region["end"], total_lines)
                self.assertLessEqual(region["end"] - region["start"] + 1, 6)

            serialized = json.dumps(row)
            self.assertNotIn("ground_truth_only.py", serialized)
            self.assertTrue(
                any(region["path"] == "src/billing/api.py" for region in row["regions"]),
                row["regions"],
            )

    def test_swe_explore_adapter_beat_sota1_outputs_scored_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bench, repos, issue_map, output = write_tiny_swe_explore_fixture(Path(tmp))

            result = subprocess.run(
                [
                    sys.executable,
                    str(SWE_SCRIPT),
                    "--bench",
                    str(bench),
                    "--repos",
                    str(repos),
                    "--issue-map",
                    str(issue_map),
                    "--strategy",
                    "beat-sota1",
                    "--top-k",
                    "3",
                    "--line-window",
                    "4",
                    "--line-overlap",
                    "1",
                    "--beat-sota1-regions-per-file",
                    "2",
                    "--output",
                    str(output),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            row = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(row["explorer"], "agent-contracts:beat-sota1")
            self.assertLessEqual(row["num_regions"], 3)
            self.assertTrue(any(region["path"] == "src/billing/api.py" for region in row["regions"]))
            self.assertIn("beat_sota1", row["metadata"])
            self.assertTrue(row["metadata"]["beat_sota1"]["candidate_files"])
            self.assertTrue(
                any(item.get("operation") == "active_expansion" for item in row["metadata"]["trace"]),
                row["metadata"]["trace"],
            )

    def test_swe_explore_adapter_beat_sota1_ablation_disables_expansion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bench, repos, issue_map, output = write_tiny_swe_explore_fixture(Path(tmp))

            result = subprocess.run(
                [
                    sys.executable,
                    str(SWE_SCRIPT),
                    "--bench",
                    str(bench),
                    "--repos",
                    str(repos),
                    "--issue-map",
                    str(issue_map),
                    "--strategy",
                    "beat-sota1",
                    "--top-k",
                    "2",
                    "--line-window",
                    "6",
                    "--line-overlap",
                    "2",
                    "--ablation",
                    "no-active",
                    "--output",
                    str(output),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            row = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(row["metadata"]["limits"]["ablations"], ["no-active"])
            self.assertFalse(any(item.get("operation") == "active_expansion" for item in row["metadata"]["trace"]))

    def test_swe_restricted_repair_mock_consumes_prediction_regions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            bench, repos, issue_map, predictions = write_tiny_swe_explore_fixture(tmp_path)
            repair_output = tmp_path / "repair.jsonl"
            predictions.write_text(
                json.dumps(
                    {
                        "instance_id": "demo__repo-1",
                        "explorer": "agent-contracts:beat-sota1",
                        "regions": [{"path": "src/billing/api.py", "start": 9, "end": 12}],
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    sys.executable,
                    str(REPAIR_SCRIPT),
                    "--bench",
                    str(bench),
                    "--repos",
                    str(repos),
                    "--issue-map",
                    str(issue_map),
                    "--predictions",
                    str(predictions),
                    "--output",
                    str(repair_output),
                    "--mode",
                    "mock",
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            summary = json.loads(result.stdout)
            self.assertEqual(summary["written"], 1)
            self.assertEqual(summary["resolved"], 1)
            row = json.loads(repair_output.read_text(encoding="utf-8"))
            self.assertEqual(row["prediction_explorer"], "agent-contracts:beat-sota1")
            self.assertTrue(row["resolved"])
            self.assertEqual(row["region_count"], 1)
            self.assertGreater(row["context_bytes"], 0)

    def test_swe_explore_adapter_output_order_is_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bench, repos, issue_map, first_output = write_tiny_swe_explore_fixture(Path(tmp))
            second_output = Path(tmp) / "results-second.jsonl"
            common_args = [
                sys.executable,
                str(SWE_SCRIPT),
                "--bench",
                str(bench),
                "--repos",
                str(repos),
                "--issue-map",
                str(issue_map),
                "--strategy",
                "contract-ranked",
                "--top-k",
                "3",
                "--line-window",
                "6",
                "--line-overlap",
                "2",
            ]

            first = subprocess.run(
                [*common_args, "--output", str(first_output)],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            second = subprocess.run(
                [*common_args, "--output", str(second_output)],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(
                first_output.read_text(encoding="utf-8"),
                second_output.read_text(encoding="utf-8"),
            )

    def test_swe_explore_codex_agent_contracts_uses_precontext_and_parses_regions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            bench, repos, issue_map, output = write_tiny_swe_explore_fixture(tmp_path)
            prompt_log = tmp_path / "prompt.txt"
            fake_codex = tmp_path / "fake_codex.py"
            fake_codex.write_text(
                "\n".join(
                    [
                        "import json",
                        "import pathlib",
                        "import sys",
                        "prompt = sys.stdin.read()",
                        "pathlib.Path(sys.argv[1]).write_text(prompt, encoding='utf-8')",
                        "print('```json')",
                        "print(json.dumps({'regions': [{'path': 'src/billing/api.py', 'start': 9, 'end': 12, 'reason': 'payment status logic'}]}))",
                        "print('```')",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            command = f"{shlex.quote(sys.executable)} {shlex.quote(str(fake_codex))} {shlex.quote(str(prompt_log))}"

            result = subprocess.run(
                [
                    sys.executable,
                    str(SWE_SCRIPT),
                    "--bench",
                    str(bench),
                    "--repos",
                    str(repos),
                    "--issue-map",
                    str(issue_map),
                    "--strategy",
                    "codex-agent-contracts",
                    "--top-k",
                    "2",
                    "--line-window",
                    "6",
                    "--line-overlap",
                    "2",
                    "--codex-command",
                    command,
                    "--codex-timeout",
                    "5",
                    "--output",
                    str(output),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            summary = json.loads(result.stdout)
            self.assertEqual(summary["written"], 1)
            prompt = prompt_log.read_text(encoding="utf-8")
            self.assertIn("agent-contracts pre-context", prompt)
            self.assertIn("Payment status should validate", prompt)

            row = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(row["explorer"], "agent-contracts:codex-agent-contracts")
            self.assertEqual(row["regions"], [{"path": "src/billing/api.py", "start": 9, "end": 12}])
            self.assertEqual(row["metadata"]["condition"], "codex-agent-contracts")
            self.assertIn("agent_contracts_precontext", row["metadata"]["allowed_inputs"])
            self.assertIsNone(row["metadata"]["runner_error"])
            self.assertTrue(row["metadata"]["precontext"]["regions"])

    def test_swe_explore_codex_beat_sota1_uses_compact_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            bench, repos, issue_map, output = write_tiny_swe_explore_fixture(tmp_path)
            prompt_log = tmp_path / "prompt.txt"
            fake_codex = tmp_path / "fake_codex.py"
            fake_codex.write_text(
                "\n".join(
                    [
                        "import json",
                        "import pathlib",
                        "import sys",
                        "prompt = sys.stdin.read()",
                        "pathlib.Path(sys.argv[1]).write_text(prompt, encoding='utf-8')",
                        "print(json.dumps({'regions': [{'path': 'src/billing/api.py', 'start': 9, 'end': 12, 'reason': 'reranked candidate'}]}))",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            command = f"{shlex.quote(sys.executable)} {shlex.quote(str(fake_codex))} {shlex.quote(str(prompt_log))}"

            result = subprocess.run(
                [
                    sys.executable,
                    str(SWE_SCRIPT),
                    "--bench",
                    str(bench),
                    "--repos",
                    str(repos),
                    "--issue-map",
                    str(issue_map),
                    "--strategy",
                    "codex-beat-sota1",
                    "--top-k",
                    "2",
                    "--line-window",
                    "6",
                    "--line-overlap",
                    "2",
                    "--beat-sota1-precontext-candidates",
                    "4",
                    "--codex-command",
                    command,
                    "--codex-timeout",
                    "5",
                    "--output",
                    str(output),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            prompt = prompt_log.read_text(encoding="utf-8")
            self.assertIn("beatSOTA1 candidates", prompt)
            self.assertIn("score=", prompt)
            row = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(row["explorer"], "agent-contracts:codex-beat-sota1")
            self.assertEqual(row["regions"], [{"path": "src/billing/api.py", "start": 9, "end": 12}])
            self.assertIn("beat_sota1_precontext", row["metadata"]["allowed_inputs"])
            self.assertTrue(row["metadata"]["precontext"]["beat_sota1"]["candidate_files"])

    def test_swe_explore_codex_beat_sota2_uses_advisory_gated_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            bench, repos, issue_map, output = write_tiny_swe_explore_fixture(tmp_path)
            prompt_log = tmp_path / "prompt.txt"
            fake_codex = tmp_path / "fake_codex.py"
            fake_codex.write_text(
                "\n".join(
                    [
                        "import json",
                        "import pathlib",
                        "import sys",
                        "prompt = sys.stdin.read()",
                        "pathlib.Path(sys.argv[1]).write_text(prompt, encoding='utf-8')",
                        "print(json.dumps({'regions': [{'path': 'src/billing/api.py', 'start': 9, 'end': 12, 'reason': 'independent hypothesis checked'}]}))",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            command = f"{shlex.quote(sys.executable)} {shlex.quote(str(fake_codex))} {shlex.quote(str(prompt_log))}"

            result = subprocess.run(
                [
                    sys.executable,
                    str(SWE_SCRIPT),
                    "--bench",
                    str(bench),
                    "--repos",
                    str(repos),
                    "--issue-map",
                    str(issue_map),
                    "--strategy",
                    "codex-beat-sota2",
                    "--top-k",
                    "2",
                    "--line-window",
                    "6",
                    "--line-overlap",
                    "2",
                    "--beat-sota1-precontext-candidates",
                    "4",
                    "--codex-command",
                    command,
                    "--codex-timeout",
                    "5",
                    "--output",
                    str(output),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            prompt = prompt_log.read_text(encoding="utf-8")
            self.assertIn("beatSOTA2 advisory evidence", prompt)
            self.assertIn("Independent-first workflow", prompt)
            self.assertIn("Candidate evidence is advisory", prompt)
            self.assertNotIn("primary evidence set", prompt)
            row = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(row["explorer"], "agent-contracts:codex-beat-sota2")
            self.assertEqual(row["regions"], [{"path": "src/billing/api.py", "start": 9, "end": 12}])
            self.assertIn("beat_sota2_precontext", row["metadata"]["allowed_inputs"])
            self.assertEqual(row["metadata"]["precontext"]["strategy"], "beat-sota2")
            self.assertIn("confidence", row["metadata"]["precontext"])

    def test_node_launcher_skips_unsupported_python3_when_supported_python_exists(self) -> None:
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not available")

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            old_log = tmp_path / "old-python-ran.txt"
            selected_log = tmp_path / "selected-python-args.txt"
            fake_python3 = tmp_path / "python3"
            fake_python = tmp_path / "python"

            fake_python3.write_text(
                "\n".join(
                    [
                        f"#!{sys.executable}",
                        "import pathlib",
                        "import sys",
                        "if sys.argv[1:2] == ['-c']:",
                        "    print('3.9.10')",
                        "    raise SystemExit(0)",
                        f"pathlib.Path({str(old_log)!r}).write_text('unsupported python3 ran\\n', encoding='utf-8')",
                        "raise SystemExit(77)",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            fake_python.write_text(
                "\n".join(
                    [
                        f"#!{sys.executable}",
                        "import pathlib",
                        "import sys",
                        "if sys.argv[1:2] == ['-c']:",
                        "    print('3.10.14')",
                        "    raise SystemExit(0)",
                        f"pathlib.Path({str(selected_log)!r}).write_text('\\n'.join(sys.argv[1:]) + '\\n', encoding='utf-8')",
                        "print('selected-python')",
                        "raise SystemExit(0)",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            fake_python3.chmod(0o755)
            fake_python.chmod(0o755)

            env = dict(os.environ)
            env["PATH"] = str(tmp_path)
            result = subprocess.run(
                [node, str(ROOT / "bin" / "agent-contracts.js"), "--help"],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
                env=env,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("selected-python", result.stdout)
            self.assertFalse(old_log.exists(), "unsupported python3 should be probed but not used to run the CLI")
            selected_args = selected_log.read_text(encoding="utf-8").splitlines()
            self.assertEqual(selected_args[0], str(SCRIPT))
            self.assertEqual(selected_args[1], "--help")


if __name__ == "__main__":
    unittest.main()
