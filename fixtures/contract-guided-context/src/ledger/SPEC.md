---
module: ledger
kind: component
status: active
owned_paths:
  - src/ledger/**
depends_on: []
capabilities:
  - refund-audit-log
---

# ledger SPEC

## Public Contract

`ledger` exposes audit helpers for payment events. Payment modules may call
`record_refund_event` for refund status transitions, but must not write ledger
records directly.
