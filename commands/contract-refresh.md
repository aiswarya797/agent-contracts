---
name: contract-refresh
description: Plan safe instruction refreshes and contract-changing updates
allowed-tools: Read, Bash(python3:*), Bash(pwd:*), Bash(ls:*), Bash(rg:*), Write, Edit
---

Run the agent-contracts refresh workflow for the current repository.

Steps:
1. Resolve the bundled analyzer path. If the current repository has `scripts/agent_contracts.py`, use it. Otherwise use the absolute plugin checkout path to `scripts/agent_contracts.py`.
2. Run `python3 "$SCRIPT" refresh --repo .`.
3. Separate safe instruction refreshes from contract-changing updates.
4. Ask the user for approval before writing. Present numbered choices and wait for a reply number:
   1. Write safe instruction refreshes only.
   2. Write safe refreshes and contract-changing updates.
   3. Do not write files.
5. If the user chooses option 1, run `python3 "$SCRIPT" refresh --repo . --write-safe --yes`.
6. If the user chooses option 2, restate the contract-changing files and ask for explicit confirmation before running `python3 "$SCRIPT" refresh --repo . --write-safe --write-contract --yes`.
7. Never silently change public contracts.
