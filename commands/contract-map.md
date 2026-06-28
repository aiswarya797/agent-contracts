---
name: contract-map
description: Read-only module map for the current repository
allowed-tools: Read, Bash(python3:*), Bash(pwd:*), Bash(ls:*), Bash(rg:*)
---

Run the agent-contracts map workflow for the current repository.

Steps:
1. Resolve the bundled analyzer path. If the current repository has `scripts/agent_contracts.py`, use it. Otherwise use the absolute plugin checkout path to `scripts/agent_contracts.py`.
2. Run `python3 "$SCRIPT" map --repo .`.
3. Summarize detected modules, owned files, public surfaces, dependencies, tests, commands, confidence, and ambiguous boundaries.
4. Do not write files.
5. If the script is not available, explain that the plugin checkout path is needed.
