from payments import refund_status


def test_refund_status_queues_captured_payment():
    assert refund_status("pay_123", captured=True) == "queued"


def test_refund_status_waits_for_capture():
    assert refund_status("pay_123", captured=False) == "pending_capture"
