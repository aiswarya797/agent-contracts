---
name: contract-refresh
description: Refresh stale module instructions and separately gate contract-changing updates.
---

# Contract Refresh

Use this skill when the user asks for `/contract-refresh` or wants generated contracts updated after code changes.

Read first:
- `../shared/safety.md`
- `../shared/analyzer-contract.md`
- `../shared/generated-files.md`
- `../shared/script-resolution.md`

Workflow:
1. Resolve `SCRIPT` using the shared script-resolution instructions.
2. Run `python3 "$SCRIPT" refresh --repo .`.
3. Separate safe instruction refreshes from contract-changing updates.
4. Explain what changed and why.
5. Ask the user for approval before writing. Present numbered choices and wait for a reply number:
   1. Write safe instruction refreshes only.
   2. Write safe refreshes and contract-changing updates.
   3. Do not write files.
6. For option 1, run `python3 "$SCRIPT" refresh --repo . --write-safe --yes`.
7. For option 2, restate contract-changing files, ask for explicit confirmation, then run `python3 "$SCRIPT" refresh --repo . --write-safe --write-contract --yes`.
8. Never silently change public contracts.
