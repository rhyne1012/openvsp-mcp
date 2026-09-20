"""Read bounded saved results/log tails without relaunching OpenVSP."""

import json
import math
from pathlib import Path

from .models import ResultRequest


def read_results(request: ResultRequest) -> dict:
    path = Path(request.manifest_file).expanduser().resolve()
    try:
        with path.open("rb") as stream:
            raw = stream.read(4 * 1024 * 1024 + 1)
        if len(raw) > 4 * 1024 * 1024:
            raise RuntimeError("Manifest exceeds the 4 MiB read limit")
        manifest = json.loads(raw)
        if (
            not isinstance(manifest, dict)
            or manifest.get("status") not in {"running", "success", "failed", "cancelled"}
            or "operation" not in manifest
        ):
            raise RuntimeError("Not an OpenVSP operation manifest")
        coefficients = manifest.get("coefficients", {})
        if not isinstance(coefficients, dict) or any(
            not isinstance(v, (int, float)) or not math.isfinite(v) for v in coefficients.values()
        ):
            raise RuntimeError("Invalid saved coefficients")
        missing = set(request.coefficient_names) - coefficients.keys()
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
            {k: coefficients[k] for k in request.coefficient_names}
            if request.coefficient_names
            else coefficients
        )
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
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"Cannot read saved result: {exc}") from exc
