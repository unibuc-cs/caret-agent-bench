"""Masking helpers for compliance checks."""


def mask_email(value: str) -> str:
    if "@" not in value:
        return "***"
    local, domain = value.split("@", 1)
    head = local[:1] if local else "*"
    return f"{head}***@{domain}"
