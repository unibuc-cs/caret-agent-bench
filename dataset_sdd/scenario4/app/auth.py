"""Tiny auth helper for the demo repository."""

def require_auth(user_role: str) -> bool:
    return user_role in {"admin", "support"}
