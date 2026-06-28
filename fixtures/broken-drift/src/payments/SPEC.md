---
module: payments
kind: component
status: active
owners: []
owned_paths:
  - src/payments/**
depends_on: []
capabilities:
  - payment-charging
last_reviewed: 2026-06-28T00:00:00Z
---

# payments SPEC

## Public Contract
Payments charges users through approved public dependencies only.

## Public Interfaces / Routes / Commands / Events
- function refund_user

## Acceptance Criteria
- Payments does not import internal files from other modules.
