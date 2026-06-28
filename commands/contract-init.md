---
name: contract-init
description: Propose module contracts and write them only after explicit approval
allowed-tools: Read, Bash(python3:*), Bash(pwd:*), Bash(ls:*), Bash(rg:*), Write, Edit
---

Run the agent-contracts initialization workflow for the current repository.

Steps:
1. Resolve the bundled analyzer path. If the current repository has `scripts/agent_contracts.py`, use it. Otherwise use the absolute plugin checkout path to `scripts/agent_contracts.py`.
2. Run `python3 "$SCRIPT" init --repo .` first. This is read-only and prints the proposed module map plus planned files.
3. Explain every planned generated file and every ambiguous boundary.
4. Ask the user for approval before writing. Present numbered choices and wait for a reply number:
   1. Write only new files.
   2. Write new files and overwrite listed existing files.
   3. Do not write files.
5. If the user chooses option 1, run `python3 "$SCRIPT" init --repo . --write --yes`.
6. If the user chooses option 2, repeat the existing file list and ask for explicit confirmation before running `python3 "$SCRIPT" init --repo . --write --yes --overwrite-existing`.
7. Never overwrite existing files without explicit user confirmation.
