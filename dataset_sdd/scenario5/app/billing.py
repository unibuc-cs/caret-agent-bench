"""Tiny billing/service logic."""


def calculate_total(amount: float, discount: float = 0.0) -> float:
    total = amount - discount
    return round(total, 2)
