<p align="center">
  <a href="https://github.com/aiswarya797/agent-contracts">
    <picture>
      <source srcset="assets/agent-contracts-wordmark-dark.svg" media="(prefers-color-scheme: dark)">
      <source srcset="assets/agent-contracts-wordmark-light.svg" media="(prefers-color-scheme: light)">
      <img src="assets/agent-contracts-wordmark-light.svg" alt="agent-contracts logo" width="520">
    </picture>
  </a>
</p>

<div align="center">
  <p><strong>Keep AI coding agents inside the shape of your repo.</strong></p>

  <p>Automatically generate module contracts so agents understand ownership, dependencies, and instructions before they edit.</p>

  <p>
    <a href="https://www.npmjs.com/package/agent-contracts-cli"><img alt="npm version" src="https://img.shields.io/npm/v/agent-contracts-cli.svg" /></a>
    <a href="https://www.npmjs.com/package/agent-contracts-cli"><img alt="npm downloads" src="https://img.shields.io/npm/dm/agent-contracts-cli.svg" /></a>
    <a href="https://github.com/aiswarya797/agent-contracts/releases"><img alt="GitHub release" src="https://img.shields.io/github/v/release/aiswarya797/agent-contracts.svg" /></a>
    <a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-blue.svg" /></a>
  </p>

  <p>
    <a href="#why-this-is-needed">Why this is needed</a> |
    <a href="#what-it-generates">What it generates</a> |
    <a href="#how-to-install">How to install</a> |
    <a href="#commands">Commands</a>
  </p>
</div>

---

`agent-contracts` turns the implicit rules of a codebase into local, reviewable files that both humans and AI coding agents can read before making changes.

> “Agent contracts are a progressive context layer on top of code evidence.”

Most repos already contain the truth, but it is scattered across source files, tests, package manifests, docs, conventions, and team memory. This tool scans that evidence and writes contracts that answer:

- Which part of the repo owns these files?
- What public behavior is promised here?
- Which dependencies are expected, and which ones look suspicious?
- Which tests or commands prove the change still works?
- Which local instructions should an agent read before touching code?

Code graphs are still useful for structural evidence. Agent contracts add the operating layer agents need before they edit: ownership, public contracts, allowed dependencies, forbidden internals, verification commands, drift checks, and bounded context loading.

It is local-first: no source upload, no hosted service, no dependency install inside the target repo, and no target-code execution during analysis.

## Why This Is Needed

AI agents are fast at patching symptoms. That is useful until the patch crosses a hidden boundary.

An agent can fix one failing test by importing a private helper, changing a public return shape, or editing a neighboring package it does not really own. The immediate task passes, but a different flow regresses later because the agent did not know the module contract, dependency rules, or verification path.

`agent-contracts` makes those expectations explicit before the next change:

- ownership boundaries are written down
- public behavior is listed in one place
- dependencies are compared against code evidence
- test evidence is attached to the contract
- agent instructions live next to the code they govern

The goal is not more documentation. The goal is fewer patch fixes that quietly damage the repo.

## What It Generates

When initialized in a target repository, `agent-contracts` can create:

- `ARCHITECTURE.md`: repo-wide boundaries and invariants.
- root `AGENTS.md`: repo-wide operating instructions for AI agents.
- local `SPEC.md`: ownership, public behavior, dependencies, acceptance criteria, and verification evidence.
- local `AGENTS.md`: instructions for safe agent work in that part of the repo.
- `.agent-contracts/module-map.json`: machine-readable ownership and dependency map.
- `.agent-contracts/context-packs/...`: bounded local bundles for focused agent sessions.

Generated files are plain Markdown and JSON. They are meant to be reviewed, edited, and committed like normal repo files.

## How To Install

Install from npm:

```bash
npm install -g agent-contracts-cli
```

Then run it inside any repository:

```bash
agent-contracts init --repo .
```

The package is named `agent-contracts-cli` because `agent-contracts` was already taken on npm. The installed command is still:

```bash
agent-contracts --help
```

You can also install directly from GitHub:

```bash
npm install -g github:aiswarya797/agent-contracts
```

Python 3.10+ is required. The npm package is a thin launcher around the bundled local Python analyzer.

## Commands

### 1. Initialize Contracts

Preview what would be generated:

```bash
agent-contracts init --repo .
```

Write only new files after review:

```bash
agent-contracts init --repo . --write --yes
```

