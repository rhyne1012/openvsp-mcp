import json
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from openvsp_mcp import core, health, workflows
from openvsp_mcp.fastapi_app import create_app
from openvsp_mcp.models import CreateModelRequest, OpenVSPRequest, SweepRequest, VSPAeroSettings
from openvsp_mcp.quality import history_diagnostics


def test_missing_executables_are_unhealthy_including_http(monkeypatch, tmp_path):
    monkeypatch.setattr(core, "OPENVSP_BIN", str(tmp_path / "missing-vsp"))
    monkeypatch.setattr(core, "VSPAERO_BIN", str(tmp_path / "missing-aero"))
    result = TestClient(create_app()).get("/health")
    assert result.status_code == 503
    data = result.json()
    assert data["status"] == "error"
    assert data["package_version"] == "0.5.0"
    assert len(data["package_sha256"]) == 64
    assert all(v["status"] == "error" for v in data["checks"].values())


@pytest.mark.parametrize(
    "rc,out,err,expected",
    [
        (0, "VSPAERO 7.2.2\n", "", "ok"),
        (1, "VSPAERO 7.2.2\n", "", "ok"),
        (2, "VSPAERO 7.2.2\n", "", "error"),
        (1, "VSPAERO 7.2.2\nError", "", "error"),
        (1, "VSPAERO 7.2.2\n", "failed", "error"),
    ],
)
def test_version_only_exit_convention_is_narrow(monkeypatch, rc, out, err, expected):
    monkeypatch.setattr(health, "_executable", lambda value: value)

    def api(script, log, timeout):
        log.write_text("OpenVSP 3.51.3\nOPENVSP_HEALTH_API_OK\n")
        return 0

    monkeypatch.setattr(core, "_run_script", api)
    monkeypatch.setattr(
        health.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess([], rc, out, err)
    )
    result = health.health_check()
    assert result["status"] == expected
    assert result["tested_version_pair"] == (expected == "ok")


def test_api_timeout_is_unhealthy(monkeypatch):
    monkeypatch.setattr(health, "_executable", lambda value: value)

    def timeout(*args):
        raise RuntimeError("OpenVSP timed out after 15s")

    monkeypatch.setattr(core, "_run_script", timeout)
    monkeypatch.setattr(
        health.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess([], 1, "VSPAERO 7.2.2\n", ""),
    )
    assert health.health_check()["checks"]["openvsp"]["status"] == "error"


@pytest.mark.parametrize("thick,thin", [(-1, -1), (3, 3), (0, 0)])
def test_same_set_rejected(thick, thin):
    with pytest.raises(ValidationError, match="sets must differ"):
        VSPAeroSettings(thick_geom_set=thick, thin_geom_set=thin)


def test_custom_model_requires_commands():
    with pytest.raises(ValidationError):
        CreateModelRequest(output_dir="unused", template="custom")


def test_quality_reports_changes_not_convergence(tmp_path):
    p = tmp_path / "case.history"
    p.write_text(
        "Iter Mach AoA Beta CLtot CDtot CMytot\n1 .1 3 0 .2 .01 .04\n2 .1 3 0 .21 .011 .041\n"
    )
    result = history_diagnostics(p)
    assert result["convergence_status"] == "not_assessed"
    assert result["mesh_study"] == "not_performed"
    assert result["last_step_absolute_change"]["CLtot"] == pytest.approx(0.01)
    p.write_text("unrecognized version\n")
    assert history_diagnostics(p)["history_status"] == "unavailable_or_insufficient"


def test_sweep_preserves_partial_results_and_snapshot(monkeypatch, tmp_path):
    source = tmp_path / "source.vsp3"
    source.write_text("snapshot")
    calls = []

    def solve(request):
        calls.append(request)
        assert Path(request.geometry_file).read_text() == "snapshot"
        if len(calls) == 2:
            raise RuntimeError("solver failed")
        source.write_text("external change")

        class Result:
            def model_dump(self):
                return {"coefficients": {"CLtot": 0.2}}

        return Result()

    monkeypatch.setattr(workflows, "execute_openvsp", solve)
    request = SweepRequest(
        geometry_file=str(source),
        output_dir=str(tmp_path),
        conditions=[VSPAeroSettings(alpha=0), VSPAeroSettings(alpha=3)],
    )
    with pytest.raises(RuntimeError, match="Partial results"):
        workflows.run_sweep(request)
    manifest = json.loads(next(tmp_path.glob("*-sweep-*/sweep.json")).read_text())
    assert manifest["status"] == "failed" and len(manifest["results"]) == 1
    assert calls[0].analysis.alpha == 0 and calls[1].analysis.alpha == 3
    assert source.read_text() == "external change"


def test_sweep_total_budget_stops_before_next_condition(monkeypatch, tmp_path):
    source = tmp_path / "source.vsp3"
    source.write_text("snapshot")
    times = iter([0, 601])
    monkeypatch.setattr(workflows.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(workflows, "execute_openvsp", lambda r: pytest.fail("solver launched"))
    with pytest.raises(RuntimeError, match="total time budget"):
        workflows.run_sweep(
            SweepRequest(
                geometry_file=str(source), output_dir=str(tmp_path), conditions=[VSPAeroSettings()]
            )
        )


def test_preview_and_preflight_force_read_only_operation(monkeypatch):
    seen = []
    monkeypatch.setattr(workflows, "execute_openvsp", lambda request, **kw: seen.append(kw))
    request = OpenVSPRequest(geometry_file="model.vsp3", run_vspaero=True)
    workflows.preview_model(request)
    workflows.preflight_model(request)
    assert seen == [{"operation": "preview"}, {"operation": "preflight"}]
