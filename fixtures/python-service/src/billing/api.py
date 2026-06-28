from auth import current_user


def calculate_total(items: list[dict[str, int]]) -> int:
    return sum(item["price"] * item.get("quantity", 1) for item in items)


def payment_status(total: int, token: str) -> str:
    current_user(token)
    if total <= 0:
        return "unpaid"
    return "paid"
