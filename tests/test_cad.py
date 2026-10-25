import hashlib
import json
import math
import os
import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from openvsp_mcp import cad
from openvsp_mcp.cad_models import ExportRequest


def request(**kwargs):
    return ExportRequest(geometry_file="source.vsp3", output_dir="results", **kwargs)


def step(unit=".MILLI."):
    return (
        "ISO-10303-21;\nHEADER;\nFILE_SCHEMA(('CONFIG_CONTROL_DESIGN'));\nENDSEC;\n"
        "DATA;\n#1=(LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT(" + unit + ",.METRE.));\n"
        "#2=B_SPLINE_SURFACE_WITH_KNOTS('surface',3,3,(),.UNSPECIFIED.,.F.,.F.,.F.,(),(),(),(),.UNSPECIFIED.);\n"
        "ENDSEC;\nEND-ISO-10303-21;\n"
    )


def iges(unit_code=2):
    sections = {
        "S": ["OpenVSP"],
        "G": [f",,3Hpod,7Hpod.igs,7HOpenVSP,6Hwriter,32,38,6,308,15,3Hpod,1.0,{unit_code},2HMM;"],
        "D": ["     128       1", "     128       0       0       1"],
        "P": ["128,0,0,0,0;"],
        "T": ["S      1G      1D      2P      1"],
    }
    return (
        "\n".join(
            f"{row:<72}{section}{i:7}"
            for section, rows in sections.items()
            for i, row in enumerate(rows, 1)
        )
        + "\n"
    )


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf, 0, -1, 1e-13, 1e13])
def test_tolerance_rejects_nonfinite_and_out_of_native_range(value):
    with pytest.raises(ValidationError):
        request(format="step", length_unit="m", tolerance=value)


def test_explicit_units_and_format_specific_settings():
    with pytest.raises(ValidationError):
        request(format="step")
    with pytest.raises(ValidationError, match="only for STEP"):
        request(format="iges", length_unit="m", tolerance=1e-6)
    with pytest.raises(ValidationError, match="requires split_and_stitch"):
        request(format="step", length_unit="m", step_representation="brep")
    with pytest.raises(ValidationError):
        request(format="step", length_unit="m", commands=["system('bad')"])
    assert request(format="step", length_unit="m").effective_tolerance == 1e-6
    assert request(format="iges", length_unit="m").effective_tolerance is None
    assert (
        request(
            format="step", length_unit="m", export_mode="split_and_stitch"
        ).effective_representation
        == "brep"
    )


@pytest.mark.parametrize(
    "format,mode,enum,group",
    [
        ("step", "standard", "EXPORT_STEP", "STEPSettings"),
        ("iges", "standard", "EXPORT_IGES", "IGESSettings"),
        ("step", "split_and_stitch", "EXPORT_STEP_STITCH", "ExportIntersect"),
        ("iges", "split_and_stitch", "EXPORT_IGES_STITCH", "ExportIntersect"),
    ],
)
def test_native_export_routes_and_private_output(tmp_path, format, mode, enum, group):
    script = cad._script(
        request(format=format, length_unit="m", export_mode=mode),
        tmp_path / "input.vsp3",
        tmp_path,
        "nonce",
    )
    assert f",selectedSet,{enum});" in script
    assert '"' + group + '"' in script
    assert "LEN_M" in script
    assert "ScaleSet" not in script
    assert "source.vsp3" not in script
    assert str(tmp_path / "input.vsp3") in script


def test_step_structure_and_unit_checks():
    assert cad._check_step(step(), "mm")["length_unit"] == "mm"
    with pytest.raises(ValueError, match="declared length unit"):
        cad._check_step(step(), "m")
    with pytest.raises(ValueError, match="envelope"):
        cad._check_step(step().replace("END-ISO-10303-21;", ""), "mm")
    with pytest.raises(ValueError, match="no exported CAD surfaces"):
        cad._check_step(step().replace("B_SPLINE_SURFACE", "NOT_A_SURFACE"), "mm")


def test_iges_structure_sequence_counts_and_units():
    assert cad._check_iges(iges(), "mm")["surface_entities"] == 1
    with pytest.raises(ValueError, match="declared length unit"):
        cad._check_iges(iges(), "m")
    with pytest.raises(ValueError, match="terminate counts"):
        cad._check_iges(iges().replace("D      2P", "D      4P"), "mm")
    with pytest.raises(ValueError, match="record sequence"):
        cad._check_iges(iges().replace("D      2\n", "D      4\n"), "mm")
    with pytest.raises(ValueError, match="80 columns"):
        cad._check_iges(iges()[:-12], "mm")


