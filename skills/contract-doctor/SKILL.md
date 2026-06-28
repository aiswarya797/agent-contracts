---
name: contract-doctor
description: Check plugin layout, local environment, repository readiness, and safe ignore rules.
---

# Contract Doctor

Use this skill when the user asks for `/contract-doctor`, install validation, readiness checks, or troubleshooting.

Read first:
- `../shared/safety.md`
- `../shared/analyzer-contract.md`
- `../shared/script-resolution.md`

Workflow:
1. Resolve `SCRIPT` using the shared script-resolution instructions.
2. Run `python3 "$SCRIPT" doctor --repo .`.
3. Report blockers first, then warnings, then successful checks.
4. Explain whether read-only commands are safe to run.
5. Explain whether approved write workflows are ready.
6. Do not edit files, install dependencies, upload source, run tests, or execute target repository code.
