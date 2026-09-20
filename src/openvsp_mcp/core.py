"""Run OpenVSP's Analysis API with persistent artifacts and explicit success checks."""

from __future__ import annotations

import hashlib
import json
import math
import os
import signal
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from shutil import copy2, which

from .describe import describe_geometry
from .geometry import preflight_commands
from .models import OpenVSPRequest, OpenVSPResponse
from .quality import history_diagnostics
from .runtime import OperationCancelled, check_cancelled, commit_lock
from .settings import read_effective_settings
from .version import version_info

OPENVSP_BIN = os.environ.get("OPENVSP_BIN") or which("vspscript") or which("vsp") or "vsp"
# Retained for health/config compatibility. The Analysis API finds vspaero via SetVSPAEROPath.
VSPAERO_BIN = os.environ.get("VSPAERO_BIN") or which("vspaero") or "vspaero"


def _quote(value: str | Path) -> str:
    return json.dumps(str(value), ensure_ascii=False)


def _ensure_statement(command: str) -> str:
    return command.rstrip() if command.rstrip().endswith((";", "}")) else command.rstrip() + ";"


def _input(analysis: str, name: str, value: float | str) -> str:
    if isinstance(value, str):
        kind, atype, literal = "String", "string", _quote(value)
    elif isinstance(value, int):
        kind, atype, literal = "Int", "int", str(value)
    else:
        kind, atype, literal = "Double", "double", repr(value)
    return f'{{ array<{atype}> x={{{literal}}}; Set{kind}AnalysisInput("{analysis}","{name}",x); }}'


def _write_script(
    request: OpenVSPRequest, run_dir: Path, token: str, operation: str = "modify"
) -> Path:
    model = run_dir / f"{request.case_name}.vsp3"
    # An integer main with an explicit return fixes undefined exit values from void main.
    lines = [
        (
            "int CheckErrors() { int n=GetNumTotalErrors(); "
            "while(GetNumTotalErrors()>0) { ErrorObj e=PopLastError(); "
            "Print(e.GetErrorString()); } return n; }"
        ),
        "int main() {",
        "ClearVSPModel();",
        (
            f"ReadVSPFile({_quote(run_dir / 'input' / 'source.vsp3')});"
            if operation != "create"
            else "// Start with an empty model."
        ),
        "if(CheckErrors()!=0) return 1;",
    ]
    lines.extend(_ensure_statement(c.command) for c in request.set_commands)
    for edit in request.parameter_edits:
        pid, value = _quote(edit.parm_id), repr(edit.value)
        lines += [
            f'if(!ValidParm({pid})) {{ Print("PARAMETER_ERROR: invalid ID"); return 10; }}',
            (
                f"if({value}<GetParmLowerLimit({pid}) || {value}>GetParmUpperLimit({pid})) "
                '{ Print("PARAMETER_ERROR: value outside limits"); return 10; }'
            ),
            f"SetParmVal({pid},{value});",
        ]
    lines += [
        "Update();",
        "if(CheckErrors()!=0) return 2;",
        'Print("OPENVSP_VERSION:"+GetVSPVersion());',
    ]
    for index, edit in enumerate(request.parameter_edits):
        pid, value = _quote(edit.parm_id), repr(edit.value)
        lines += [
            (
                f"if(abs(GetParmVal({pid})-{value})>1e-9*(1+abs({value}))) "
                '{ Print("PARAMETER_ERROR: requested value did not take effect"); return 10; }'
            ),
            f'Print("PARAMETER_VALUE_{index}:"+formatFloat(GetParmVal({pid}),"",0,17));',
        ]
    lines += [
        f"SetVSP3FileName({_quote(model)});",
        f"WriteVSPFile({_quote(model)},SET_ALL);",
        "if(CheckErrors()!=0) return 3;",
    ]
    if request.run_vspaero or operation == "preflight":
        lines.extend(
            preflight_commands(request.analysis.thick_geom_set, request.analysis.thin_geom_set)
        )
    if operation == "preview":
        lines += [
            f"ExportFile({_quote(run_dir / 'preview.svg')},SET_ALL,EXPORT_SVG);",
            f"ExportFile({_quote(run_dir / 'preview.stl')},SET_ALL,EXPORT_STL);",
            "if(CheckErrors()!=0) return 9;",
        ]
    if request.run_vspaero:
        solver = which(VSPAERO_BIN)
        if not solver and Path(VSPAERO_BIN).is_file():
            solver = str(Path(VSPAERO_BIN).resolve())
        if not solver and os.environ.get("VSPAERO_BIN"):
            raise RuntimeError(f"Configured VSPAERO_BIN was not found: {VSPAERO_BIN}")
        if solver:
            lines.append(f"SetVSPAEROPath({_quote(Path(solver).resolve().parent)});")
            lines.append("if(CheckErrors()!=0) return 4;")
        a = request.analysis
        for analysis in ["VSPAEROComputeGeometry", "VSPAEROSweep"]:
            lines.append(f'SetAnalysisInputDefaults("{analysis}");')
            values = {"GeomSet": a.thick_geom_set, "ThinGeomSet": a.thin_geom_set, "UseModeFlag": 0}
            if analysis == "VSPAEROSweep":
                values.update(
                    {
                        "RefFlag": 0,
                        "Sref": a.sref,
                        "bref": a.bref,
                        "cref": a.cref,
                        "AlphaStart": a.alpha,
                        "AlphaEnd": a.alpha,
                        "AlphaNpts": 1,
                        "BetaStart": a.beta,
                        "BetaEnd": a.beta,
                        "BetaNpts": 1,
                        "MachStart": a.mach,
                        "MachEnd": a.mach,
                        "MachNpts": 1,
                        "ReCref": a.reynolds,
                        "ReCrefEnd": a.reynolds,
                        "ReCrefNpts": 1,
                        "Vinf": a.vinf,
                        "Rho": a.rho,
                        "Xcg": a.xcg,
                        "Ycg": a.ycg,
                        "Zcg": a.zcg,
                        "NCPU": a.ncpu,
                        "UnsteadyType": 0,
                        "FixedWakeFlag": int(a.fixed_wake),
                        "WakeNumIter": a.wake_iterations,
                        "NumWakeNodes": a.wake_nodes,
                        "ForwardGMRESConvergenceFactor": a.forward_gmres_tolerance_factor,
                        "RedirectFile": str(run_dir / "solver.log"),
                    }
                )
            lines.extend(_input(analysis, n, v) for n, v in values.items())
            lines += [
                "if(CheckErrors()!=0) return 4;",
                f'PrintAnalysisInputs("{analysis}");',
                (
                    f'{{ string rid=ExecAnalysis("{analysis}"); '
                    "if(CheckErrors()!=0 || rid.length()==0) return 5; }"
                ),
            ]
        lines += [
            'string hid=FindLatestResultsID("VSPAERO_History");',
            "if(CheckErrors()!=0 || hid.length()==0) return 6;",
            f"WriteResultsCSVFile(hid,{_quote(run_dir / 'history.csv')});",
            "if(CheckErrors()!=0) return 7;",
        ]
    lines += [f'Print("OPENVSP_MCP_SUCCESS_{token}");', "return 0;", "}"]
    script = run_dir / "automation.vspscript"
    script.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return script


