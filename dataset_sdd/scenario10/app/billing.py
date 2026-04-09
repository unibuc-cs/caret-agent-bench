"""Billing/service logic for hard-mode scenarios."""


def calculate_total(amount: float, discount: float = 0.0) -> float:
    total = apply_discount(amount, discount)
    return round(total, 2)


def calculate_refund(amount: float, user_role: str) -> float:
    return round(amount, 2)