Overwrite existing generated paths only when you explicitly ask:

```bash
agent-contracts init --repo . --write --yes --overwrite-existing
```

### 2. Discover And Read Progressive Context

Start with a compact catalog:

```bash
agent-contracts context-discover --repo .
```

Then read only the slice needed for the next step:

```bash
agent-contracts context-read billing --section summary --repo .
agent-contracts context-read billing --section tests --repo .
```

Supported read sections are `summary`, `contract`, `instructions`, `dependencies`, `tests`, `source-list`, and `drift`.

Build a bounded local bundle only when the agent needs working context for a task:

```bash
agent-contracts context-pack "fix payment status tests" --repo .
```

You can also target a module directly:

```bash
agent-contracts context-pack billing --repo .
```

Context packs include relevant contracts, instructions, owned source files, tests, and direct dependency contract summaries when present.

For issue-specific work, use the v2 localized flow before building a pack:

```bash
agent-contracts context-intent "fix payment status renewal handling"
agent-contracts context-localize "fix src/billing/api.py payment_status renewal handling" --repo .
agent-contracts context-read-region src/billing/api.py 20 60 --repo .
agent-contracts context-gate "fix src/billing/api.py payment_status renewal handling" --repo . --model-profile spark
agent-contracts context-pack-v2 "fix src/billing/api.py payment_status renewal handling" --repo . --model-profile spark
```

The v2 pack localizes task evidence to ranked files and line windows, compares it against baseline lexical retrieval, and emits `inject`, `advisory`, `tool_only`, or `abstain` gate decisions. Advisory packs tell agents to form an independent hypothesis before trusting localized evidence.

Progressive context commands reuse local caches. In git repositories they are stored under git-private storage at `.git/agent-contracts/cache/`; outside git they fall back to `.agent-contracts/cache/`. The module-map cache warms module detection for later `context-read` and `context-pack` calls. The v2 localized flow also caches search, symbol, and test indexes, so repeated `context-localize`, `context-read-region`, `context-expand`, `context-gate`, and `context-pack-v2` calls do not rebuild the same expensive repo indexes when files have not changed. Cache fingerprints include file paths, sizes, mtimes, roles, languages, schema versions, and algorithm versions. Set `AGENT_CONTRACTS_DISABLE_CACHE=1` to force fresh module maps and localization indexes.

### MCP Adapter

The package also ships a thin MCP stdio server:

```bash
agent-contracts-mcp
```

It exposes `context_discover`, `context_read`, `context_pack`, `context_verify`, and the v2 primitives `context_intent`, `context_localize`, `context_read_region`, `context_expand`, `context_gate`, `context_pack_v2`, and `context_explain` as adapters over the same local analyzer. The legacy intended flow is `context_discover` -> `context_read` -> `context_pack`; the issue-localized flow is `context_intent` -> `context_localize` -> `context_read_region` or `context_expand` -> `context_gate` -> `context_pack_v2`. There is no whole-repository read tool.

### 3. Verify Phase 1 Context Selection

Run the local deterministic verifier against the bundled fixture manifest:

```bash
agent-contracts verify-context validation/context-selection/manifest.jsonl --repo .
```

This verifies context selection only. It checks the shared context-pack planning path and compares deterministic context-selection strategies for expected source files, relevant tests, required contract context, and misleading or irrelevant local context. It does not directly invoke `context-discover` or `context-read`, execute fixture code, or measure real-agent task success.

The JSON report includes selected files as a trace, per-file classifications, target-file recall, relevant-test recall, required-context recall, selected bytes, irrelevant files, misleading files included, context bloat, first target rank, and aggregate metrics by strategy.

Use text output for a compact terminal summary:

```bash
agent-contracts verify-context validation/context-selection/manifest.jsonl --repo . --format text
```

### 4. Benchmark Context Strategies

Run the richer Phase 3 benchmark when you want comparative evidence across the verifier strategies plus graph-like and progressive MCP-equivalent baselines:

```bash
agent-contracts benchmark-context validation/context-selection/manifest.jsonl --repo .
```

The benchmark report includes aggregate and per-task metrics for `naive`, `module`, `module-no-contracts`, `graph-like`, and `progressive-mcp`, plus deltas such as module versus naive, module versus graph-like, and progressive-mcp versus graph-like.

Use text output for a compact thesis report:

