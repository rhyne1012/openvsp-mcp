"""Bounded executable and API probes, with no dependency on a user's model."""

import os
import re
import subprocess
import tempfile
from pathlib import Path
from shutil import which
from typing import Any

from . import core
from .runtime import OperationCancelled, check_cancelled, cpu_pool
from .version import version_info


def _executable(value: str) -> str:
    resolved = which(value)
    if not resolved or not os.access(resolved, os.X_OK):
        raise RuntimeError(f"Executable not found or not executable: {value}")
    return str(Path(resolved).resolve())


def health_check() -> dict[str, Any]:
    check_cancelled()
    result = {"status": "ok", **version_info(), "checks": {}}
    result["resources"] = {
        "server_cpu_budget": cpu_pool.capacity,
        "scope": "Per-process native operation admission; not a machine-wide CPU quota",
    }
    for name, configured in [("openvsp", core.OPENVSP_BIN), ("vspaero", core.VSPAERO_BIN)]:
        check = {"configured_path": configured, "status": "error"}
        try:
            binary = _executable(configured)
            check["resolved_path"] = binary
            if name == "openvsp":
                with tempfile.TemporaryDirectory(prefix="openvsp-health-") as tmp:
                    script = Path(tmp) / "probe.vspscript"
                    script.write_text("""int main() {
 ClearVSPModel(); string id=AddGeom("POD", ""); Update();
 if (id.length()==0 || GetNumTotalErrors()!=0) return 1;
 array<string>@ inputs=GetAnalysisInputNames("VSPAEROComputeGeometry");
 if(inputs.find("GeomSet")<0 || inputs.find("ThinGeomSet")<0) return 2;
 array<string>@ sweep=GetAnalysisInputNames("VSPAEROSweep");
 if(sweep.find("FixedWakeFlag")<0 || sweep.find("ForwardGMRESConvergenceFactor")<0 ||
    sweep.find("UseModeFlag")<0 || sweep.find("UnsteadyType")<0) return 3;
 Print(GetVSPVersion()); Print("OPENVSP_HEALTH_API_OK"); return 0;
}
""")
                    log = Path(tmp) / "probe.log"
                    rc = core._run_script(script, log, 15)
                    output = log.read_text(errors="replace")
                if rc != 0 or "OPENVSP_HEALTH_API_OK" not in output:
                    raise RuntimeError(f"API probe failed (exit {rc}): {output[-1500:]}")
            else:
                proc = subprocess.run(
                    [binary, "-version"], capture_output=True, text=True, timeout=10, check=False
                )
                output = proc.stdout + proc.stderr
                # VSPAERO 7.2.2 exits 1 for its successful version-only command.
                # Accept that exact response, never a nonzero solver execution.
                version_only = re.fullmatch(r"VSPAERO \d+\.\d+\.\d+\s*", proc.stdout.strip())
                if proc.returncode != 0 and not (
                    proc.returncode == 1 and version_only and not proc.stderr.strip()
                ):
                    raise RuntimeError(f"Version probe failed (exit {proc.returncode})")
            match = re.search(r"(?:OpenVSP|VSPAERO)\s+(?:v\.?\s*)?(\d+\.\d+\.\d+)", output)
            if not match:
                raise RuntimeError("Probe did not return a recognized version")
            check.update(status="ok", version=match[1])
        except OperationCancelled:
            raise
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
            check["error"] = str(exc)
            result["status"] = "error"
        result["checks"][name] = check
    result["tested_version_pair"] = (
        result["checks"]["openvsp"].get("version") == "3.51.3"
        and result["checks"]["vspaero"].get("version") == "7.2.2"
    )
    result["scope"] = "Executable/API readiness; no aerodynamic accuracy or convergence claim."
    return result
