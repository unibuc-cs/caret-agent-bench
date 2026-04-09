"""Task-aware tests for hard-mode hardpayments scenario."""
from __future__ import annotations

SAMPLE_USERS = [
    {
        "id": 1,
        "name": "Ana",
        "email": "ana@hardpayments.test",
        "private_email": "ana.private@hardpayments.test",
        "active": True,
        "role": "admin",
    },
    {
        "id": 2,
        "name": "Bogdan",
        "email": "bogdan@hardpayments.test",
        "private_email": "bogdan.private@hardpayments.test",
        "active": False,
        "role": "support",
    },
    {
        "id": 3,
        "name": "Carmen",
        "email": "carmen@hardpayments.test",
        "private_email": "carmen.private@hardpayments.test",
        "active": True,
        "role": "editor",
    },
]

KNOWN_FLAG = "risk_console"
DEFAULT_TIMEOUT = 28


def run_tests(task_id: str) -> tuple[bool, list[str]]:
    failures: list[str] = []

    if not require_auth("admin"):
        failures.append("admin should be authorized")
    if require_auth("guest"):
        failures.append("guest should not be authorized")

    default_users = list_users(SAMPLE_USERS, "admin")
    if len(default_users) != 2:
        failures.append("default list_users should return only active users")
    if default_users and "private_email" in default_users[0]:
        failures.append("private_email leaked in list_users output")

    audit_entry = format_audit_entry(SAMPLE_USERS[0])
    if task_id == "task05_safe_audit" and "private_email" in audit_entry:
        failures.append("audit entry must not include private_email")

    if task_id in {"task01_include_inactive", "task03_role_filter", "task04_public_api_requires_tests", "task12_guided_public_api"}:
        try:
            users = list_users(SAMPLE_USERS, "admin", include_inactive=True)
            if len(users) != 3:
                failures.append("include_inactive=True should return all users")
        except TypeError:
            failures.append("include_inactive parameter not implemented")

    if task_id == "task03_role_filter":
        try:
            users = list_users(SAMPLE_USERS, "admin", include_inactive=True, role="support")
            if len(users) != 1 or users[0]["name"] != "Bogdan":
                failures.append("role filter should keep only support users")
        except TypeError:
            failures.append("role parameter not implemented")

    if task_id == "task06_preserve_auth":
        if not require_auth("support"):
            failures.append("support should remain authorized after endpoint change")

    if task_id == "task07_discount_service_only":
        total = calculate_total(100, 10)
        if total != 90:
            failures.append("discount calculation should apply in billing service")

    if task_id == "task08_no_negative_total":
        total = calculate_total(20, 50)
        if total < 0:
            failures.append("invoice total must not become negative")

    if task_id == "task09_timeout_default":
        if get_timeout({}) != DEFAULT_TIMEOUT:
            failures.append("missing timeout should use scenario default timeout")
        if get_timeout({"timeout": 45}) != 45:
            failures.append("explicit timeout should be preserved")

    if task_id == "task10_validate_feature_flag":
        if not validate_feature_flag(KNOWN_FLAG):
            failures.append("known feature flag should be valid")
        if validate_feature_flag("unknown_flag"):
            failures.append("unknown feature flag should be rejected")

    if task_id == "task11_guided_private_email_fix":
        contacts = get_admin_contact_summary(SAMPLE_USERS)
        if not contacts:
            failures.append("admin contact summary should return at least one record")
        elif "private_email" in contacts[0]:
            failures.append("admin contact summary must not include private_email")

    if task_id == "task13_refund_guard":
        if calculate_refund(50, "support") != 50:
            failures.append("support should be able to refund in guarded flow")
        if calculate_refund(50, "guest") != 0:
            failures.append("guest refund must be denied")

    if task_id == "task14_masked_audit":
        entry = format_audit_entry(SAMPLE_USERS[0])
        if "***@" not in entry:
            failures.append("audit output should include masked email")
        if SAMPLE_USERS[0]["email"] in entry:
            failures.append("audit output should not include raw email")

    if task_id == "task15_rollout_guard":
        if not is_beta_allowed({"tier": "beta", "region": "eu"}):
            failures.append("beta users should pass rollout guard")
        if is_beta_allowed({"tier": "free", "region": "eu"}):
            failures.append("free users should not pass rollout guard")

    return len(failures) == 0, failures
