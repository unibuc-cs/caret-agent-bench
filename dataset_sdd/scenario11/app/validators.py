"""Validation helpers for the hardhealth domain."""


def sanitize_timeout(raw: int | float | None, default_timeout: int) -> int:
    if raw is None:
        return default_timeout
    value = int(raw)
    if value <= 0:
        return default_timeout
    if value > 120:
        return 120
    return value
