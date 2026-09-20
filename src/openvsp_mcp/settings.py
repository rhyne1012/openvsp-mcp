"""Validate the solver input actually written by the audited Analysis API."""

import math
from pathlib import Path

from .models import OpenVSPRequest


def read_effective_settings(path: Path, request: OpenVSPRequest) -> dict:
    values = {}
    for line in path.read_text().splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    a = request.analysis
    expected = {
        "Sref": a.sref,
        "Bref": a.bref,
        "Cref": a.cref,
        "X_cg": a.xcg,
        "Y_cg": a.ycg,
        "Z_cg": a.zcg,
        "Mach": a.mach,
        "AoA": a.alpha,
        "Beta": a.beta,
        "Vinf": a.vinf,
        "Rho": a.rho,
        "ReCref": a.reynolds,
        "WakeIters": 0 if a.fixed_wake else a.wake_iterations,
        "NumWakeNodes": a.wake_nodes,
        "ForwardGMRESConvergenceFactor": a.forward_gmres_tolerance_factor,
        "VSP_StabilityType": 0,
    }
    actual = {}
    for name, requested in expected.items():
        try:
            value = float(values[name])
        except (KeyError, ValueError) as exc:
            raise RuntimeError(f"Missing or invalid effective solver setting: {name}") from exc
        if not math.isfinite(value) or not math.isclose(
            value, requested, rel_tol=1e-9, abs_tol=1e-10
        ):
            raise RuntimeError(
                f"Effective solver setting mismatch: {name}, requested={requested}, actual={value}"
            )
        actual[name] = value
    return {
        "status": "verified",
        "solver_file_values": actual,
        "scope": "Listed solver-file fields only; NCPU is passed by the Analysis API.",
    }
