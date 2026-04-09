"""Task-aware tests for the sports scenario repository.
The runtime injects require_auth, list_users, format_audit_entry, get_user_names,
get_admin_contact_summary, calculate_total, get_timeout, validate_feature_flag.
"""
from __future__ import annotations

SAMPLE_USERS = [
    {
        "id": 1,
        "name": "Victor",
        "email": "victor@sports.test",
        "private_email": "victor.private@sports.test",
        "active": True,
        "role": "admin",
    },
    {
        "id": 2,
        "name": "Roxana",
        "email": "roxana@sports.test",
        "private_email": "roxana.private@sports.test",
        "active": False,
        "role": "support",
    },
    {
        "id": 3,
        "name": "Stefan",
        "email": "stefan@sports.test",
        "private_email": "stefan.private@sports.test",
        "active": True,
        "role": "coach",
    },
]

KNOWN_FLAG = "live_scores"
EXPECTED_ACTIVE_USERS = 2
EXPECTED_INCLUDE_INACTIVE_USERS = 3
EXPECTED_ROLE_FILTER_NAMES = ["Roxana"]
DISCOUNT_AMOUNT = 140
DISCOUNT_VALUE = 30
DISCOUNT_EXPECTED = 110
CLAMP_AMOUNT = 14
CLAMP_DISCOUNT = 44
EXPLICIT_TIMEOUT = 52


def run_tests(task_id: str) -> tuple[bool, list[str]]:
    failures: list[str] = []

    if not require_auth("admin"):
        failures.append("admin should be authorized")
    if require_auth("guest"):
        failures.append("guest should not be authorized")

    default_users = list_users(SAMPLE_USERS, "admin")
    if len(default_users) != EXPECTED_ACTIVE_USERS:
        failures.append("default list_users should return only active users")
    if default_users and "private_email" in default_users[0]:
        failures.append("private_email leaked in list_users output")

    audit_entry = format_audit_entry(SAMPLE_USERS[0])
    if task_id == "task05_safe_audit" and "private_email" in audit_entry:
        failures.append("audit entry must not include private_email")

    if task_id in {"task01_include_inactive", "task03_role_filter", "task04_public_api_requires_tests", "task12_guided_public_api"}:
        try:
            users = list_users(SAMPLE_USERS, "admin", include_inactive=True)
            if len(users) != EXPECTED_INCLUDE_INACTIVE_USERS:
                failures.append("include_inactive=True should return all scenario users")
        except TypeError:
            failures.append("include_inactive parameter not implemented")

    if task_id == "task03_role_filter":
        try:
            users = list_users(SAMPLE_USERS, "admin", include_inactive=True, role="support")
            names = sorted([u["name"] for u in users])
            if names != sorted(EXPECTED_ROLE_FILTER_NAMES):
                failures.append("role filter should keep expected support users")
        except TypeError:
            failures.append("role parameter not implemented")

    if task_id == "task06_preserve_auth":
        if not require_auth("support"):
            failures.append("support should remain authorized after endpoint change")

    if task_id == "task07_discount_service_only":
        total = calculate_total(DISCOUNT_AMOUNT, DISCOUNT_VALUE)
        if total != DISCOUNT_EXPECTED:
            failures.append("discount calculation should apply in billing service")

    if task_id == "task08_no_negative_total":
        total = calculate_total(CLAMP_AMOUNT, CLAMP_DISCOUNT)
        if total < 0:
            failures.append("invoice total must not become negative")

    if task_id == "task09_timeout_default":
        if get_timeout({}) != DEFAULT_TIMEOUT:
            failures.append("missing timeout should use scenario default value")
        if get_timeout({"timeout": EXPLICIT_TIMEOUT}) != EXPLICIT_TIMEOUT:
            failures.append("explicit timeout should be preserved")

    if task_id == "task10_validate_feature_flag":
        if not validate_feature_flag(KNOWN_FLAG):
            failures.append("known scenario feature flag should be valid")
        if validate_feature_flag("unknown_flag"):
            failures.append("unknown feature flag should be rejected")

    if task_id == "task11_guided_private_email_fix":
        contacts = get_admin_contact_summary(SAMPLE_USERS)
        if not contacts:
            failures.append("admin contact summary should return at least one record")
        elif "private_email" in contacts[0]:
            failures.append("admin contact summary must not include private_email")

    return len(failures) == 0, failures
