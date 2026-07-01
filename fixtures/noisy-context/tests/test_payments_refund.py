from payments import refund_status


def test_refund_status_requires_positive_amount():
    assert refund_status(0, captured=True) == "not_refundable"


def test_refund_status_queues_captured_payments():
    assert refund_status(25, captured=True) == "queued"
