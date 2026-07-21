def shipping_quote(weight_kg, zone, expedited=False):
    if weight_kg <= 0:
        raise ValueError("weight_kg must be positive")
    if zone not in {1, 2, 3}:
        raise ValueError("unsupported zone")

    base = 4.0 + (weight_kg * 1.25)
    zone_multiplier = {1: 1.0, 2: 1.35, 3: 1.8}[zone]
    quote = base * zone_multiplier

    if expedited and zone != 3:
        quote += 7.5

    return round(quote, 2)
