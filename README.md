<p align="center">
  <picture>
    <source srcset="assets/agent-contracts-wordmark-dark.svg" media="(prefers-color-scheme: dark)">
    <source srcset="assets/agent-contracts-wordmark-light.svg" media="(prefers-color-scheme: light)">
    <img src="assets/agent-contracts-wordmark-light.svg" alt="agent-contracts">
  </picture>
</p>

<p align="center">Local module contracts for AI coding agents.</p>

<p align="center">
  <a href="#quick-start">Quick Start</a> |
  <a href="#commands">Commands</a> |
  <a href="#current-capabilities">Capabilities</a> |
  <a href="#safety-model">Safety</a>
</p>

`agent-contracts` is a local Claude Code plugin for creating and maintaining module-level contracts for AI coding agents.

It helps a repository answer five practical questions before an agent edits code:

- Which logical module owns these files?
- What public behavior does the module promise?
- Which dependencies are expected or suspicious?
- Which tests or commands verify the module?
- Which local instructions should an agent read before working here?

The plugin is local-first. It reads repository files on your machine, writes Markdown and JSON artifacts only after approval, and does not require a cloud service, external API key, dependency installation, CI setup, or source upload.

## What It Generates

When initialized in a target repository, the plugin can create:

- `ARCHITECTURE.md`: repository-wide module boundaries and invariants.
- root `AGENTS.md`: repo-wide operating instructions for coding agents.
- module `SPEC.md`: durable ownership, dependency, public-surface, and verification contract.
- module `AGENTS.md`: local instructions for safe agent work inside the module.
- `.agent-contracts/module-map.json`: machine-readable module graph.
- `.agent-contracts/context-packs/...`: local, bounded bundles for scoped agent sessions.

Transient context packs and cache files should stay local. The durable contracts and module map are intended to be reviewed and committed when your team is happy with them.

## Repository Layout

This plugin repo contains:

- `.claude-plugin/plugin.json`: plugin metadata.
- `commands/`: Claude slash-command wrappers.
- `skills/`: Claude skills used by the command wrappers.
- `scripts/agent_contracts.py`: the local analyzer and generator.
- `fixtures/`: small repositories used for validation.
- `tests/`: unit tests for analyzer behavior.
- `examples/`: generated output examples.

## Install

Use this repository as a local Claude Code plugin. The metadata lives in `.claude-plugin/plugin.json`; command wrappers live in `commands/`; skill instructions live in `skills/`.

You can also run the analyzer directly without Claude:

```bash
python3 scripts/agent_contracts.py doctor --repo /path/to/repo
python3 scripts/agent_contracts.py map --repo /path/to/repo
```

Python 3.10+ is required. The analyzer uses the Python standard library only.

## Quick Start

From the repository you want to analyze:

```bash
# 1. Check local readiness.
python3 /path/to/agent-contracts/scripts/agent_contracts.py doctor --repo .

# 2. Preview detected modules. This writes nothing.
python3 /path/to/agent-contracts/scripts/agent_contracts.py map --repo .

# 3. Preview generated contract files. This writes nothing.
python3 /path/to/agent-contracts/scripts/agent_contracts.py init --repo .

# 4. After reviewing the plan, write only new files.
python3 /path/to/agent-contracts/scripts/agent_contracts.py init --repo . --write --yes

# 5. Check for contract drift later.
python3 /path/to/agent-contracts/scripts/agent_contracts.py check --repo .
```

Existing files are skipped unless you explicitly pass `--overwrite-existing`.

## Commands

### `/contract-doctor`

Checks whether the local plugin and target repository are ready.

Direct script:

```bash
python3 scripts/agent_contracts.py doctor --repo .
```

It reports:

- Python version support.
- Plugin file layout.
- Git availability and repository status.
- Read/write permissions.
- Ignore-rule warnings for transient outputs.
- A read-only inventory sample.

Exit codes:

- `0`: usable.
- `1`: usable with warnings.
- `2`: blocked.

### `/contract-map`

Read-only module discovery.

Direct script:

```bash
python3 scripts/agent_contracts.py map --repo .
python3 scripts/agent_contracts.py map --repo . --format json
```

