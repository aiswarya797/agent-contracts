# payments Agent Instructions

## Module Scope

Work in `src/payments` for active payment refund status behavior.

## Start Here

- Read `src/payments/SPEC.md` before editing refund behavior.
- Use `tests/test_payments_refunds.py` as the source-to-test anchor.

## Dependency Rules

- Use the public `ledger` contract for refund audit events.
- Do not edit or import `src/legacy/refunds.py`; it is a retired batch path
  with intentionally similar refund names.
