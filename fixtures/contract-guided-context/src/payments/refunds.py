from ledger.api import record_refund_event


def refund_status(payment_id: str, captured: bool) -> str:
    status = "queued" if captured else "pending_capture"
    record_refund_event(payment_id, status)
    return status
