def refund_status(amount: int, captured: bool) -> str:
    if amount <= 0:
        return "not_refundable"
    if captured:
        return "queued"
    return "pending_capture"
