"""Regression gates for API alignment, malformed outputs, and transport liveness."""

import asyncio
import json
import os
import signal
import sys
import threading
import time
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client
from pydantic import ValidationError

from openvsp_mcp import core
from openvsp_mcp.models import (
    OpenVSPRequest,
    ParameterEditRequest,
    QueryRequest,
    ResultRequest,
    VSPAeroSettings,
)
from openvsp_mcp.quality import history_diagnostics
from openvsp_mcp.query import query_model
from openvsp_mcp.results import read_results
from openvsp_mcp.runtime import run_async
from openvsp_mcp.settings import read_effective_settings


@pytest.mark.parametrize(
    "settings",
    [
        {"wake_iterations": 1},
        {"wake_iterations": 256},
        {"ncpu": 256},
        {"forward_gmres_tolerance_factor": 0},
        {"forward_gmres_tolerance_factor": float("nan")},
    ],
)
def test_native_ranges(settings):
    with pytest.raises(ValidationError):
        VSPAeroSettings(**settings)


def test_fixed_wake_and_mode_are_explicit(tmp_path):
    r = OpenVSPRequest(geometry_file="unused", analysis=VSPAeroSettings(fixed_wake=True))
    script = core._write_script(r, tmp_path, "test").read_text()
    assert '"FixedWakeFlag",x)' in script
    assert 'array<int> x={1}; SetIntAnalysisInput("VSPAEROSweep","FixedWakeFlag",x)' in script
    assert script.count('"UseModeFlag",x)') == 2
    assert '"UnsteadyType",x)' in script


@pytest.mark.parametrize(
    "tail", ["0 .1 4 2.9 BROKEN .01 .02", "Beta Mach AoA Re/1e6 CLtot CDtot CMytot"]
)
def test_corrupt_polar_tail_is_not_silently_ignored(tmp_path, tail):
    p = tmp_path / "case.polar"
    p.write_text("Beta Mach AoA Re/1e6 CLtot CDtot CMytot\n0 .1 3 2.9 .2 .01 .02\n" + tail + "\n")
    with pytest.raises(RuntimeError):
        core._read_polar(p, OpenVSPRequest(geometry_file="unused"))


@pytest.mark.parametrize("tail", ["3 .1 3 0 nan .01 .02", "3 .1 3 0 BROKEN .01 .02", "3 .1 3"])
def test_invalid_final_history_is_reported(tmp_path, tail):
    p = tmp_path / "case.history"
    p.write_text(
        "Iter Mach AoA Beta CLtot CDtot CMytot\n1 .1 3 0 .2 .01 .02\n2 .1 3 0 .21 .01 .02\n"
        + tail
        + "\n"
    )
    result = history_diagnostics(p)
    assert result["history_status"] == "invalid"
    assert result["invalid_line_numbers"] == [4]
    assert "last_iteration" not in result


def test_effective_solver_settings_reject_clamping(tmp_path):
    p = tmp_path / "case.vspaero"
    p.write_text(
        "Sref=1\nBref=1\nCref=1\nX_cg=0\nY_cg=0\nZ_cg=0\nMach=.1\nAoA=3\nBeta=0\nVinf=34.03\nRho=1.225\nReCref=2900000\nWakeIters=3\nNumWakeNodes=32\nForwardGMRESConvergenceFactor=1\nVSP_StabilityType=0\n"
    )
    with pytest.raises(RuntimeError, match="WakeIters"):
        read_effective_settings(p, OpenVSPRequest(geometry_file="unused"))
    p.write_text(p.read_text().replace("WakeIters=3", "WakeIters=0"))
    result = read_effective_settings(
        p, OpenVSPRequest(geometry_file="unused", analysis=VSPAeroSettings(fixed_wake=True))
    )
    assert result["solver_file_values"]["WakeIters"] == 0


def test_parameter_request_rejects_duplicates():
    with pytest.raises(ValidationError):
        ParameterEditRequest(geometry_file="unused", edits=[{"parm_id": "x", "value": 1}] * 2)


