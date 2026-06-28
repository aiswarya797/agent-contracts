# Drift Report Example

## 1. [HIGH] Module imports another module's internal file
- code: internal-import
- evidence: `src/payments/api.py` imports `users.internal.store`.
- affected files: `src/payments/api.py`, `src/users/internal/store.py`
- local fix: Replace the import with a public surface from the dependency module.

## 2. [MEDIUM] Detected public surface is not listed in SPEC.md
- code: undeclared-public-surface
- evidence: `function charge_user` is detected but absent from `src/payments/SPEC.md`.
- affected files: `src/payments/api.py`
- local fix: Review the function and add it to the contract or make it internal.
