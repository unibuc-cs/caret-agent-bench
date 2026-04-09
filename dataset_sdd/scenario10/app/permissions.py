"""Permission matrix for user and billing operations."""


def can_view_role(actor_role: str, target_role: str) -> bool:
    if actor_role == "admin":
        return True
    if actor_role == "support":
        return target_role in {"support", "editor", "viewer"}
    return actor_role == target_role


def can_issue_refund(actor_role: str) -> bool:
    return actor_role == "admin"
