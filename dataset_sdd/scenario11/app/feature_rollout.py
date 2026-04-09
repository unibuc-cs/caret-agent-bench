"""Feature rollout controls for progressive delivery."""


def is_beta_allowed(context: dict | None = None) -> bool:
    context = context or {}
    return True