def test_capability_cache_key_and_copies(monkeypatch, tmp_path):
    binary = tmp_path / "binary"
    binary.write_text("binary")
    monkeypatch.setattr(core, "OPENVSP_BIN", str(binary))
    calls = []

    def native(script, log, timeout):
        text = script.read_text()
        prefix = text.split('Print("MCP_QUERY_')[1].split(":")[0]
        log.write_text(
            "MCP_QUERY_"
            + prefix
            + ":"
            + json.dumps({"analyses": ["VSPAEROSweep"], "openvsp_version": "3.51.3"})
        )
        calls.append(script)
        return 0

    monkeypatch.setattr(core, "_run_script", native)
    first = query_model(QueryRequest())
    first["analyses"].clear()
    second = query_model(QueryRequest())
    assert second["cache_hit"] and second["analyses"] == ["VSPAEROSweep"]
    binary.write_text("replacement binary")
    assert not query_model(QueryRequest())["cache_hit"]
    assert len(calls) == 2


def test_saved_result_subset_and_bounded_tail(tmp_path):
    p = tmp_path / "manifest.json"
    p.write_text(
        json.dumps(
            {
                "status": "success",
                "operation": "run_vspaero",
                "coefficients": {"CLtot": 0.2, "CDtot": 0.01},
            }
        )
    )
    (tmp_path / "solver.log").write_text("old\n" * 30000 + "one\ntwo\n")
    result = read_results(
        ResultRequest(
            manifest_file=str(p), coefficient_names=["CLtot"], log="solver", log_tail_lines=2
        )
    )
    assert result["coefficients"] == {"CLtot": 0.2} and result["log_tail"] == "one\ntwo"
    with pytest.raises(RuntimeError, match="Unknown coefficient"):
        read_results(ResultRequest(manifest_file=str(p), coefficient_names=["typo"]))


def test_async_workers_are_bounded():
    active = 0
    maximum = 0
    lock = threading.Lock()

    def work():
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
        time.sleep(0.05)
        with lock:
            active -= 1

    async def run():
        await asyncio.gather(*(run_async(work) for _ in range(6)))

    asyncio.run(run())
    assert maximum == 2


@pytest.mark.skipif(os.name != "posix", reason="POSIX process groups")
def test_stdio_ping_and_cancel_reaps_process_group(tmp_path):
    """Real transport, real blocking child: ping and cancellation must remain live."""
    pids = tmp_path / "pids.json"
    binary = tmp_path / "fake-openvsp"
    binary.write_text(f"""#!{sys.executable}
import json,os,subprocess,sys,time
from pathlib import Path
child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'])
Path({str(pids)!r}).write_text(json.dumps([os.getpid(),child.pid]))
time.sleep(60)
""")
    binary.chmod(0o755)
    model = tmp_path / "model.vsp3"
    model.write_text("<Vsp_Geometry><Vehicle/></Vsp_Geometry>")

    async def run():
        server = StdioServerParameters(
            command=sys.executable,
            args=["-m", "openvsp_mcp"],
            env=dict(os.environ, OPENVSP_BIN=str(binary), VSPAERO_BIN=str(tmp_path / "missing")),
        )
        async with stdio_client(server) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            request_id = session._request_id  # Capture the outgoing tool request for cancellation.
            task = asyncio.create_task(
                session.call_tool(
                    "openvsp.modify",
                    {
                        "request": {
                            "geometry_file": str(model),
                            "output_dir": str(tmp_path / "runs"),
                        }
                    },
                )
            )
            try:
                for _ in range(150):
                    if pids.exists():
                        break
                    await asyncio.sleep(0.02)
                assert pids.exists()
                await asyncio.wait_for(session.send_ping(), timeout=1)
                await session.send_notification(
                    types.ClientNotification(
                        types.CancelledNotification(
                            params=types.CancelledNotificationParams(
                                requestId=request_id, reason="regression test"
                            )
                        )
                    )
                )
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                for _ in range(150):
                    manifests = list((tmp_path / "runs").glob("*/manifest.json"))
                    if manifests and json.loads(manifests[0].read_text())["status"] == "cancelled":
                        break
                    await asyncio.sleep(0.02)
                assert json.loads(manifests[0].read_text())["status"] == "cancelled"
                await asyncio.wait_for(session.send_ping(), timeout=1)
                parent, child = json.loads(pids.read_text())
                with pytest.raises(ProcessLookupError):
                    os.kill(parent, 0)
                # A reparented child may briefly remain a zombie on Linux; /proc state is enough.
                try:
                    os.kill(child, 0)
                except ProcessLookupError:
                    pass
                else:
                    stat = Path(f"/proc/{child}/stat")
                    assert stat.exists() and stat.read_text().split()[2] == "Z"
            finally:
                if not task.done():
                    task.cancel()
                if pids.exists():
                    try:
                        os.killpg(json.loads(pids.read_text())[0], signal.SIGKILL)
                    except ProcessLookupError:
                        pass

    asyncio.run(run())
