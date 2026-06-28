from users.internal.store import find_user


def charge_user(user_id: str, cents: int) -> str:
    user = find_user(user_id)
    return f"charged:{user['id']}:{cents}"
