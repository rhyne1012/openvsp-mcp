"""Only mathematically undefined, named ratios may be absent from numeric results."""

import math

# Names and denominators from the audited VSPAERO polar/history writers.
RATIO_DENOMINATORS = {"L/D": "CDtot", "E": "CDi", "LoDw": "CDwtot", "Ew": "CDiw"}


def finite_row(names, values):
    if len(names) != len(values) or len(set(names)) != len(names):
        raise ValueError("Invalid numeric table columns")
    row = dict(zip(names, values))
    unavailable = {}
    for name, value in list(row.items()):
        if math.isfinite(value):
            continue
        denominator = RATIO_DENOMINATORS.get(name)
        if denominator is None or row.get(denominator) != 0.0:
            raise ValueError(f"Non-finite required or unexplained field: {name}")
        unavailable[name] = f"Undefined ratio: {denominator} is zero in native output"
        del row[name]
    return row, unavailable
