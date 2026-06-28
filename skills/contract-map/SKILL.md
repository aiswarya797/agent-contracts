---
name: contract-map
description: Read-only module map for logical repository modules, public surfaces, dependencies, tests, commands, and ambiguous boundaries.
---

# Contract Map

Use this skill when the user asks for `/contract-map` or wants a read-only view of module boundaries.

Read first:
- `../shared/safety.md`
- `../shared/analyzer-contract.md`
- `../shared/script-resolution.md`

Workflow:
1. Resolve `SCRIPT` using the shared script-resolution instructions.
2. Run `python3 "$SCRIPT" map --repo .`.
3. Summarize modules, owned files, public surfaces, dependencies, tests, commands, and confidence.
4. Highlight ambiguous module boundaries from boundary notes.
5. Do not write files.
6. Do not install dependencies or execute target repository code.
