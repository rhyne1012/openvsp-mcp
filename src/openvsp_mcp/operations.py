"""Snapshot-only native geometry operations with retained evidence and one deadline."""

import json
import math
import tempfile
import time
import uuid
from pathlib import Path
from shutil import copy2

from pydantic import BaseModel, ConfigDict, Field, model_validator

from . import core
from .describe import describe_geometry
from .native import digest_file, executable_identity
from .query import _JSON
from .runtime import (
    OperationCancelled,
    check_cancelled,
    cpu_allocation,
    deadline_scope,
    worker_queue_seconds,
)
from .version import version_info


class GeometryOperationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    geometry_file: str = Field(
        min_length=1, description="Source .vsp3 file, read into a private copy."
    )
    output_dir: str | None = Field(
        default=None,
        description="Parent for unique run directories; defaults to source/openvsp_runs.",
    )
    case_name: str = Field(
        default="geometry",
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$",
        description="Safe prefix for the unique run directory.",
    )
    timeout_seconds: int = Field(
        default=300,
        ge=1,
        le=3600,
        description="Total time budget including worker admission, preparation and native execution.",
    )
    geom_set: int | None = Field(
        default=None,
        ge=0,
        description="Normal geometry set index; defaults to SET_ALL (0) when no mode is supplied.",
    )
    mode_id: str | None = Field(
        default=None,
        min_length=1,
        description="Mode to apply on the private copy, using its normal set; excludes geom_set.",
    )

    @model_validator(mode="after")
    def selection(self):
        if self.geom_set is not None and self.mode_id is not None:
            raise ValueError("Specify either geom_set or mode_id, not both")
        return self


class NativeOperationError(RuntimeError):
    """A failed operation whose log and manifest are retained for diagnosis."""

    def __init__(self, message, details):
        self.details = details
        super().__init__(
            f"{message}; manifest: {details['manifest_path']}; log: {details['log_path']}"
        )


def script_prelude(snapshot: Path, request: GeometryOperationRequest) -> str:
    q = core._quote
    lines = [
        _JSON,
        r"""
int CheckOperationErrors(string phase) {
 int count=GetNumTotalErrors();
 while(GetNumTotalErrors()>0) {
  ErrorObj e=PopLastError();
  Print("MCP_NATIVE_ERROR:{\"code\":"+formatInt(e.GetErrorCode())+
        ",\"message\":"+J(e.GetErrorString())+",\"phase\":"+J(phase)+"}");
 }
 return count;
}
int SelectionError(string message) {
 Print("MCP_NATIVE_ERROR:{\"code\":null,\"message\":"+J(message)+
       ",\"phase\":\"selection\"}"); return 2;
}
int main() {
 ClearVSPModel();
""",
        f"ReadVSPFile({q(snapshot)}); Update();",
        'if(CheckOperationErrors("load")!=0) return 1;',
    ]
    if request.mode_id is not None:
        mode = q(request.mode_id)
        lines += [
            "array<string> modes=GetAllModes();",
            f'if(modes.find({mode})<0) return SelectionError("Unknown mode_id");',
            f"ApplyModeSettings({mode}); Update();",
            'if(CheckOperationErrors("mode")!=0) return 2;',
            f'int selectedSet=int(GetParmVal(FindParm({mode},"NormalSet","Sets")));',
        ]
    else:
        lines.append(f"int selectedSet={request.geom_set or 0};")
    lines += [
        'if(selectedSet<0 || selectedSet>=GetNumSets()) return SelectionError("Invalid set index");',
        "array<string> selectedIDs=GetGeomSetAtIndex(selectedSet);",
        'if(CheckOperationErrors("selection")!=0) return 2;',
        'if(selectedIDs.length()==0) return SelectionError("Selected set has no geometry");',
    ]
    return "\n".join(lines)


def script_end(token: str, json_expression: str) -> str:
    return (
        '\nif(CheckOperationErrors("operation")!=0) return 3;\n'
        f"Print({core._quote('MCP_OPERATION_' + token + ':')}+({json_expression}));\n"
        "return 0; }\n"
    )


def _strict_json(text):
    def reject(value):
        raise ValueError(f"Non-finite native result: {value}")

    def finite(value):
        if isinstance(value, float) and not math.isfinite(value):
            reject(value)
        if isinstance(value, dict):
            for child in value.values():
                finite(child)
        elif isinstance(value, list):
            for child in value:
                finite(child)

    result = json.loads(text, parse_constant=reject)
    finite(result)
    return result


