from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from importlib import resources
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from openvsp_mcp import core
from openvsp_mcp.describe import describe_geometry
from openvsp_mcp.fastapi_app import create_app
from openvsp_mcp.models import OpenVSPRequest, VSPAeroSettings, VSPCommand

MODEL = """<Vsp_Geometry><Vehicle><Geom>
<ParmContainer><ID>wing1</ID><Name>Wing</Name></ParmContainer>
<GeomBase><TypeName>Wing</TypeName></GeomBase>
<Geom><ParmContainer><ID>nested</ID></ParmContainer></Geom>
</Geom></Vehicle></Vsp_Geometry>"""
POLAR = "Beta Mach AoA Re/1e6 CLtot CDtot CMytot\n0 .1 3 2.9 .233 .0096 .068\n"


@pytest.fixture
def request_model(tmp_path):
    source = tmp_path / 'input space "quoted".vsp3'
    source.write_text(MODEL)
    return OpenVSPRequest(geometry_file=str(source), output_dir=str(tmp_path / "runs"))


@pytest.fixture
def simulator(monkeypatch):
    """Simulate external artifacts, including independent failures in the solver contract."""
    behavior = {"returncode": 0, "marker": True, "omit": None, "polar": POLAR}

    def run(script, log, timeout):
        run_dir = script.parent
        text = script.read_text()
        marker = re.search(r"OPENVSP_MCP_SUCCESS_[a-f0-9]+", text).group()
        log.write_text(marker if behavior["marker"] else "Compile error")
        snapshot = run_dir / "input" / "source.vsp3"
        match = re.search(r'SetVSP3FileName\((".*")\);', text)
        model = Path(json.loads(match.group(1)))
        model.write_text(snapshot.read_text().replace("<Name>Wing</Name>", "<Name>Edited</Name>"))
        for ext in ["adb", "history", "vspgeom", "vspaero"]:
            model.with_suffix("." + ext).write_text("fresh output")
        model.with_suffix(".polar").write_text(behavior["polar"])
        (run_dir / "history.csv").write_text("CL,0.233\n")
        (run_dir / "solver.log").write_text("Done\n")
        if behavior["omit"]:
            (run_dir / behavior["omit"]).unlink()
        if behavior.get("concurrent_edit"):
            behavior["concurrent_edit"].write_text("external edit")
        return behavior["returncode"]

    monkeypatch.setattr(core, "_run_script", run)
    return behavior


def test_inspect_is_read_only_and_counts_only_components(request_model, monkeypatch):
    source = Path(request_model.geometry_file)
    before, mtime = source.read_bytes(), source.stat().st_mtime_ns
    monkeypatch.setattr(
        subprocess, "Popen", lambda *a, **kw: pytest.fail("inspect launched a binary")
    )
    info = describe_geometry(str(source))
    assert info.geom_ids == ["wing1"]
    assert info.wing_names == ["Wing"]
    assert source.read_bytes() == before
    assert source.stat().st_mtime_ns == mtime


def test_bundled_model_can_be_inspected(tmp_path):
    source = resources.files("openvsp_mcp.data") / "rect_wing.vsp3"
    dest = tmp_path / "rect.vsp3"
    dest.write_bytes(source.read_bytes())
    assert "RectWing" in describe_geometry(str(dest)).wing_names


@pytest.mark.parametrize("xml", ["broken XML", "<Other/>"])
def test_invalid_model_rejected_without_running(tmp_path, monkeypatch, xml):
    path = tmp_path / "bad.vsp3"
    path.write_text(xml)
    monkeypatch.setattr(core, "_run_script", lambda *a: pytest.fail("invalid model launched"))
    with pytest.raises(RuntimeError):
        core.execute_openvsp(OpenVSPRequest(geometry_file=str(path)))
    assert not (tmp_path / "openvsp_runs").exists()


def test_solver_outputs_persist_and_input_unchanged(request_model, simulator):
    source = Path(request_model.geometry_file)
    before = source.read_bytes()
    first = core.execute_openvsp(request_model)
    second = core.execute_openvsp(request_model)
    assert first.run_directory != second.run_directory
    assert source.read_bytes() == before
    assert all(Path(p).is_file() for p in first.artifacts.values())
    assert first.coefficients["CLtot"] == 0.233
    assert Path(first.result_path).is_file()
    assert json.loads(Path(first.manifest_path).read_text())["status"] == "success"
    script = Path(first.script_path).read_text()
    assert "int main()" in script and "return 0;" in script
    assert script.index('ExecAnalysis("VSPAEROComputeGeometry")') < script.index(
        'ExecAnalysis("VSPAEROSweep")'
    )
    # Archived scripts read their own input snapshot even if the source is moved later.
    assert str(source) not in script
    assert Path(first.artifacts["input/source.vsp3"]).read_bytes() == before


def test_modify_applies_only_validated_output(request_model, simulator):
    request = request_model.model_copy(update={"run_vspaero": False})
    result = core.execute_openvsp(request)
    assert describe_geometry(request.geometry_file).wing_names == ["Edited"]
    assert result.result_path is None
    assert "VSPAEROSweep" not in Path(result.script_path).read_text()


@pytest.mark.parametrize("returncode", [1, 16, 160, 224])
def test_nonzero_exit_is_failure_and_preserves_input(request_model, simulator, returncode):
    simulator["returncode"] = returncode
    source = Path(request_model.geometry_file)
    before = source.read_bytes()
    with pytest.raises(RuntimeError, match=f"code {returncode}.*Run artifacts:"):
        core.execute_openvsp(request_model.model_copy(update={"run_vspaero": False}))
    assert source.read_bytes() == before
    manifest = next(Path(request_model.output_dir).glob("*/manifest.json"))
    assert json.loads(manifest.read_text())["status"] == "failed"
    assert manifest.with_name("openvsp.log").is_file()