```bash
agent-contracts benchmark-context validation/context-selection/manifest.jsonl --repo . --format text
```

### SWE-Explore Adapter

The standalone SWE-Explore bridge emits ranked line regions in the same JSONL shape as local SWE-Explore explorers. It uses only the base repo snapshot, issue text, agent-contracts analyzer output, and local contracts already present in the base repo; it does not read solution patches, trajectories, benchmark ground truth, or future repo state for selection.

Run the 5-instance smoke subset created by the local SWE-Explore checkout:

```bash
python scripts/swe_explore_agent_contracts.py \
  --bench /tmp/swe-explore-run/SWE-Explore-Bench/bench.subset5.jsonl \
  --repos /tmp/swe-explore-run/SWE-Explore-Bench \
  --issue-map /tmp/swe-explore-run/SWE-Explore-Bench/issue_map.json \
  --strategy contract-ranked \
  --top-k 5 \
  --output /tmp/swe-explore-run/SWE-Explore-Bench/results/agent_contracts_contract_ranked/top5.jsonl
```

Add `--evaluate` to attach SWE-Explore `ExploreEvaluator` metrics when `eval.py` is available next to the bench file:

```bash
python scripts/swe_explore_agent_contracts.py \
  --bench /tmp/swe-explore-run/SWE-Explore-Bench/bench.subset5.jsonl \
  --repos /tmp/swe-explore-run/SWE-Explore-Bench \
  --issue-map /tmp/swe-explore-run/SWE-Explore-Bench/issue_map.json \
  --strategy contract-ranked \
  --top-k 5 \
  --evaluate \
  --output /tmp/swe-explore-run/SWE-Explore-Bench/results/agent_contracts_contract_ranked/top5.jsonl
```

Supported adapter strategies are `module`, `graph-like`, `progressive-mcp`, `contract-ranked`, `context-localized`, and `beat-sota1`. The `contract-ranked` strategy resolves the best module, scores files with path, basename, public-surface, local contract, import/dependency, and test evidence, then ranks bounded line windows rather than emitting full-file regions. The `context-localized` strategy uses the issue-localized v2 localizer and includes no-harm gate metadata while preserving the same ranked-region output shape.

The `beat-sota1` strategy is the research path toward stronger SWE-Explore results. It builds a hybrid evidence index from issue text, paths, symbols, imports, source/test pairs, module contracts, local graph evidence, global code/test retrieval, and line-window scoring. When `scikit-learn` is available, it also adds a TF-IDF chunk retriever matching SWE-Explore's 80-line / 20-overlap baseline shape; otherwise it falls back to dependency-free retrieval. Use `--ablation no-contracts`, `--ablation no-graph`, `--ablation no-symbols`, `--ablation no-tests`, or `--ablation no-active` to measure which evidence source is actually helping.

On the local 76-instance SWE-Explore subset, the sklearn-enabled `beat-sota1` run narrowly beat the existing TF-IDF baseline: `weighted_core_coverage` 0.1086 vs 0.1066 and `f1_score` 0.0851 vs 0.0810. Treat this as a promising local result, not a full-benchmark SOTA claim.

```bash
python scripts/swe_explore_agent_contracts.py \
  --bench /tmp/swe-explore-run/SWE-Explore-Bench/bench.subset5.jsonl \
  --repos /tmp/swe-explore-run/SWE-Explore-Bench \
  --issue-map /tmp/swe-explore-run/SWE-Explore-Bench/issue_map.json \
  --strategy beat-sota1 \
  --top-k 5 \
  --evaluate \
  --output /tmp/swe-explore-run/SWE-Explore-Bench/results/beat_sota1/top5.jsonl
```

To test the actual agent-level thesis, use the Codex-backed conditions:

```bash
python scripts/swe_explore_agent_contracts.py \
  --bench /tmp/swe-explore-run/SWE-Explore-Bench/bench.subset5.jsonl \
  --repos /tmp/swe-explore-run/SWE-Explore-Bench \
  --issue-map /tmp/swe-explore-run/SWE-Explore-Bench/issue_map.json \
  --strategy codex-baseline \
  --top-k 5 \
  --evaluate \
  --output /tmp/swe-explore-run/SWE-Explore-Bench/results/codex_baseline/top5.jsonl
```

