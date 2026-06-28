def current_user(token: str) -> str:
    if not token:
        raise ValueError("token is required")
    return "user-" + token[-4:]
