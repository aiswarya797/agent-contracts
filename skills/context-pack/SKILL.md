---
name: context-pack
description: Build bounded local context packs for a module or task using exact relevant files and contract summaries.
---

# Context Pack

Use this skill when the user asks for `/context-pack <module-or-task>` or wants a local bundle for agent work.

Read first:
- `../shared/safety.md`
- `../shared/analyzer-contract.md`
- `../shared/generated-files.md`
- `../shared/script-resolution.md`

Workflow:
1. Identify the target module or task from the user request.
2. Resolve `SCRIPT` using the shared script-resolution instructions.
3. Run `python3 "$SCRIPT" context-pack "<target>" --repo .`.
4. Include root `ARCHITECTURE.md`, root `AGENTS.md`, target module `SPEC.md`, target module `AGENTS.md`, direct dependency contract summaries, owned source files, relevant tests, and verification commands when present.
5. Exclude unrelated sibling modules, generated outputs, vendor files, build outputs, dependency internals, and caches unless the user explicitly asks.
6. Explain the output directory, included files, omitted files, and limits.
