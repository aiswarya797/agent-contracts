from legacy.refunds import refund_status


def test_legacy_refund_status_is_not_customer_facing():
    assert refund_status("pay_123") == "legacy-refund:pay_123"
