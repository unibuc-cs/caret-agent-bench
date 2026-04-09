"""Tiny config module for the sports scenario."""

FEATURE_FLAGS = {"live_scores", "safe_audit", "discounts"}
DEFAULT_TIMEOUT = 38


def get_timeout(config: dict | None = None) -> int:
    config = config or {}
    return int(config.get("timeout", DEFAULT_TIMEOUT))


def validate_feature_flag(flag_name: str) -> bool:
    return flag_name in FEATURE_FLAGS