def _run_script(script: Path, log: Path, timeout: int) -> int:
    """Keep logs on failure; terminate the solver process group on POSIX timeouts."""
    check_cancelled()
    deadline = time.monotonic() + timeout
    with (
        log.open("wb") as stream,
        subprocess.Popen(
            [OPENVSP_BIN, "-script", str(script)],
            cwd=script.parent,
            stdout=stream,
            stderr=subprocess.STDOUT,
            start_new_session=(os.name == "posix"),
        ) as proc,
    ):
        try:
            while True:
                check_cancelled()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError(f"OpenVSP timed out after {timeout}s")
                try:
                    return proc.wait(timeout=min(0.1, remaining))
                except subprocess.TimeoutExpired:
                    pass
        except BaseException:
            if os.name == "posix":
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                proc.kill()
            proc.wait()
            raise


def _read_polar(path: Path, request: OpenVSPRequest) -> dict[str, float]:
    header = None
    rows = []
    for line in path.read_text().splitlines():
        cols = line.split()
        if cols[:3] == ["Beta", "Mach", "AoA"]:
            if header is not None or len(set(cols)) != len(cols):
                raise RuntimeError("Duplicate VSPAERO polar header or columns")
            header = cols
        elif header and cols:
            try:
                vals = [float(v) for v in cols]
            except ValueError as exc:
                raise RuntimeError("Malformed VSPAERO polar row") from exc
            if len(vals) != len(header) or not all(math.isfinite(v) for v in vals):
                raise RuntimeError("Invalid or non-finite VSPAERO polar row")
            rows.append(dict(zip(header, vals)))
    if len(rows) != 1:
        raise RuntimeError(f"Expected one flight condition in polar, found {len(rows)}")
    row = rows[0]
    for name in ["CLtot", "CDtot", "CMytot"]:
        if name not in row:
            raise RuntimeError(f"Missing coefficient {name}")
    a = request.analysis
    for name, value in {
        "Mach": a.mach,
        "AoA": a.alpha,
        "Beta": a.beta,
        "Re/1e6": a.reynolds / 1e6,
    }.items():
        if name not in row or not math.isclose(row[name], value, rel_tol=1e-7, abs_tol=1e-8):
            raise RuntimeError(f"Flight condition mismatch for {name}")
    return row


