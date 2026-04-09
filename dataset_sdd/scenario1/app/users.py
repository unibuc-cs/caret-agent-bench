"""User endpoints for hard-mode scenarios."""
from __future__ import annotations
from typing import Any, Dict, List


def list_users(users: List[Dict[str, Any]], user_role: str) -> List[Dict[str, Any]]:
    visible = []
    for user in users:
        if not user.get("active", False):
            continue
        visible.append({
            "id": user["id"],
            "name": user["name"],
            "email": user["email"],
        })
    return visible


def format_audit_entry(user: Dict[str, Any]) -> str:
    return build_audit_line(user)


def get_user_names(users: List[Dict[str, Any]]) -> List[str]:
    return [u["name"] for u in users if u.get("active", False)]


def get_admin_contact_summary(users: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Stub to be extended by a task-specific patch."""
    return []
