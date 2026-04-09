"""Audit utilities used by user-facing handlers."""


def build_audit_line(user: dict) -> str:
    return f"AUDIT user_id={user['id']} email={user['email']}"
