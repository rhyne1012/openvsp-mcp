"""Report observed iteration changes without claiming aerodynamic convergence."""

import math
from pathlib import Path


def history_diagnostics(path: Path) -> dict:
    report = {
        "convergence_status": "not_assessed",
        "mesh_study": "not_performed",
        "scope": "Iteration changes are diagnostics, not an accuracy or convergence gate.",
    }
    header, rows, invalid = None, [], []
    for number, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
        cols = (
            line.replace("L2 Residual", "L2_Residual")
            .replace("Max Residual", "Max_Residual")
            .split()
        )
        if cols[:4] == ["Iter", "Mach", "AoA", "Beta"]:
            header = cols
        elif header and cols:
            try:
                values = [float(v) for v in cols]
            except ValueError:
                invalid.append(number)
                continue
            if len(values) == len(header) and all(math.isfinite(v) for v in values):
                rows.append(dict(zip(header, values)))
            else:
                invalid.append(number)
    if invalid:
        return report | {
            "history_status": "invalid",
            "invalid_line_numbers": invalid,
            "iterations_recorded": len(rows),
        }
    keys = ["CLtot", "CDtot", "CMytot"]
    if len(rows) < 2 or not all(k in rows[-1] for k in keys):
        return report | {"history_status": "unavailable_or_insufficient"}
    return report | {
        "history_status": "available",
        "iterations_recorded": len(rows),
        "last_iteration": rows[-1]["Iter"],
        "last_step_absolute_change": {k: abs(rows[-1][k] - rows[-2][k]) for k in keys},
        "trailing_window_size": min(5, len(rows)),
        "trailing_window_range": {
            k: max(r[k] for r in rows[-5:]) - min(r[k] for r in rows[-5:]) for k in keys
        },
    }
