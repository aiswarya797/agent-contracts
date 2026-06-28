# Safety Rules

agent-contracts is local-only by default.

- Do not upload source.
- Do not require a cloud service.
- Do not install dependencies.
- Do not execute target repository code.
- Do not run tests unless the user explicitly asks.
- Do not overwrite existing files without explicit user confirmation.
- Explain every generated file before writing.
- Explain every drift finding with evidence and a local fix.
- Prefer deterministic scripts and readable markdown over hidden behavior.