It detects logical modules, owned files, public surfaces, dependencies, tests, local commands, confidence, and boundary notes.

### `/contract-init`

Bootstraps contract files after a preview.

Direct script:

```bash
python3 scripts/agent_contracts.py init --repo .
python3 scripts/agent_contracts.py init --repo . --write --yes
python3 scripts/agent_contracts.py init --repo . --write --yes --overwrite-existing
```

Default behavior is conservative:

- The first run previews planned files and writes nothing.
- `--write --yes` writes new files and skips existing files.
- `--overwrite-existing` is required to replace existing files.

### `/contract-check`

Read-only drift analysis.

Direct script:

```bash
python3 scripts/agent_contracts.py check --repo .
python3 scripts/agent_contracts.py check --repo . --format json
```

It can report:

- Missing `SPEC.md`.
- Missing or stale `AGENTS.md`.
- Orphan files not covered by any declared module path.
- Undeclared module dependencies.
- Missing dependency contracts.
- Imports from another module's `internal/` or `private/` files.
- Public surfaces missing from `SPEC.md`.
- Missing test evidence for declared capabilities.

### `/contract-refresh`

Plans updates after code changes.

Direct script:

```bash
python3 scripts/agent_contracts.py refresh --repo .
python3 scripts/agent_contracts.py refresh --repo . --write-safe --yes
python3 scripts/agent_contracts.py refresh --repo . --write-safe --write-contract --yes
```

Refresh separates:

- Safe instruction refreshes: generated module-map and agent-instruction updates.
- Contract-changing updates: `SPEC.md` changes that can alter public surface, dependencies, or acceptance criteria.

Review contract-changing updates before applying them.

### `/context-pack <module-or-task>`

Builds a bounded local bundle for a module or task.

Direct script:

```bash
python3 scripts/agent_contracts.py context-pack billing --repo .
python3 scripts/agent_contracts.py context-pack "fix payment status tests" --repo .
```

Context packs include relevant contracts, instructions, owned source files, tests, and direct dependency contract summaries when present. They exclude unrelated sibling modules, dependency internals, generated output, vendor files, build output, and caches by default.

## Fixture Walkthrough

Try the Python fixture from this plugin repo:

```bash
python3 scripts/agent_contracts.py doctor --repo fixtures/python-service
python3 scripts/agent_contracts.py map --repo fixtures/python-service
python3 scripts/agent_contracts.py init --repo fixtures/python-service
python3 scripts/agent_contracts.py check --repo fixtures/broken-drift
```

The Python fixture detects `auth`, `billing`, and `root` modules. The broken-drift fixture intentionally reports an internal import, undeclared dependency, missing contracts, and missing agent instructions.

## Current Capabilities

The analyzer currently supports:

- File inventory with generated/vendor/build/cache exclusions.
- Python import and public function/class detection.
- JavaScript and TypeScript import/export detection.
- Basic HTTP route detection for Python and JavaScript/TypeScript patterns.
- Package and command hints from `package.json`, `pyproject.toml`, `Makefile`, and README snippets.
- Root-level tests associated with modules by local evidence.
- Deterministic Markdown and JSON output.
- Local drift reports and bounded context packs.

## Current Limitations

This is a local Phase 1 implementation. It deliberately does not include:

- CI/CD integration.
- Hosted dashboards.
- Source upload.
- External API calls.
- Automatic commits, pushes, pull requests, or merges.
- Execution of target repository code.
- Dependency installation.
- Full language-server precision.
- Specialized Phase 2 contract-maintenance agents.
- A Codex adapter package.

When evidence is incomplete, the analyzer reports confidence and boundary notes instead of pretending certainty.

## Safety Model

Read-only commands stay read-only. Write commands require explicit flags and should be run only after reviewing the preview.

The analyzer does not execute target repository code. It reads text files, manifests, tests, docs, and configs, then produces local artifacts.

See `PRIVACY.md` and `SECURITY.md` for the local-only safety posture.

## Validation

Run:

```bash
python3 scripts/validate_plugin.py
python3 -m unittest discover -s tests
```

Fixtures cover Python, TypeScript, mixed monorepo, existing-document handling, ambiguous module names, overwrite behavior, context packs, and intentionally broken drift cases.