def test_hollerith_strings_may_contain_delimiters():
    assert cad._iges_fields(",,5Ha,b;c,1,2;") == ["", "", "a,b;c", "1", "2"]
    with pytest.raises(ValueError, match="Truncated"):
        cad._iges_fields(",,100Hshort;")


def test_success_keeps_topology_explicitly_unassessed(tmp_path):
    path = tmp_path / "geometry.stp"
    path.write_text(step())
    result = cad._validate(
        request(format="step", length_unit="mm"), tmp_path, {"selected_geometry_ids": ["pod"]}
    )
    assert result["artifacts"]["cad"]["path"] == str(path)
    assert len(result["artifacts"]["cad"]["sha256"]) == 64
    assert result["verification"]["file_structure"] == "passed"
    assert result["verification"]["topology"] == "not_assessed"
    assert result["verification"]["watertight"] is None
    assert result["settings"]["coordinate_scaling"] == "unchanged"


def test_corrupt_native_export_cannot_be_success(tmp_path):
    (tmp_path / "geometry.stp").write_text("native process wrote some text")
    with pytest.raises(RuntimeError, match="CAD export verification failed"):
        cad._validate(request(format="step", length_unit="mm"), tmp_path, {})


def test_runner_callbacks_use_request_and_source_snapshot(monkeypatch, tmp_path):
    def run_operation(req, name, build, validate):
        assert name == "export_geometry"
        assert req.length_unit == "mm"
        generated = build(tmp_path / "private.vsp3", tmp_path, "token")
        assert str(tmp_path / "private.vsp3") in generated
        (tmp_path / "geometry.stp").write_text(step())
        return validate(tmp_path, {"selected_geometry_ids": ["pod"]})

    monkeypatch.setattr(cad, "run_operation", run_operation)
    assert (
        cad.export_geometry(request(format="step", length_unit="mm"))["artifacts"]["cad"]["bytes"]
        > 0
    )


@pytest.mark.parametrize(
    "unit,scale", [("mm", 1), ("cm", 10), ("m", 1000), ("in", 25.4), ("ft", 304.8)]
)
@pytest.mark.parametrize("format", ["step", "iges"])
@pytest.mark.parametrize("mode", ["standard", "split_and_stitch"])
def test_native_cad_units_selection_and_independent_reopening(
    monkeypatch, tmp_path, unit, scale, format, mode
):
    """Opt-in native regression; optional FreeCAD Python proves dimensions independently."""
    binary = os.environ.get("OPENVSP_NATIVE_TEST_BIN")
    if not binary:
        pytest.skip("Set OPENVSP_NATIVE_TEST_BIN for native CAD export tests")
    monkeypatch.setattr(cad.core, "OPENVSP_BIN", binary)
    source = tmp_path / "pod.vsp3"
    fixture_script = tmp_path / "fixture.vspscript"
    fixture_script.write_text(
        'int main(){ClearVSPModel();string g=AddGeom("POD");SetSetFlag(g,3,true);Update();'
        f"WriteVSPFile({cad.core._quote(source)},SET_ALL);return 0;}}"
    )
    assert cad.core._run_script(fixture_script, tmp_path / "fixture.log", 60) == 0
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    result = cad.export_geometry(
        ExportRequest(
            geometry_file=str(source),
            output_dir=str(tmp_path),
            geom_set=3,
            format=format,
            length_unit=unit,
            export_mode=mode,
        )
    )
    assert result["status"] == "ok"
    assert result["selected_set"] == 3
    assert len(result["selected_geometry_ids"]) == 1
    assert result["input_sha256"] == source_hash == hashlib.sha256(source.read_bytes()).hexdigest()
    assert result["verification"]["topology"] == "not_assessed"
    freecad_python = os.environ.get("OPENVSP_CAD_TEST_PYTHON")
    if freecad_python:
        report = tmp_path / "reopen.json"
        command = [
            freecad_python,
            str(Path(__file__).parents[1] / "scripts/validate_cad.py"),
            result["artifacts"]["cad"]["path"],
            "--output",
            str(report),
            "--expected-bbox-mm",
            str(10 * scale),
            str(4 / 3 * scale),
            str(4 / 3 * scale),
        ]
        if os.environ.get("OPENVSP_CAD_TEST_LIB"):
            command += ["--freecad-lib", os.environ["OPENVSP_CAD_TEST_LIB"]]
        subprocess.run(command, check=True, capture_output=True, text=True, timeout=60)
        reopened = json.loads(report.read_text())[0]
        assert reopened["valid"] and reopened["dimensions_match"]
        if format == "step" and mode == "split_and_stitch":
            assert reopened["solids"] == reopened["closed_shells"] == 1
        else:
            assert reopened["faces"] == 8
