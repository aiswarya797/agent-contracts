# Contract-Guided Context Fixture

This fixture proves that module source and tests are not always enough.

The active payments module owns refund status behavior, but the repository also
contains a legacy refund implementation with similar names and wording. The
payments contract and local agent instructions explain that new refund status
work must stay on the public payments path and must not use the legacy refund
module.
