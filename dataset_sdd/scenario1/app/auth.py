"""Auth helpers for hard-mode scenarios."""


def require_auth(user_role: str) -> bool:
    return user_role in {"admin", "support", "auditor"}
