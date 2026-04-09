"""Billing discount helpers."""


def apply_discount(amount: float, discount: float) -> float:
    return amount - discount


def clamp_total(value: float) -> float:
    return value
