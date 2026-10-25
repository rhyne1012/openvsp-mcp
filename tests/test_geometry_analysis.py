"""Validation failures and optional native analytic/scaling checks for geometry measures."""

import csv
import hashlib
import json
import math
import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from openvsp_mcp import core
from openvsp_mcp.analysis_models import GeometryAnalysisRequest
from openvsp_mcp.geometry_analysis import _validate, geometry_analysis
from openvsp_mcp.operations import NativeOperationError


def request(**changes):
    return GeometryAnalysisRequest(
        **{"geometry_file": "source.vsp3", "analysis": "comp_geom", "length_unit": "m", **changes}
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"analysis": "CFDMesh"},
        {"analysis": "projection"},
        {"projection_direction": "x"},
        {"analysis": "projection", "projection_direction": [0, 0, 0]},
        {"analysis": "projection", "projection_direction": [float("nan"), 1, 0]},
        {"analysis": "projection", "projection_direction": [float("inf"), 1, 0]},
        {"analysis": "projection", "projection_direction": [1, 2]},
        {"geom_set": 3, "mode_id": "mode"},
        {"length_unit": "unspecified"},
        {"execute_analysis": "MassProp"},
    ],
)
def test_invalid_geometry_analysis_requests(changes):
    with pytest.raises(ValidationError):
        request(**changes)


def test_geometry_analysis_requires_declared_units():
    with pytest.raises(ValidationError):
        GeometryAnalysisRequest(geometry_file="source.vsp3", analysis="comp_geom")


@pytest.mark.parametrize("vector", [[3, 4, 0], [3e300, 4e300, 0], [3e-300, 4e-300, 0]])
def test_projection_vectors_normalize_without_overflow(vector):
    assert request(
        analysis="projection", projection_direction=vector
    ).direction_vector == pytest.approx([0.6, 0.8, 0.0])


def field(kind, values):
    return {
        "type": kind,
        "type_code": {"int": 0, "double": 1, "string": 2, "vec3d": 3}[kind],
        "blocks": [values],
    }


def projection_result():
    return {
        "result_id": "result",
        "result_name": "Projection",
        "data": {
            "Area": field("double", [3.0]),
            "Comp_Areas": field("double", [2.0, 2.0]),
            "Comp_IDs": field("string", ["a", "b"]),
            "Comp_Names": field("string", ["same", "same"]),
            "Direction": field("vec3d", [[0.0, 0.0, 1.0]]),
            "Path": field("vec3d", [[0.0, 0.0, 0.0], [1.0, 1.0, 0.0]]),
        },
    }


def validate_projection(tmp_path, raw):
    write_results(tmp_path, raw)
    return _validate(
        request(analysis="projection", projection_direction="z"),
        tmp_path,
        {"result_id": "result", "result_name": "Projection", "selection": {"geom_ids": ["a", "b"]}},
    )


def write_results(tmp_path, raw):
    (tmp_path / "raw_results.json").write_text(json.dumps(raw))
    with (tmp_path / "raw_results.csv").open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["Results_Name", raw["result_name"]])
        for name, item in raw["data"].items():
            if item["type"] in ("int", "double"):
                for block in item["blocks"]:
                    writer.writerow([name, *block])


def test_projection_union_is_not_component_sum(tmp_path):
    result = validate_projection(tmp_path, projection_result())
    assert result["summary"]["projected_area"] == 3
    assert sum(component["projected_area"] for component in result["components"]) == 4
    assert [component["geom_id"] for component in result["components"]] == ["a", "b"]
    assert result["units"]["area"] == "m^2"
    assert not result["unit_conversion_applied"]
    assert set(result["artifacts"]) == {"raw_results.csv", "raw_results.json"}


@pytest.mark.parametrize(
    "name,replacement",
    [
        ("Area", field("int", [3])),
        ("Area", field("double", [])),
        ("Area", field("double", [-1.0])),
        ("Comp_Areas", field("double", [2.0])),
        ("Comp_IDs", field("string", ["a", "outside-selection"])),
        ("Direction", field("vec3d", [[1.0, 0.0, 0.0]])),
        ("Path", field("vec3d", [[1.0, float("nan"), 0.0]])),
        ("Path", field("vec3d", [[1.0, float("inf"), 0.0]])),
        ("Area", field("double", [True])),
    ],
)
def test_rejects_malformed_or_mismatched_results(tmp_path, name, replacement):
    raw = projection_result()
    raw["data"][name] = replacement
    with pytest.raises(RuntimeError):
        validate_projection(tmp_path, raw)


