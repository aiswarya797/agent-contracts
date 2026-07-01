---
module: payments
kind: component
status: active
owned_paths:
  - src/payments/**
depends_on:
  - ledger
capabilities:
  - public-refund-status
---

# payments SPEC

## Public Contract

`payments` owns active refund status behavior through `src/payments/refunds.py`.
Tasks that mention customer-facing refund status must use this public payments
surface and its tests.

## Boundary Rules

- Do not use `src/legacy/refunds.py` for active payment behavior, even when the
  filename or function names look relevant.
- Keep refund audit writes behind the public `ledger` contract.

## Verification Evidence

- `tests/test_payments_refunds.py`