```bash
python scripts/swe_explore_agent_contracts.py \
  --bench /tmp/swe-explore-run/SWE-Explore-Bench/bench.subset5.jsonl \
  --repos /tmp/swe-explore-run/SWE-Explore-Bench \
  --issue-map /tmp/swe-explore-run/SWE-Explore-Bench/issue_map.json \
  --strategy codex-agent-contracts \
  --top-k 5 \
  --evaluate \
  --output /tmp/swe-explore-run/SWE-Explore-Bench/results/codex_agent_contracts/top5.jsonl
```

```bash
python scripts/swe_explore_agent_contracts.py \
  --bench /tmp/swe-explore-run/SWE-Explore-Bench/bench.subset5.jsonl \
  --repos /tmp/swe-explore-run/SWE-Explore-Bench \
  --issue-map /tmp/swe-explore-run/SWE-Explore-Bench/issue_map.json \
  --strategy codex-beat-sota1 \
  --top-k 5 \
  --evaluate \
  --output /tmp/swe-explore-run/SWE-Explore-Bench/results/codex_beat_sota1/top5.jsonl
```

```bash
python scripts/swe_explore_agent_contracts.py \
  --bench /tmp/swe-explore-run/SWE-Explore-Bench/bench.subset5.jsonl \
  --repos /tmp/swe-explore-run/SWE-Explore-Bench \
  --issue-map /tmp/swe-explore-run/SWE-Explore-Bench/issue_map.json \
  --strategy codex-beat-sota2 \
  --top-k 5 \
  --evaluate \
  --output /tmp/swe-explore-run/SWE-Explore-Bench/results/codex_beat_sota2/top5.jsonl
```

`codex-baseline` asks Codex to explore the repository and emit ranked regions directly. `codex-agent-contracts` first builds `contract-ranked` pre-context, includes the candidate files/line windows in the prompt, then asks Codex to emit final ranked regions. `codex-context-localized` gives Codex the v2 localized, gate-aware precontext and records the gate, selected bytes, selected regions, and noisy-path counts. `codex-beat-sota1` gives Codex compact, scored `beat-sota1` candidate regions and asks it to rerank, drop, or replace regions only when repository evidence supports the change. `codex-beat-sota2` keeps the deterministic scorer but filters noisy precontext, labels evidence strength, withholds low-signal snippets, and tells Codex to form independent hypotheses before comparing candidates. All conditions use the same JSONL output shape, so their results can be paired. For `--strategy context-localized` or `codex-context-localized`, pass `--model-profile spark`, `mini`, `frontier`, or `unknown` to exercise the same Agent Contracts gate limits used by the CLI/MCP flow; the selected profile is included in each output row's metadata.

On a fresh local paired 76-instance run, `codex-beat-sota1` improved F1 and recall over `codex-baseline` but regressed weighted core coverage, hit-file, hit-region, precision, and context efficiency. Treat this as a mixed research signal, not a SOTA result.

| Condition | F1 | Recall | Hit File | Hit Region | Weighted Core Coverage |
|---|---:|---:|---:|---:|---:|
| `codex-baseline` | 0.2282 | 0.1972 | 0.7789 | 0.6048 | 0.2214 |
| `codex-beat-sota1` | 0.2324 | 0.2000 | 0.7656 | 0.5985 | 0.2157 |
| `codex-beat-sota2` | 0.2373 | 0.2057 | 0.7704 | 0.6149 | 0.2410 |

On the same 76-instance set, `codex-beat-sota2` beats `codex-baseline` on F1 (+0.0090), recall (+0.0085), hit-region (+0.0101), and weighted core coverage (+0.0196). It still regresses precision (-0.0118), hit-file (-0.0086), and context efficiency (-0.0103), so the remaining work is to keep the WCC/F1 gains while recovering file breadth and efficiency.

The default command template is:

```bash
codex exec --sandbox read-only --output-last-message {response_file} -C {repo} -
```

Override it with `--codex-command` when using a profile, local provider, or alternate Codex model. The template supports `{repo}`, `{response_file}`, and `{instance_id}` placeholders.

#### Quick SWE/Spark Evaluation Runbook

Use this before any full SWE or SWE-Explore run. It creates a tiny filtered bench under `benchmark-results/quick-swe-spark/`, includes `psf__requests-5414` when that instance exists in your local bench, and prints commands without launching Codex unless `--run` is passed.

The dry-run JSON includes `instances` and `missing_requested_instances`. Check those before `--run`; pass `--fallback-limit 0` when you want the command to fail instead of filling from the first available bench row if a requested instance is missing.