def test_rejects_wrong_result_identity_and_missing_artifact(tmp_path):
    raw = projection_result()
    raw["result_name"] = "Comp_Geom"
    with pytest.raises(RuntimeError, match="identity"):
        validate_projection(tmp_path, raw)
    (tmp_path / "raw_results.json").unlink()
    with pytest.raises(RuntimeError, match="Invalid native"):
        _validate(request(analysis="projection", projection_direction="z"), tmp_path, {})


@pytest.mark.parametrize(
    "contents", ["Results_Name,Projection\n", "Results_Name,Projection\nArea,9\n"]
)
def test_rejects_truncated_or_conflicting_csv(tmp_path, contents):
    write_results(tmp_path, projection_result())
    (tmp_path / "raw_results.csv").write_text(contents)
    with pytest.raises(RuntimeError, match="CSV"):
        _validate(
            request(analysis="projection", projection_direction="z"),
            tmp_path,
            {"result_id": "result", "result_name": "Projection"},
        )


def test_comp_geom_reports_discarded_meshes_and_keeps_signed_volumes(tmp_path):
    values = {
        "Comp_Name": field("string", ["outer", "void"]),
        "Wet_Area": field("double", [10.0, 2.0]),
        "Wet_Vol": field("double", [8.0, -1.0]),
        "Theo_Area": field("double", [10.0, 2.0]),
        "Theo_Vol": field("double", [8.0, -1.0]),
        "Total_Wet_Area": field("double", [12.0]),
        "Total_Wet_Vol": field("double", [7.0]),
        "Total_Theo_Area": field("double", [12.0]),
        "Total_Theo_Vol": field("double", [7.0]),
        "Num_Open_Meshes_Removed": field("int", [1]),
        "Num_Open_Meshes_Merged": field("int", [2]),
        "Num_Degen_Tris_Removed": field("int", [0]),
        "Total_Num_Meshes": field("int", [2]),
        "Total_Num_Tris": field("int", [100]),
    }
    write_results(tmp_path, {"result_id": "result", "result_name": "Comp_Geom", "data": values})
    result = _validate(request(), tmp_path, {"result_id": "result", "result_name": "Comp_Geom"})
    assert result["components"][1]["wetted_volume"] == -1
    assert result["summary"]["wetted_volume"] == 7
    assert result["diagnostics"]["open_meshes_removed"] == 1
    assert any("exclude" in warning for warning in result["warnings"])
    assert any("merged" in warning for warning in result["warnings"])


