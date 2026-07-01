from legacy.refund_handler import refund_status


def test_legacy_refund_status():
    assert refund_status(10) == "legacy"