Dry-run the exact commands:

```bash
python3 scripts/quick_swe_spark_eval.py \
  --bench /path/to/SWE-Explore-Bench/bench.jsonl \
  --repos /path/to/SWE-Explore-Bench \
  --issue-map /path/to/SWE-Explore-Bench/issue_map.json \
  --output-dir benchmark-results/quick-swe-spark
```

Run the small paired evaluation after reviewing the printed commands:

```bash
python3 scripts/quick_swe_spark_eval.py \
  --bench /path/to/SWE-Explore-Bench/bench.jsonl \
  --repos /path/to/SWE-Explore-Bench \
  --issue-map /path/to/SWE-Explore-Bench/issue_map.json \
  --output-dir benchmark-results/quick-swe-spark \
  --run
```

The default paired conditions are:

- `spark_baseline`: `codex-baseline` with no Agent Contracts precontext.
- `context_localized_preflight`: deterministic `context-localized --model-profile spark` for inspecting selected files, selected regions, selected bytes, gate decision, and noisy/vendored-path counts.
- `spark_context_localized`: `codex-context-localized --model-profile spark`, using the same Codex command as baseline plus the localized Agent Contracts precontext.

Add two to five targeted cases with repeated `--instance`, and add the previous Agent Contracts path with `--include-legacy`:

```bash
python3 scripts/quick_swe_spark_eval.py \
  --bench /path/to/SWE-Explore-Bench/bench.jsonl \
  --repos /path/to/SWE-Explore-Bench \
  --issue-map /path/to/SWE-Explore-Bench/issue_map.json \
  --instance psf__requests-5414 \
  --instance another__instance-123 \
  --include-legacy \
  --run
```

If your local Spark runner needs a specific command, pass it through the shared template:

```bash
python3 scripts/quick_swe_spark_eval.py \
  --bench /path/to/SWE-Explore-Bench/bench.jsonl \
  --repos /path/to/SWE-Explore-Bench \
  --issue-map /path/to/SWE-Explore-Bench/issue_map.json \
  --spark-codex-command 'codex exec --sandbox read-only --output-last-message {response_file} -C {repo} -' \
  --run
```

Inspect the diagnostic outputs before looking only at pass/fail:

```bash
jq -c '{instance_id, explorer, regions, selected_bytes: .metadata.selected_bytes, gate: .metadata.context_localized.gate, noisy_path_count: .metadata.context_localized.noisy_path_count, vendored_noisy_path_count: .metadata.context_localized.vendored_noisy_path_count}' benchmark-results/quick-swe-spark/context_localized_preflight/top5.jsonl

jq -c '{instance_id, explorer, num_regions, codex_returncode: .metadata.codex_returncode, gate: .metadata.precontext.gate, selected_bytes: .metadata.precontext.selected_bytes, risk_counts: .metadata.precontext.risk_counts, regions}' benchmark-results/quick-swe-spark/spark_context_localized/top5.jsonl

jq -c '{instance_id, explorer, num_regions, codex_returncode: .metadata.codex_returncode, runner_error: .metadata.runner_error, regions}' benchmark-results/quick-swe-spark/spark_baseline/top5.jsonl
```

If you run restricted repair after localization, inspect patch breadth and pass/fail together:

```bash
python3 scripts/swe_restricted_repair.py \
  --bench benchmark-results/quick-swe-spark/bench.quick-spark.jsonl \
  --repos /path/to/SWE-Explore-Bench \
  --issue-map benchmark-results/quick-swe-spark/issue_map.quick-spark.json \
  --predictions benchmark-results/quick-swe-spark/spark_context_localized/top5.jsonl \
  --mode subprocess \
  --repair-command 'your-spark-repair-command --repo {repo} --output {response_file}' \
  --output benchmark-results/quick-swe-spark/spark_context_localized/restricted-repair.jsonl

jq -c '{instance_id, resolved, files_edited_count, files_edited, patch_bytes, patch_lines, commands_run, failure_reason}' benchmark-results/quick-swe-spark/spark_context_localized/restricted-repair.jsonl
```

This quick run is intentionally not the full SWE bench. Keep benchmark JSONL, local repos, generated patches, context packs, and repair outputs under ignored paths such as `benchmark-results/`.