def run_operation(request, operation, build_script, validate):
    """Execute a fixed generated script, validate artifacts, and retain all run evidence."""
    started = time.monotonic()
    source = Path(request.geometry_file).expanduser().resolve()
    parent = (
        Path(request.output_dir).expanduser().resolve()
        if request.output_dir
        else source.parent / "openvsp_runs"
    )
    parent.mkdir(parents=True, exist_ok=True)
    run = Path(tempfile.mkdtemp(prefix=request.case_name + "-", dir=parent))
    script, log, manifest_path = (
        run / name for name in ("automation.vspscript", "openvsp.log", "manifest.json")
    )
    log.touch()
    manifest = {
        "operation": operation,
        "status": "running",
        "run_directory": str(run),
        "manifest_path": str(manifest_path),
        "script_path": str(script),
        "log_path": str(log),
        "request": request.model_dump(mode="json"),
        "versions": version_info(),
        "timings": {"worker_queue_seconds": worker_queue_seconds()},
        "artifacts": {},
    }

    def save():
        manifest["timings"]["total_seconds"] = time.monotonic() - started + worker_queue_seconds()
        manifest_path.write_text(json.dumps(manifest, indent=2, allow_nan=False) + "\n")

    save()
    try:
        deadline = started + request.timeout_seconds - worker_queue_seconds()
        with deadline_scope(deadline):
            if source.suffix.lower() != ".vsp3":
                raise RuntimeError("geometry_file must have a .vsp3 extension")
            snapshot = run / "input" / "source.vsp3"
            snapshot.parent.mkdir()
            copy2(source, snapshot)
            describe_geometry(str(snapshot))
            manifest["input_sha256"] = digest_file(snapshot)
            manifest["versions"]["native_identity"] = {
                "openvsp": executable_identity(core.OPENVSP_BIN)
            }
            check_cancelled()
            token = uuid.uuid4().hex
            script.write_text(build_script(snapshot, run, token), encoding="utf-8")
            manifest["timings"]["prepare_seconds"] = time.monotonic() - started
            queued = time.monotonic()
            with cpu_allocation(1):
                manifest["timings"]["resource_wait_seconds"] = time.monotonic() - queued
                native = time.monotonic()
                manifest["returncode"] = core._run_script(
                    script, log, max(0.001, deadline - native)
                )
            manifest["timings"]["native_seconds"] = time.monotonic() - native
            check_cancelled()
            output = log.read_text(errors="replace")
            prefix = "MCP_OPERATION_" + token + ":"
            payloads = [
                line.strip()[len(prefix) :]
                for line in output.splitlines()
                if line.strip().startswith(prefix)
            ]
            if manifest["returncode"] != 0:
                raise RuntimeError(f"OpenVSP exited with code {manifest['returncode']}")
            if len(payloads) != 1:
                raise RuntimeError("OpenVSP must emit exactly one operation result")
            payload = _strict_json(payloads[0])
            if not isinstance(payload, dict):
                raise TypeError("Native result must be an object")
            validation = time.monotonic()
            data = validate(run, payload)
            if not isinstance(data, dict) or (set(data) & (set(manifest) - {"artifacts"})):
                raise RuntimeError("Operation validator returned reserved response fields")
            manifest.update(data)
            manifest["versions"]["openvsp_version"] = payload.get("openvsp_version")
            manifest["timings"]["validation_seconds"] = time.monotonic() - validation
            check_cancelled()
        manifest["status"] = "ok"
        save()
        return manifest
    except Exception as exc:
        manifest["status"] = "cancelled" if isinstance(exc, OperationCancelled) else "error"
        manifest["error"] = str(exc)
        errors = []
        for line in log.read_text(errors="replace").splitlines():
            if line.strip().startswith("MCP_NATIVE_ERROR:"):
                try:
                    errors.append(_strict_json(line.strip().split(":", 1)[1]))
                except ValueError:
                    pass
        manifest["native_errors"] = errors
        save()
        if isinstance(exc, OperationCancelled):
            raise
        summary = str(exc)
        if errors:
            summary += ": " + "; ".join(
                f"{e.get('phase')} [{e.get('code')}]: {e.get('message')}"
                for e in errors
                if isinstance(e, dict)
            )
        raise NativeOperationError(summary, manifest) from exc
