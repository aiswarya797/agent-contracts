---
name: contract-init
description: Initialize module contracts after a read-only proposal and explicit user approval.
---

# Contract Init

Use this skill when the user asks for `/contract-init` or wants to generate initial module contracts.

Read first:
- `../shared/safety.md`
- `../shared/analyzer-contract.md`
- `../shared/generated-files.md`
- `../shared/script-resolution.md`

Workflow:
1. Resolve `SCRIPT` using the shared script-resolution instructions.
2. Run `python3 "$SCRIPT" init --repo .`.
3. Present the proposed module map and planned generated files.
4. Explain existing files that would be skipped or overwritten.
5. Ask the user for approval before writing. Present numbered choices and wait for a reply number:
   1. Write only new files.
   2. Write new files and overwrite listed existing files.
   3. Do not write files.
6. For option 1, run `python3 "$SCRIPT" init --repo . --write --yes`.
7. For option 2, ask for explicit overwrite confirmation, then run `python3 "$SCRIPT" init --repo . --write --yes --overwrite-existing`.
8. Never overwrite existing files without explicit confirmation.