To test whether better context improves patch-style behavior, run restricted-context repair over any prediction file. Mock mode checks harness plumbing; subprocess mode should point at a fixed repair scaffold.

```bash
python scripts/swe_restricted_repair.py \
  --bench /tmp/swe-explore-run/SWE-Explore-Bench/bench.subset5.jsonl \
  --repos /tmp/swe-explore-run/SWE-Explore-Bench \
  --issue-map /tmp/swe-explore-run/SWE-Explore-Bench/issue_map.json \
  --predictions /tmp/swe-explore-run/SWE-Explore-Bench/results/beat_sota1/top5.jsonl \
  --mode mock \
  --output /tmp/swe-explore-run/SWE-Explore-Bench/results/beat_sota1/restricted-repair.jsonl
```

### 5. Trial Simulated Agent Behavior

Run the Phase 4 deterministic simulated agent trial when you want behavior-style metrics before live-agent evaluation:

```bash
agent-contracts trial-context validation/context-selection/manifest.jsonl --repo .
```

This is not a real LLM benchmark. It reuses the selected context for each strategy, then deterministically simulates read and edit decisions against manifest ground truth. The report includes files read, files edited, target and test reads, required context reads, misleading edits, read bytes, first target rank, trial success, failure reasons, and trace steps.

Use text output for a compact trial summary:

```bash
agent-contracts trial-context validation/context-selection/manifest.jsonl --repo . --format text
```

### 6. Run Real/Mock Agent Context Trials

Run the Phase 5 live-agent harness when you want to measure what an agent actually read, edited, and tested under each context strategy:

```bash
agent-contracts agent-trial-context validation/context-selection/manifest.jsonl --repo . --mode mock --format json
```

Mock mode is deterministic and requires no API credentials. Use repeated runs to prepare paired task/run comparisons:

```bash
agent-contracts agent-trial-context validation/context-selection/manifest.jsonl --repo . --mode mock --runs 2 --format text
```

Subprocess mode runs an external agent command in an isolated temporary copy of each fixture repository and passes the task, temp repo path, strategy, and strategy-selected context over JSON stdin:

```bash
agent-contracts agent-trial-context validation/context-selection/manifest.jsonl --repo . --mode subprocess --agent-command "codex exec --json"
```

The agent input does not include manifest scoring fields such as target files, relevant tests, required context, misleading files, or expected module. After each run, the harness scores observed reads, edits, commands, diffs, target recall, required-context recall, misleading reads/edits, and paired strategy deltas against the manifest ground truth.

Each raw run includes `task_id`, `strategy`, `run_index`, and a stable `run_id`. JSON output includes `statistical_analysis` with paired deltas, deterministic bootstrap confidence intervals, and an exact sign test over success wins/losses. Small samples are labeled as directional or insufficient so mock or low-N results are not presented as statistically significant.

For a vendor-neutral subprocess starting point, copy or adapt:

```bash
python3 scripts/agent_trial_runner_template.py --mode echo
```

The template reads harness JSON from stdin and returns the required JSON contract. Replace its adapter function with your real agent call when you are ready to run live trials.

External baselines such as semantic/code search tools can be added later by extending the strategy list and returning the same selected-context/run JSON shape. The bundled tests do not install or invoke any external semantic search provider.

Use text output for a compact live-trial summary:

```bash
agent-contracts agent-trial-context validation/context-selection/manifest.jsonl --repo . --mode mock --format text
```

### 7. Check For Drift

Compare contracts against current code evidence:

```bash
agent-contracts check --repo .
```

This reports issues such as undeclared dependencies, internal imports, missing contracts, missing agent instructions, uncovered files, and public surfaces missing from `SPEC.md`.

### 8. Refresh After Code Changes

Plan updates after the repo changes:

```bash
agent-contracts refresh --repo .
```

Apply safe instruction refreshes:

```bash
agent-contracts refresh --repo . --write-safe --yes
```

Apply contract-changing updates only after review:

```bash
agent-contracts refresh --repo . --write-safe --write-contract --yes
```

### 9. Inspect The Map

Show the detected ownership and dependency map without writing files:

```bash
agent-contracts map --repo .
```

### 10. Troubleshoot Setup

Use doctor when installation or repository readiness looks off:

```bash
agent-contracts doctor --repo .
```

Doctor checks plugin layout, Python availability, Git state, permissions, ignore rules, and whether the repo can be inventoried locally.