@pytest.fixture
def native_models(tmp_path, monkeypatch):
    binary = os.environ.get("OPENVSP_NATIVE_TEST_BIN")
    if not binary:
        pytest.skip("Set OPENVSP_NATIVE_TEST_BIN for native analytic/scaling tests")
    monkeypatch.setattr(core, "OPENVSP_BIN", binary)
    script, log = tmp_path / "create.vspscript", tmp_path / "create.log"
    model, scaled, overlap = (
        tmp_path / name for name in ("sphere.vsp3", "scaled.vsp3", "overlap.vsp3")
    )
    script.write_text(
        "\n".join(
            [
                'int main() { ClearVSPModel(); string id=AddGeom("ELLIPSOID");',
                'SetParmVal(id,"Tess_U","Shape",61); SetParmVal(id,"Tess_W","Shape",61);',
                'SetSetFlag(id,3,true); string mode=CreateAndAddMode("sphere only",3,SET_NONE);',
                'Update(); Print("MODE:"+mode); Print("GEOM:"+id);',
                f"WriteVSPFile({core._quote(model)},SET_ALL);",
                'SetParmVal(id,"A_Radius","Design",2); SetParmVal(id,"B_Radius","Design",2);',
                'SetParmVal(id,"C_Radius","Design",2); Update();',
                f"WriteVSPFile({core._quote(scaled)},SET_ALL);",
                f"ClearVSPModel(); ReadVSPFile({core._quote(model)});",
                'string second=AddGeom("ELLIPSOID"); SetParmVal(second,"Y_Location","XForm",0.5);',
                'SetParmVal(second,"Tess_U","Shape",61); SetParmVal(second,"Tess_W","Shape",61); Update();',
                f"WriteVSPFile({core._quote(overlap)},SET_ALL);",
                "if(GetNumTotalErrors()!=0) { while(GetNumTotalErrors()>0) {",
                "ErrorObj e=PopLastError(); Print(e.GetErrorString()); } return 1; }",
                'Print("FIXTURE_OK"); return 0; }',
            ]
        )
    )
    assert core._run_script(script, log, 60) == 0
    text = log.read_text()
    assert "FIXTURE_OK" in text, text
    mode = next(
        line.strip().split(":", 1)[1]
        for line in text.splitlines()
        if line.strip().startswith("MODE:")
    )
    geom = next(
        line.strip().split(":", 1)[1]
        for line in text.splitlines()
        if line.strip().startswith("GEOM:")
    )
    return model, scaled, overlap, mode, geom


def test_native_sphere_analytic_values_scaling_and_source_preservation(native_models, tmp_path):
    model, scaled, _, _, _ = native_models
    hashes = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in (model, scaled)}
    outputs = []
    for path in (model, scaled):
        options = {"geometry_file": str(path), "output_dir": str(tmp_path / "runs")}
        comp = geometry_analysis(request(**options))
        projection = geometry_analysis(
            request(**options, analysis="projection", projection_direction=[5, 0, 0])
        )
        assert comp["status"] == projection["status"] == "ok"
        assert comp["diagnostics"]["open_meshes_removed"] == 0
        for result in (comp, projection):
            assert Path(result["raw_results_json"]).is_file()
            assert Path(result["raw_results_csv"]).is_file()
            assert result["input_sha256"] == hashes[path]
            assert json.loads(Path(result["manifest_path"]).read_text())["status"] == "ok"
        outputs.append((comp["summary"], projection["summary"]))
    small, large = outputs
    assert small[0]["wetted_area"] == pytest.approx(4 * math.pi, rel=0.005)
    assert small[0]["wetted_volume"] == pytest.approx(4 * math.pi / 3, rel=0.005)
    assert small[1]["projected_area"] == pytest.approx(math.pi, rel=0.005)
    assert large[0]["wetted_area"] / small[0]["wetted_area"] == pytest.approx(4, rel=1e-9)
    assert large[0]["wetted_volume"] / small[0]["wetted_volume"] == pytest.approx(8, rel=1e-9)
    assert large[1]["projected_area"] / small[1]["projected_area"] == pytest.approx(4, rel=1e-9)
    assert all(
        hashlib.sha256(path.read_bytes()).hexdigest() == digest for path, digest in hashes.items()
    )


def test_native_selection_mode_union_and_unit_declaration(native_models, tmp_path):
    _, _, overlap, mode, geom = native_models
    options = {
        "geometry_file": str(overlap),
        "output_dir": str(tmp_path / "runs"),
        "analysis": "projection",
        "projection_direction": "x",
    }
    selected = geometry_analysis(request(**options, geom_set=3))
    by_mode = geometry_analysis(request(**options, mode_id=mode, length_unit="ft"))
    all_geoms = geometry_analysis(request(**options))
    assert selected["selection"]["geom_ids"] == by_mode["selection"]["geom_ids"] == [geom]
    assert by_mode["selection"]["mode_id"] == mode
    assert by_mode["units"]["area"] == "ft^2"
    assert selected["summary"] == by_mode["summary"]  # Declaration does not rescale.
    union = all_geoms["summary"]["projected_area"]
    assert selected["summary"]["projected_area"] < union
    assert union < sum(component["projected_area"] for component in all_geoms["components"])
    with pytest.raises(NativeOperationError, match="no geometry"):
        geometry_analysis(request(**options, geom_set=4))
