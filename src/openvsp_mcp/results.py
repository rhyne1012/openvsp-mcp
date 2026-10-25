"""Read bounded saved results/log tails without relaunching OpenVSP."""

import json
import math
from pathlib import Path

from .models import ResultRequest

_GEOMETRY_OPERATIONS = {"export_geometry", "geometry_analysis"}
_GEOMETRY_KEYS = (
    "analysis",
    "length_unit",
    "units",
    "unit_conversion_applied",
    "summary",
    "components",
    "component_semantics",
    "diagnostics",
    "selection",
    "selected_set",
    "selected_geometry_ids",
    "projection_direction",
    "artifacts",
    "raw_results_json",
    "raw_results_csv",
    "settings",
    "verification",
    "native_errors",
    "result_id",
    "result_name",
    "input_sha256",
    "run_directory",
    "manifest_path",
    "log_path",
    "script_path",
)
_MEASUREMENTS = {
    "wetted_area",
    "wetted_volume",
    "theoretical_area",
    "theoretical_volume",
    "projected_area",
}


def _validate_geometry_metadata(manifest):
    """Reject nonfinite JSON anywhere; retain artifact paths without dereferencing them."""

    def finite(value):
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("Non-finite saved geometry metadata")
        if isinstance(value, dict):
            for child in value.values():
                finite(child)
        elif isinstance(value, list):
            for child in value:
                finite(child)

    def number(value):
        return type(value) in (int, float) and math.isfinite(value)

    finite(manifest)
    if "summary" in manifest:
        summary = manifest["summary"]
        if not isinstance(summary, dict) or not all(number(value) for value in summary.values()):
            raise ValueError("Invalid saved geometry summary")
    if "components" in manifest:
        components = manifest["components"]
        if not isinstance(components, list) or any(
            not isinstance(item, dict) for item in components
        ):
            raise ValueError("Invalid saved geometry components")
        for component in components:
            if any(not number(component[key]) for key in _MEASUREMENTS & component.keys()):
                raise ValueError("Invalid saved geometry component measurement")


def read_results(request: ResultRequest) -> dict:
    path = Path(request.manifest_file).expanduser().resolve()
    try:
        with path.open("rb") as stream:
            raw = stream.read(4 * 1024 * 1024 + 1)
        if len(raw) > 4 * 1024 * 1024:
            raise RuntimeError("Manifest exceeds the 4 MiB read limit")
        manifest = json.loads(raw)
        if not isinstance(manifest, dict) or "operation" not in manifest:
            raise RuntimeError("Not an OpenVSP operation manifest")
        geometry_operation = manifest["operation"] in _GEOMETRY_OPERATIONS
        statuses = {"running", "success", "failed", "cancelled"}
        if geometry_operation:
            statuses |= {"ok", "error"}
        if manifest.get("status") not in statuses:
            raise RuntimeError("Not an OpenVSP operation manifest")
        if geometry_operation:
            _validate_geometry_metadata(manifest)
        coefficients = manifest.get("coefficients", {})
        if not isinstance(coefficients, dict) or any(
            not isinstance(v, (int, float)) or not math.isfinite(v) for v in coefficients.values()
        ):
            raise RuntimeError("Invalid saved coefficients")
        quality = manifest.get("numerical_quality", {})
        if not isinstance(quality, dict):
            raise TypeError("Invalid saved numerical quality")
        unavailable = quality.get("unavailable_coefficients", {})
        if not isinstance(unavailable, dict) or any(
            not isinstance(reason, str) or key in coefficients
            for key, reason in unavailable.items()
        ):
            raise RuntimeError("Invalid saved unavailable coefficients")
        missing = set(request.coefficient_names) - coefficients.keys() - unavailable.keys()
        if missing:
            raise RuntimeError(f"Unknown coefficient names: {sorted(missing)}")
        result = {
            key: manifest.get(key)
            for key in (
                "status",
                "operation",
                "error",
                "versions",
                "timings",
                "numerical_quality",
                "effective_settings",
                "warnings",
                "parameter_values",
            )
        }
        result["coefficients"] = (
            {k: coefficients[k] for k in request.coefficient_names if k in coefficients}
            if request.coefficient_names
            else coefficients
        )
        result["unavailable_coefficients"] = {
            k: reason
            for k, reason in unavailable.items()
            if not request.coefficient_names or k in request.coefficient_names
        }
        if geometry_operation:
            result.update({key: manifest[key] for key in _GEOMETRY_KEYS if key in manifest})
        if request.log != "none":
            log = path.parent / (request.log + ".log")
            with log.open("rb") as stream:
                stream.seek(0, 2)
                start = max(0, stream.tell() - 65536)
                stream.seek(start)
                tail = stream.read(65536).decode("utf-8", errors="replace")
            lines = tail.splitlines()
            if start and lines:
                lines = lines[1:]  # First line can be truncated by the byte window.
            result["log_tail"] = "\n".join(lines[-request.log_tail_lines :])
            result["log_byte_limit"] = 65536
        return result
    except (OSError, ValueError, TypeError, OverflowError) as exc:
        raise RuntimeError(f"Cannot read saved result: {exc}") from exc
