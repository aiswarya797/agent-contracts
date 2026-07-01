def refund_status(payment_id: str) -> str:
    return f"legacy-refund:{payment_id}"
