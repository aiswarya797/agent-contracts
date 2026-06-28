---
name: contract-check
description: Compare module contracts against local code evidence and report drift findings with fixes.
---

# Contract Check

Use this skill when the user asks for `/contract-check` or wants drift analysis.

Read first:
- `../shared/safety.md`
- `../shared/analyzer-contract.md`
- `../shared/script-resolution.md`

Workflow:
1. Resolve `SCRIPT` using the shared script-resolution instructions.
2. Run `python3 "$SCRIPT" check --repo .`.
3. Report findings by severity.
4. For each finding, include severity, evidence, affected files, and a concrete local fix.
5. Keep the workflow read-only unless the user later asks for a refresh or edit.
6. Do not run tests unless the user explicitly asks.
