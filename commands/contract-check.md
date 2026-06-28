---
name: contract-check
description: Read-only drift check between module contracts and code evidence
allowed-tools: Read, Bash(python3:*), Bash(pwd:*), Bash(ls:*), Bash(rg:*)
---

Run the agent-contracts drift workflow for the current repository.

Steps:
1. Resolve the bundled analyzer path. If the current repository has `scripts/agent_contracts.py`, use it. Otherwise use the absolute plugin checkout path to `scripts/agent_contracts.py`.
2. Run `python3 "$SCRIPT" check --repo .`.
3. Report findings by severity.
4. For each finding, include evidence, affected files, and the concrete local fix.
5. Do not edit files.
6. Do not run tests unless the user explicitly asks.
