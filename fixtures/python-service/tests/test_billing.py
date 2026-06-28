from billing import calculate_total, payment_status


def test_calculate_total():
    assert calculate_total([{"price": 10, "quantity": 2}]) == 20


def test_payment_status():
    assert payment_status(20, "token-1234") == "paid"