def test_zero_exit_without_completion_marker_is_failure(request_model, simulator):
    simulator["marker"] = False
    with pytest.raises(RuntimeError, match="completion marker"):
        core.execute_openvsp(request_model)


@pytest.mark.parametrize(
    "missing",
    [
        "case.adb",
        "case.polar",
        "case.history",
        "case.vspgeom",
        "case.vspaero",
        "solver.log",
        "history.csv",
    ],
)
def test_zero_exit_missing_artifact_is_failure(request_model, simulator, missing):
    simulator["omit"] = missing
    # A stale file outside the new run must never satisfy the artifact check.
    Path(request_model.geometry_file).with_name(missing).write_text("stale")
    with pytest.raises(RuntimeError, match="Missing or empty solver artifact"):
        core.execute_openvsp(request_model)


@pytest.mark.parametrize(
    "polar, message",
    [
        (POLAR.replace("0 .1 3", "0 .2 3"), "Flight condition mismatch"),
        (POLAR.replace(".233", "nan"), "non-finite"),
        (POLAR + "0 .1 3 2.9 .24 .01 .07\n", "Expected one flight condition"),
        ("", "Missing or empty"),
        ("Beta Mach AoA\n0 .1 3\n", "Missing coefficient"),
    ],
)
def test_invalid_solver_results_rejected(request_model, simulator, polar, message):
    simulator["polar"] = polar
    with pytest.raises(RuntimeError, match=message):
        core.execute_openvsp(request_model)


def test_concurrent_source_change_not_overwritten(request_model, simulator):
    simulator["concurrent_edit"] = Path(request_model.geometry_file)
    with pytest.raises(RuntimeError, match="changed during the run"):
        core.execute_openvsp(request_model.model_copy(update={"run_vspaero": False}))
    assert Path(request_model.geometry_file).read_text() == "external edit"


def test_missing_binary_failure_is_archived(request_model, monkeypatch):
    monkeypatch.setattr(core, "OPENVSP_BIN", str(Path(request_model.output_dir) / "missing"))
    with pytest.raises(RuntimeError, match="Run artifacts:"):
        core.execute_openvsp(request_model)
    manifest = next(Path(request_model.output_dir).glob("*/manifest.json"))
    assert json.loads(manifest.read_text())["status"] == "failed"


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group timeout regression")
def test_timeout_terminates_child_process(tmp_path, monkeypatch):
    original_popen = subprocess.Popen
    launched = []

    def sleeper(*args, **kwargs):
        proc = original_popen([sys.executable, "-c", "import time; time.sleep(60)"], **kwargs)
        launched.append(proc)
        return proc

    monkeypatch.setattr(subprocess, "Popen", sleeper)
    with pytest.raises(RuntimeError, match="timed out after 1s"):
        core._run_script(tmp_path / "test.vspscript", tmp_path / "log", 1)
    assert launched[0].poll() is not None


@pytest.mark.parametrize(
    "settings",
    [{"mach": float("nan")}, {"alpha": float("inf")}, {"sref": 0}, {"ncpu": 0}, {"typo": 3}],
)
def test_analysis_settings_validation(settings):
    with pytest.raises(ValidationError):
        VSPAeroSettings(**settings)


def test_case_name_prevents_output_path_escape():
    with pytest.raises(ValidationError):
        OpenVSPRequest(geometry_file="input.vsp3", case_name="../outside")


def test_script_escapes_paths_and_preserves_blocks(tmp_path, request_model):
    run_dir = tmp_path / 'a "quoted" path'
    run_dir.mkdir()
    request = request_model.model_copy(
        update={"set_commands": [VSPCommand(command="if (true) { Update(); }")]}
    )
    script = core._write_script(request, run_dir, "test").read_text()
    assert '\\"quoted\\"' in script
    assert "if (true) { Update(); }\n" in script


def test_fastapi_modes_and_failure_details(request_model, simulator):
    client = TestClient(create_app())
    payload = request_model.model_dump()
    modified = client.post("/vsp/modify", json=payload | {"run_vspaero": True})
    assert modified.status_code == 200
    assert modified.json()["result_path"] is None
    solved = client.post("/vsp/run", json=payload | {"run_vspaero": False})
    assert solved.status_code == 200
    assert solved.json()["coefficients"]["CLtot"] == 0.233
    simulator["marker"] = False
    failed = client.post("/vsp/run", json=payload)
    assert failed.status_code == 500
    assert "Run artifacts:" in failed.json()["detail"]
    assert client.post("/vsp/run", json=payload | {"analysis": {"sref": -1}}).status_code == 422


def test_explicit_missing_solver_does_not_silently_fall_back(request_model, monkeypatch):
    missing = str(Path(request_model.output_dir) / "no_solver")
    monkeypatch.setenv("VSPAERO_BIN", missing)
    monkeypatch.setattr(core, "VSPAERO_BIN", missing)
    monkeypatch.setattr(core, "_run_script", lambda *args: pytest.fail("bad solver launched"))
    with pytest.raises(RuntimeError, match="Configured VSPAERO_BIN was not found"):
        core.execute_openvsp(request_model)
    manifest = next(Path(request_model.output_dir).glob("*/manifest.json"))
    assert json.loads(manifest.read_text())["status"] == "failed"
