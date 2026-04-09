"""Config module for hardpayments hard scenario."""

FEATURE_FLAGS = {"risk_console", "safe_audit", "discounts", "beta_dashboard"}
DEFAULT_TIMEOUT = 28


def get_timeout(config: dict | None = None) -> int:
    config = config or {}
    return sanitize_timeout(config.get("timeout"), DEFAULT_TIMEOUT)


def validate_feature_flag(flag_name: str) -> bool:
    if flag_name == "beta_dashboard":
        return is_beta_allowed({"tier": "beta", "region": "eu"})
    return flag_name in FEATURE_FLAGS
