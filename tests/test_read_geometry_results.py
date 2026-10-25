"""Saved geometry manifests remain usable through the existing bounded reader."""

import json
from pathlib import Path

import pytest

from openvsp_mcp.models import ResultRequest
from openvsp_mcp.results import read_results


def save(tmp_path, manifest):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    return ResultRequest(manifest_file=str(path))


def measurement():
    return {
        "operation": "geometry_analysis",
        "status": "ok",
        "analysis": "projection",
        "length_unit": "m",
        "units": {"area": "m^2", "volume": "m^3"},
        "unit_conversion_applied": False,
        "summary": {"projected_area": 2.5},
        "components": [{"geom_id": "one", "name": "Wing", "projected_area": 2.5}],
        "selection": {"geom_set": 3, "geom_ids": ["one"], "mode_id": None},
        "projection_direction": [0, 0, 1],
        "diagnostics": {},
        "raw_results_json": "/not/opened/results.json",
        "artifacts": {"raw_results.csv": "/not/opened/results.csv"},
    }


def test_geometry_summary_selection_and_artifact_paths_are_returned_without_extra_reads(
    tmp_path, monkeypatch
):
    manifest = measurement()
    request = save(tmp_path, manifest)
    opened = []
    original = Path.open

    def open_only_manifest(path, *args, **kwargs):
        opened.append(path)
        assert path == Path(request.manifest_file)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", open_only_manifest)
    result = read_results(request)
    for key, value in manifest.items():
        assert result[key] == value
    assert result["coefficients"] == result["unavailable_coefficients"] == {}
    assert opened == [Path(request.manifest_file)]
    assert "log_tail" not in result


def test_cad_metadata_and_failed_native_details_are_readable(tmp_path):
    cad = {
        "operation": "export_geometry",
        "status": "ok",
        "selected_set": 0,
        "selected_geometry_ids": ["one"],
        "settings": {"length_unit": "mm"},
        "verification": {"file_structure": "passed", "watertight": None},
        "artifacts": {"cad": {"path": "/not/opened/geometry.stp", "bytes": 100}},
    }
    result = read_results(save(tmp_path, cad))
    for key, value in cad.items():
        assert result[key] == value
    failure = {
        "operation": "geometry_analysis",
        "status": "error",
        "error": "Selected set has no geometry",
        "native_errors": [
            {"code": None, "phase": "selection", "message": "Selected set has no geometry"}
        ],
        "log_path": str(tmp_path / "openvsp.log"),
        "manifest_path": str(tmp_path / "manifest.json"),
    }
    (tmp_path / "openvsp.log").write_text("error details\n")
    request = save(tmp_path, failure).model_copy(update={"log": "openvsp"})
    result = read_results(request)
    assert result["native_errors"] == failure["native_errors"]
    assert result["status"] == "error" and result["error"] == failure["error"]
    assert result["log_tail"] == "error details"


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf"), "NaN", True])
def test_rejects_nonfinite_or_wrongly_typed_measurements(tmp_path, value):
    manifest = measurement()
    manifest["components"][0]["projected_area"] = value
    with pytest.raises(RuntimeError, match="saved geometry"):
        read_results(save(tmp_path, manifest))


@pytest.mark.parametrize("field", ["summary", "verification", "native_errors"])
def test_rejects_nested_nonfinite_geometry_metadata(tmp_path, field):
    manifest = measurement()
    manifest[field] = {"nested": [float("nan")]}
    with pytest.raises(RuntimeError, match="Non-finite"):
        read_results(save(tmp_path, manifest))


def test_rejects_overflow_float_literal_in_saved_geometry(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text('{"operation":"geometry_analysis","status":"ok","summary":{"area":1e999}}')
    with pytest.raises(RuntimeError, match="Non-finite"):
        read_results(ResultRequest(manifest_file=str(path)))


@pytest.mark.parametrize("status", ["ok", "error"])
def test_new_statuses_do_not_expand_existing_operation_contract(tmp_path, status):
    with pytest.raises(RuntimeError, match="Not an OpenVSP"):
        read_results(save(tmp_path, {"operation": "run_vspaero", "status": status}))


def test_geometry_does_not_acquire_aerodynamic_coefficients(tmp_path):
    request = save(tmp_path, measurement()).model_copy(update={"coefficient_names": ["CLtot"]})
    with pytest.raises(RuntimeError, match="Unknown coefficient"):
        read_results(request)
