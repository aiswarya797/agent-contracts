---
name: contract-doctor
description: Check local plugin and repository readiness before running contract workflows
allowed-tools: Read, Bash(python3:*), Bash(pwd:*), Bash(ls:*), Bash(git:*)
---

Run the agent-contracts readiness check for the current repository.

Steps:
1. Resolve the bundled analyzer path. If the current repository has `scripts/agent_contracts.py`, use it. Otherwise use the absolute plugin checkout path to `scripts/agent_contracts.py`.
2. Run `python3 "$SCRIPT" doctor --repo .`.
3. Report blocker checks first, then warnings, then successful checks.
4. Explain whether the repository is ready for read-only commands and whether it is ready for approved write workflows.
5. Do not edit files.
6. Do not install dependencies or execute target repository code.
