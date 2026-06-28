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

  <p>Generate local contracts, agent instructions, context packs, and drift checks from real code evidence.</p>

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

Most repos already contain the truth, but it is scattered across source files, tests, package manifests, docs, conventions, and team memory. This tool scans that evidence and writes contracts that answer:

- Which part of the repo owns these files?
- What public behavior is promised here?
- Which dependencies are expected, and which ones look suspicious?
- Which tests or commands prove the change still works?
- Which local instructions should an agent read before touching code?

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

### 2. Build A Context Pack

Create a bounded local bundle for a module or task:

```bash
agent-contracts context-pack billing --repo .
agent-contracts context-pack "fix payment status tests" --repo .
```

Context packs include relevant contracts, instructions, owned source files, tests, and direct dependency contract summaries when present.

### 3. Check For Drift

Compare contracts against current code evidence:

```bash
agent-contracts check --repo .
```

This reports issues such as undeclared dependencies, internal imports, missing contracts, missing agent instructions, uncovered files, and public surfaces missing from `SPEC.md`.

### 4. Refresh After Code Changes

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

### 5. Inspect The Map

Show the detected ownership and dependency map without writing files:

```bash
agent-contracts map --repo .
```

### 6. Troubleshoot Setup

Use doctor when installation or repository readiness looks off:

```bash
agent-contracts doctor --repo .
```

Doctor checks plugin layout, Python availability, Git state, permissions, ignore rules, and whether the repo can be inventoried locally.
