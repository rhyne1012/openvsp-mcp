"""Model creation, read-only exports, preflight, and bounded sequential sweeps."""

import hashlib
import json
import tempfile
import time
from pathlib import Path
from shutil import copy2
from typing import Any

from .core import execute_openvsp
from .geometry import simple_aircraft_commands
from .models import CreateModelRequest, OpenVSPRequest, OpenVSPResponse, SweepRequest, VSPCommand


def create_model(request: CreateModelRequest) -> OpenVSPResponse:
    commands = simple_aircraft_commands() if request.template == "simple_aircraft" else []
    base = OpenVSPRequest(
        geometry_file="",
        output_dir=request.output_dir,
        case_name=request.case_name,
        timeout_seconds=request.timeout_seconds,
        run_vspaero=False,
        set_commands=[VSPCommand(command=c) for c in commands] + request.set_commands,
    )
    return execute_openvsp(base, operation="create")


def preview_model(request: OpenVSPRequest) -> OpenVSPResponse:
    return execute_openvsp(request, operation="preview")


def preflight_model(request: OpenVSPRequest) -> OpenVSPResponse:
    return execute_openvsp(request, operation="preflight")


def run_sweep(request: SweepRequest) -> dict[str, Any]:
    """Each row is independently verified; timeout_seconds budgets the entire batch."""
    source = Path(request.geometry_file).expanduser().resolve()
    parent = (
        Path(request.output_dir).expanduser().resolve()
        if request.output_dir
        else (source.parent / "openvsp_runs")
    )
    parent.mkdir(parents=True, exist_ok=True)
    batch = Path(tempfile.mkdtemp(prefix=request.case_name + "-sweep-", dir=parent))
    manifest = {
        "status": "running",
        "conditions": [c.model_dump() for c in request.conditions],
        "results": [],
        "timeout_seconds": request.timeout_seconds,
    }
    manifest_path = batch / "sweep.json"

    def save():
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")

    save()
    deadline = time.monotonic() + request.timeout_seconds
    try:
        snapshot = batch / "source.vsp3"
        copy2(source, snapshot)
        manifest["input_sha256"] = hashlib.sha256(snapshot.read_bytes()).hexdigest()
        for index, condition in enumerate(request.conditions):
            remaining = int(deadline - time.monotonic())
            if remaining < 1:
                raise RuntimeError("Sweep total time budget exhausted")
            single = OpenVSPRequest(
                geometry_file=str(snapshot),
                output_dir=str(batch),
                case_name=f"point_{index:03d}",
                analysis=condition,
                set_commands=request.set_commands,
                timeout_seconds=remaining,
            )
            response = execute_openvsp(single)
            manifest["results"].append(response.model_dump())
            save()
        manifest["status"] = "success"
        save()
    except (OSError, RuntimeError) as exc:
        manifest.update(status="failed", error=str(exc))
        save()
        raise RuntimeError(f"Sweep failed: {exc}. Partial results: {manifest_path}") from exc
    return {"run_directory": str(batch), "manifest_path": str(manifest_path), **manifest}
