---
name: context-pack
description: Build a bounded local context pack for a module or task
allowed-tools: Read, Bash(python3:*), Bash(pwd:*), Bash(ls:*), Bash(rg:*), Write
---

Build a local context pack for the requested module or task.

Steps:
1. Require an argument such as `/context-pack billing` or `/context-pack "fix payment status tests"`.
2. Resolve the bundled analyzer path. If the current repository has `scripts/agent_contracts.py`, use it. Otherwise use the absolute plugin checkout path to `scripts/agent_contracts.py`.
3. Run `python3 "$SCRIPT" context-pack "<argument>" --repo .`.
4. Explain the output directory, included files, omitted files, dependency summaries, and verification commands.
5. Do not include unrelated sibling modules, dependency internals, generated outputs, vendor directories, or build artifacts unless the user explicitly asks.
