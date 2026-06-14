def apply_discount(total, discount):
    if total < 0:
        raise ValueError("total must be non-negative")

    kind = discount.get("kind")
    value = discount.get("value", 0)

    if kind == "percentage":
        if not 0 <= value <= 1:
            raise ValueError("percentage discount must be between 0 and 1")
        return round(total * (1 - value), 2)

    if kind == "fixed":
        if value < 0:
            raise ValueError("fixed discount must be non-negative")
        return round(max(0, total - value), 2)

    raise ValueError("unsupported discount kind")