def execute_openvsp(request: OpenVSPRequest, *, operation: str | None = None) -> OpenVSPResponse:
    started = time.monotonic()
    check_cancelled()
    operation = operation or ("run_vspaero" if request.run_vspaero else "modify")
    if operation not in {"run_vspaero", "modify", "create", "preview", "preflight"}:
        raise ValueError("Unknown OpenVSP operation")
    source = None if operation == "create" else Path(request.geometry_file).expanduser().resolve()
    if source is not None:
        describe_geometry(str(source))  # Validate before launching anything.
    if source is not None and source.suffix.lower() != ".vsp3":
        raise RuntimeError("geometry_file must have a .vsp3 extension")
    parent = (
        Path(request.output_dir).expanduser().resolve()
        if request.output_dir
        else source.parent / "openvsp_runs"
        if source is not None
        else Path("openvsp_runs").resolve()
    )
    try:
        parent.mkdir(parents=True, exist_ok=True)
        run_dir = Path(tempfile.mkdtemp(prefix=request.case_name + "-", dir=parent))
    except OSError as exc:
        raise RuntimeError(f"Cannot create output directory: {exc}") from exc
    request = request.model_copy(
        update={
            "geometry_file": str(source) if source else "",
            "run_vspaero": operation == "run_vspaero",
        }
    )
    token = uuid.uuid4().hex
    script = run_dir / "automation.vspscript"
    log = run_dir / "openvsp.log"
    manifest_path = run_dir / "manifest.json"
    manifest = {
        "status": "running",
        "request": request.model_dump(),
        "openvsp_binary": OPENVSP_BIN,
        "vspaero_binary": VSPAERO_BIN,
        "operation": operation,
        "versions": version_info(),
        "timings": {},
    }

    def save_manifest():
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    save_manifest()
    try:
        log.touch()
        if source is not None:
            snapshot = run_dir / "input" / "source.vsp3"
            snapshot.parent.mkdir()
            copy2(source, snapshot)
            source_hash = hashlib.sha256(snapshot.read_bytes()).hexdigest()
            manifest["input_sha256"] = source_hash
        _write_script(request, run_dir, token, operation)
        manifest["timings"]["prepare_seconds"] = time.monotonic() - started
        native_started = time.monotonic()
        rc = _run_script(script, log, request.timeout_seconds)
        manifest["timings"]["native_seconds"] = time.monotonic() - native_started
        validation_started = time.monotonic()
        check_cancelled()
        manifest["returncode"] = rc
        log_text = log.read_text(errors="replace")
        if rc != 0:
            detail = next(
                (
                    s.strip()
                    for s in log_text.splitlines()
                    if "PREFLIGHT_ERROR:" in s or "PARAMETER_ERROR:" in s
                ),
                "",
            )
            raise RuntimeError(f"OpenVSP exited with code {rc}. {detail}".rstrip())
        if f"OPENVSP_MCP_SUCCESS_{token}" not in log_text:
            raise RuntimeError("OpenVSP did not reach the verified script completion marker")
        model = run_dir / f"{request.case_name}.vsp3"
        if not describe_geometry(str(model)).geom_ids:
            raise RuntimeError("Output model contains no geometry")
        coefficients = {}
        result = None
        quality = {}
        warnings = []
        preflight = {}
        effective = {}
        parameters = {}
        for line in log_text.splitlines():
            if line.strip().startswith("OPENVSP_VERSION:"):
                manifest["versions"]["openvsp_version"] = line.strip().split(":", 1)[1]
        for index, edit in enumerate(request.parameter_edits):
            prefix = f"PARAMETER_VALUE_{index}:"
            values = [
                line.strip()[len(prefix) :]
                for line in log_text.splitlines()
                if line.strip().startswith(prefix)
            ]
            if len(values) != 1:
                raise RuntimeError("Missing parameter readback")
            parameters[edit.parm_id] = float(values[0])
            if not math.isclose(parameters[edit.parm_id], edit.value, rel_tol=1e-9, abs_tol=1e-9):
                raise RuntimeError("Parameter readback mismatch")
        if request.run_vspaero or operation == "preflight":
            text = log_text
            if "PREFLIGHT_OK" not in text:
                raise RuntimeError("Missing verified geometry preflight marker")
            preflight = {"status": "passed", "thick_geom_ids": [], "thin_geom_ids": []}
            for line in text.splitlines():
                for kind in ["thick", "thin"]:
                    prefix = f"PREFLIGHT_{kind.upper()}_ID:"
                    if line.strip().startswith(prefix):
                        preflight[kind + "_geom_ids"].append(line.strip()[len(prefix) :])
            a = request.analysis
            if (a.sref, a.bref, a.cref) == (1, 1, 1):
                warnings.append("Unit reference dimensions selected; confirm Sref, Bref and Cref.")
            if a.length_unit == "unspecified":
                warnings.append(
                    "Units unspecified: geometry, references, speed and density must agree."
                )
            warnings.append(
                "Mach, speed, density and Reynolds are independent inputs; verify atmosphere."
            )
            preflight["analysis_inputs"] = a.model_dump()
        if operation == "preview":
            for name in ["preview.svg", "preview.stl"]:
                if not (run_dir / name).is_file() or not (run_dir / name).stat().st_size:
                    raise RuntimeError(f"Missing or empty preview: {name}")
        if request.run_vspaero:
            for ext in ["adb", "polar", "history", "vspgeom", "vspaero"]:
                f = run_dir / f"{request.case_name}.{ext}"
                if not f.is_file() or f.stat().st_size == 0:
                    raise RuntimeError(f"Missing or empty solver artifact: {f.name}")
            for name in ["solver.log", "history.csv"]:
                f = run_dir / name
                if not f.is_file() or not f.stat().st_size:
                    raise RuntimeError(f"Missing or empty solver artifact: {name}")
            coefficients = _read_polar(run_dir / f"{request.case_name}.polar", request)
            effective = read_effective_settings(run_dir / f"{request.case_name}.vspaero", request)
            result = str(run_dir / f"{request.case_name}.adb")
            quality = history_diagnostics(run_dir / f"{request.case_name}.history")
            manifest["numerical_quality"] = quality
            if quality["history_status"] == "invalid":
                raise RuntimeError("Invalid numerical history; see numerical diagnostics")
        elif operation == "modify":
            # Preserve modify's in-place behavior, but replace only after validation.
            fd, tmp = tempfile.mkstemp(prefix=".openvsp-", suffix=".vsp3", dir=source.parent)
            os.close(fd)
            try:
                copy2(model, tmp)
                with commit_lock:
                    check_cancelled()
                    if hashlib.sha256(source.read_bytes()).hexdigest() != source_hash:
                        raise RuntimeError(
                            "Input model changed during the run; refusing to overwrite it"
                        )
                    os.replace(tmp, source)
            finally:
                Path(tmp).unlink(missing_ok=True)
        manifest.update(
            status="success",
            coefficients=coefficients,
            warnings=warnings,
            preflight=preflight,
            numerical_quality=quality,
            effective_settings=effective,
            parameter_values=parameters,
        )
        manifest["timings"].update(
            validation_seconds=time.monotonic() - validation_started,
            total_seconds=time.monotonic() - started,
        )
        save_manifest()
        return OpenVSPResponse(
            script_path=str(script),
            result_path=result,
            geometry_path=str(model),
            run_directory=str(run_dir),
            log_path=str(log),
            manifest_path=str(manifest_path),
            artifacts={
                str(f.relative_to(run_dir)): str(f) for f in run_dir.rglob("*") if f.is_file()
            },
            coefficients=coefficients,
            analysis_inputs=request.analysis.model_dump()
            if request.run_vspaero or operation == "preflight"
            else {},
            operation=operation,
            warnings=warnings,
            preflight=preflight,
            numerical_quality=quality,
            versions=manifest["versions"],
            effective_settings=effective,
            parameter_values=parameters,
            timings=manifest["timings"],
        )
    except (OSError, RuntimeError) as exc:
        with log.open("a", encoding="utf-8") as stream:
            stream.write(f"\nopenvsp-mcp: {exc}\n")
        manifest.update(
            status="cancelled" if isinstance(exc, OperationCancelled) else "failed", error=str(exc)
        )
        manifest["timings"]["total_seconds"] = time.monotonic() - started
        save_manifest()
        error_type = OperationCancelled if isinstance(exc, OperationCancelled) else RuntimeError
        raise error_type(f"{exc}. Run artifacts: {run_dir}; log: {log}") from exc
