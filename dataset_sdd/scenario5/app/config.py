"""Tiny config module for the finance scenario."""

FEATURE_FLAGS = {"risk_console", "safe_audit", "discounts"}
DEFAULT_TIMEOUT = 45


def get_timeout(config: dict | None = None) -> int:
    config = config or {}
    return int(config.get("timeout", DEFAULT_TIMEOUT))


def validate_feature_flag(flag_name: str) -> bool:
    return flag_name in FEATURE_FLAGS
