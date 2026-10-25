"""Model-discovery contracts and opt-in native OpenVSP 3.55 coverage."""

import hashlib
import json
import os
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from openvsp_mcp import core, query
from openvsp_mcp.models import QueryRequest


@pytest.mark.parametrize("kind", ["geometry", "containers", "sets", "modes", "design_variables"])
def test_model_discovery_requires_source(kind):
    with pytest.raises(ValidationError, match="require geometry_file"):
        QueryRequest(kind=kind)


@pytest.mark.parametrize(
    "fields",
    [
        {"kind": "sets", "geom_id": "g"},
        {"kind": "geometry", "container_id": "c"},
        {"kind": "modes", "parm_ids": ["p"]},
        {"kind": "parameters", "geom_id": "g", "container_id": "c"},
        {"kind": "parameters", "container_name": "Vehicle", "container_id": "c"},
        {"kind": "sets", "container_name": "Vehicle"},
        {"kind": "parameters", "container_index": 1},
        {"kind": "parameters", "design_variables_file": "vars.xddm"},
        {"kind": "design_variables", "design_variables_file": "vars.vsp3"},
        {"kind": "parameters", "parm_ids": [""]},
    ],
)
def test_discovery_rejects_ambiguous_or_mismatched_selectors(fields):
    with pytest.raises(ValidationError):
        QueryRequest(geometry_file="model.vsp3", **fields)


def test_clone_binding_is_omitted_from_older_version_script():
    request = QueryRequest(kind="geometry", geometry_file="model.vsp3")
    for version in (None, "OpenVSP 3.53.1", "OpenVSP 3.54.0"):
        script = query._script(request, Path("model.vsp3"), "test", native_version=version)
        assert "GetGeomCloneOriginal(" not in script
        assert 'resolution="unavailable"' in script
    script = query._script(request, Path("model.vsp3"), "test", native_version="OpenVSP 3.55.0")
    assert "GetGeomCloneOriginal(" in script


def test_native_query_error_exposes_original_code_message_phase(monkeypatch, tmp_path):
    source = tmp_path / "model.vsp3"
    source.write_text("<Vsp_Geometry><Vehicle/></Vsp_Geometry>")
    before = source.read_bytes()

    def native(script, log, timeout):
        token = re.search(r'Print\("MCP_QUERY_(\w+):', script.read_text()).group(1)
        error = {"code": 5, "message": "native load failed", "phase": "load_model"}
        log.write_text("MCP_NATIVE_ERRORS_" + token + ":" + json.dumps([error]))
        return 5

    monkeypatch.setattr(core, "_run_script", native)
    with pytest.raises(query.NativeQueryError) as raised:
        query.query_model(QueryRequest(kind="sets", geometry_file=str(source)))
    assert raised.value.native_errors == [
        {"code": 5, "message": "native load failed", "phase": "load_model"}
    ]
    assert raised.value.phase == "load_model"
    assert raised.value.exit_code == 5
    assert "native load failed" in str(raised.value)
    assert source.read_bytes() == before


@pytest.mark.parametrize("number", ["NaN", "Infinity", "-Infinity", "1e999", "-1e999"])
def test_nonfinite_numbers_fail_even_in_nested_native_result(monkeypatch, number):
    def native(script, log, timeout):
        token = re.search(r'Print\("MCP_QUERY_(\w+):', script.read_text()).group(1)
        log.write_text(
            "MCP_QUERY_"
            + token
            + ': {"openvsp_version":"OpenVSP 3.55.0","inputs":[{"value":'
            + number
            + "}]}"
        )
        return 0

    monkeypatch.setattr(core, "_run_script", native)
    with pytest.raises(query.NativeQueryError, match="Non-finite query value") as raised:
        query.query_model(QueryRequest(kind="analysis"))
    assert raised.value.phase == "decode"


def test_design_bounds_are_separate_from_native_parm_limits(tmp_path):
    path = tmp_path / "vars.xddm"
    path.write_text('<Model><Variable VSPID="parm" Min="2" Max="20"/></Model>')
    result = {"design_variables": [{"parm_id": "parm", "parameter": {"lower": 0, "upper": 100}}]}
    query._design_variable_bounds(path, result)
    item = result["design_variables"][0]
    assert item["design_bounds"] == {"lower": 2, "upper": 20}
    assert item["parameter"] == {"lower": 0, "upper": 100}
    path.write_text('<Model><Variable VSPID="parm" Min="nan" Max="20"/></Model>')
    with pytest.raises(query.NativeQueryError, match="Non-finite"):
        query._design_variable_bounds(path, result)


@pytest.fixture
def native_model(monkeypatch, tmp_path):
    binary = os.environ.get("OPENVSP_NATIVE_TEST_BIN")
    if not binary:
        pytest.skip("Set OPENVSP_NATIVE_TEST_BIN to run native OpenVSP 3.55 model discovery")
    monkeypatch.setattr(core, "OPENVSP_BIN", binary)
    monkeypatch.setattr(query, "_cache", {})
    source, design, des = (tmp_path / name for name in ("model.vsp3", "vars.xddm", "vars.des"))
    script, log = tmp_path / "fixture.vspscript", tmp_path / "fixture.log"
    script.write_text(
        r"""
int main() {
 ClearVSPModel();
 string pod=AddGeom("POD"); SetGeomName(pod,"Body");
 string wing=AddGeom("WING",pod); SetGeomName(wing,"Wing");
 SetParmVal(wing,"Flip_Flag","Sym",3);
 string clone=AddGeom("CLONE"); SetGeomCloneOriginal(clone,wing);
 string nested=AddGeom("CLONE"); SetGeomCloneOriginal(nested,clone);
 string empty=AddGeom("CLONE"); SetGeomCloneOriginal(empty,"");
 SetParmVal(empty,"AutoName","Behavior",0); SetGeomName(empty,"EmptyClone");
 SetSetName(SET_FIRST_USER,"Lifting"); SetSetFlag(wing,SET_FIRST_USER,true);
 string mode=CreateAndAddMode("Cruise",SET_FIRST_USER,SET_NONE);
 string group=AddVarPresetGroup("Length"); string setting=AddVarPresetSetting(group,"Default");
 string parm=FindParm(pod,"Length","Design");
 AddVarPresetParm(group,parm); SaveVarPresetParmVals(group,setting); ModeAddGroupSetting(mode,group,setting);
 Update();
 WriteVSPFile(@SOURCE@,SET_ALL);
 SetParmVal(parm,12);
 AddDesignVarLimits(parm,XDDM_VAR,2.0,20.0);
 WriteXDDMFile(@DESIGN@); WriteDESFile(@DES@);
 if(GetNumTotalErrors()>0) { Print(PopLastError().GetErrorString()); return 1; }
 return 0;
}
""".replace("@SOURCE@", core._quote(source))
        .replace("@DESIGN@", core._quote(design))
        .replace("@DES@", core._quote(des))
    )
    assert core._run_script(script, log, 30) == 0, log.read_text()
    assert source.exists() and design.exists()
    before = {path: path.read_bytes() for path in (source, design, des)}
    yield source, design, des
    assert all(path.read_bytes() == data for path, data in before.items())


def test_native_clone_hierarchy_flip_and_empty_clone(native_model):
    source, _, _ = native_model
    result = query.query_model(QueryRequest(kind="geometry", geometry_file=str(source)))
    by_name = {g["name"]: g for g in result["geometries"]}
    wing, body = by_name["Wing"], by_name["Body"]
    assert wing["parent_id"] == body["id"] and wing["id"] in body["children_ids"]
    assert wing["flip"] and wing["flip_mask"] == 3
    clone = next(g for g in result["geometries"] if len(g["clone_chain"]) == 2)
    assert clone["clone_chain"][-1] == wing["id"]
    assert clone["effective_type"] == "Wing" and clone["clone_resolution"] == "resolved"
    assert clone["surfaces"][0]["surface_type_code"] == wing["surfaces"][0]["surface_type_code"]
    assert {p["name"] for p in clone["behavior_parameters"]} >= {
        "CloneSym",
        "CloneSets",
        "Flip_Flag",
    }
    empty = by_name["EmptyClone"]
    assert empty["clone_resolution"] == "empty" and empty["effective_type"] is None
    page = query.query_model(
        QueryRequest(kind="geometry", geometry_file=str(source), offset=1, limit=1)
    )
    assert page["total"] == 5 and len(page["geometries"]) == 1
    assert page["geometries"][0]["id"] == result["geometries"][1]["id"]
    selected = query.query_model(
        QueryRequest(kind="geometry", geometry_file=str(source), geom_id=wing["id"])
    )
    assert selected["total"] == 1 and selected["geometries"][0]["id"] == wing["id"]


def test_native_container_parameters_sets_and_modes(native_model):
    source, _, _ = native_model
    containers = query.query_model(QueryRequest(kind="containers", geometry_file=str(source)))
    vehicle = next(c for c in containers["containers"] if c["name"] == "Vehicle")
    user = next(c for c in containers["containers"] if c["name"] == "UserParms")
    assert vehicle["id_persisted_in_source"] and not user["id_persisted_in_source"]
    params = query.query_model(
        QueryRequest(
            kind="parameters", geometry_file=str(source), container_id=vehicle["id"], limit=200
        )
    )
    assert any(p["group"] == "STEPSettings" for p in params["parameters"])
    assert params["total"] == vehicle["parameter_count"]
    user_params = query.query_model(
        QueryRequest(
            kind="parameters",
            geometry_file=str(source),
            container_name="UserParms",
            container_index=0,
        )
    )
    assert user_params["total"] == 16 and user_params["parameters"][0]["name"] == "User_0"
    modes = query.query_model(QueryRequest(kind="modes", geometry_file=str(source)))
    mode = modes["modes"][0]
    assert mode["name"] == "Cruise" and mode["normal_set"] == 3 and mode["degen_set"] == -1
    assert len(mode["group_ids"]) == len(mode["setting_ids"]) == 1
    mode_container = next(c for c in containers["containers"] if c["id"] == mode["id"])
    assert mode_container["parameter_count"] == 2
    mode_params = query.query_model(
        QueryRequest(kind="parameters", geometry_file=str(source), container_id=mode["id"])
    )
    assert {p["name"] for p in mode_params["parameters"]} == {"NormalSet", "DegenSet"}
    sets = query.query_model(
        QueryRequest(kind="sets", geometry_file=str(source), offset=3, limit=1)
    )
    assert sets["sets"][0]["name"] == "Lifting" and not sets["sets"][0]["builtin"]
    assert len(sets["sets"][0]["geom_ids"]) == 3  # Wing and both Clones inherit set membership.
    for kind, key in [
        ("geometry", "geometries"),
        ("containers", "containers"),
        ("parameters", "parameters"),
        ("sets", "sets"),
        ("modes", "modes"),
        ("design_variables", "design_variables"),
    ]:
        empty = query.query_model(
            QueryRequest(kind=kind, geometry_file=str(source), offset=10**30, limit=1)
        )
        assert empty[key] == [] and empty["offset"] == 10**30


def test_native_design_variable_import_and_bounds(native_model):
    source, design, des = native_model
    absent = query.query_model(QueryRequest(kind="design_variables", geometry_file=str(source)))
    assert absent["design_variables"] == [] and "not stored in .vsp3" in absent["membership_source"]
    result = query.query_model(
        QueryRequest(
            kind="design_variables", geometry_file=str(source), design_variables_file=str(design)
        )
    )
    variable = result["design_variables"][0]
    assert variable["design_bounds"] == {"lower": 2, "upper": 20}
    assert variable["parameter"]["lower"] < 2 and variable["parameter"]["upper"] > 20
    assert variable["parameter"]["value"] == 12
    original = query.query_model(
        QueryRequest(kind="parameters", geometry_file=str(source), parm_ids=[variable["parm_id"]])
    )
    assert original["parameters"][0]["value"] == 10
    assert result["input_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert (
        result["design_variables_input_sha256"] == hashlib.sha256(design.read_bytes()).hexdigest()
    )
    result_des = query.query_model(
        QueryRequest(
            kind="design_variables", geometry_file=str(source), design_variables_file=str(des)
        )
    )
    assert result_des["design_variables"][0]["design_bounds_source"] == "DES limits"


def test_native_unknown_selectors_do_not_succeed_with_empty_page(native_model):
    source, _, _ = native_model
    for fields in [
        {"kind": "geometry", "geom_id": "unknown"},
        {"kind": "parameters", "parm_ids": ["unknown"]},
        {"kind": "parameters", "container_id": "unknown"},
        {"kind": "containers", "container_name": "unknown"},
    ]:
        with pytest.raises(query.NativeQueryError) as raised:
            query.query_model(QueryRequest(geometry_file=str(source), offset=100000, **fields))
        assert raised.value.native_errors and raised.value.phase.startswith("select_")


def test_existing_parameter_selector_precedence_and_request_round_trip():
    request = QueryRequest(
        kind="parameters", geometry_file="model.vsp3", geom_id="g", parm_ids=["p"]
    )
    assert QueryRequest.model_validate(request.model_dump()) == request
    script = query._script(request, Path("model.vsp3"), "test")
    assert 'array<string> ids={"p"};' in script and 'geoms={"g"}' not in script


@pytest.mark.parametrize(
    "suffix,contents",
    [
        (".des", "not a DES file"),
        (".des", "1\n"),
        (".des", "1\np:Name:Group:Parm: nan\n"),
        (".des", "1\np: 1\n"),
        (".des", "2\np:Name:Group:Parm: 1\np:Name:Group:Parm: 2\n"),
        (".des", "1\np:Name:Group:Parm: 1\n#LIMITS\np:Name:Group:Parm: 2 1\n"),
        (".des", "1\np:Name:Group:Parm: 1\n#LIMITS\np:Name:Group:Parm: 0 inf\n"),
        (".des", "1\np:Name:Group:Parm: 1\n#LIMITS\nother:Name:Group:Parm: 0 2\n"),
        (".xddm", "not XML"),
        (".xddm", "<Model><Variable>"),
        (".xddm", "<NotModel/>"),
        (".xddm", '<Model><Variable Value="1"/></Model>'),
        (".xddm", '<Model><Variable VSPID="p" Value="1"/><Constant VSPID="p" Value="1"/></Model>'),
        (".xddm", '<Model><Variable VSPID="p" Value="nan"/></Model>'),
        (".xddm", '<Model><Variable VSPID="p" Value="1" Min="inf"/></Model>'),
    ],
)
def test_invalid_design_files_fail_before_native_import(monkeypatch, tmp_path, suffix, contents):
    source, design = tmp_path / "model.vsp3", tmp_path / ("invalid" + suffix)
    source.write_text("<Vsp_Geometry><Vehicle/></Vsp_Geometry>")
    design.write_text(contents)

    def forbidden(*args):
        pytest.fail("Invalid design-variable input reached native OpenVSP")

    monkeypatch.setattr(core, "_run_script", forbidden)
    with pytest.raises(query.NativeQueryError) as raised:
        query.query_model(
            QueryRequest(
                kind="design_variables",
                geometry_file=str(source),
                design_variables_file=str(design),
            )
        )
    assert raised.value.phase == "validate_design_variables"
    assert design.read_text() == contents


def test_native_legacy_des_bounds_are_distinguished_from_supplied_limits(native_model):
    source, _, des = native_model
    modern = query.query_model(
        QueryRequest(
            kind="design_variables", geometry_file=str(source), design_variables_file=str(des)
        )
    )
    assert modern["design_variables"][0]["design_bounds"] == {"lower": 2, "upper": 20}
    assert modern["design_variables"][0]["design_bounds_source"] == "DES limits"
    legacy = des.with_name("legacy.des")
    legacy.write_text(des.read_text().partition("#")[0])
    legacy_result = query.query_model(
        QueryRequest(
            kind="design_variables", geometry_file=str(source), design_variables_file=str(legacy)
        )
    )
    item = legacy_result["design_variables"][0]
    assert item["design_bounds_source"] == "native defaults after DES import"
    assert item["design_bounds"] == {
        "lower": item["parameter"]["lower"],
        "upper": item["parameter"]["upper"],
    }


def test_native_design_import_rejects_unknown_parm_ids(native_model):
    source, design, _ = native_model
    for suffix, contents in [
        (".xddm", '<Model><Variable VSPID="NOSUCHPARM" Value="1" Min="0" Max="2"/></Model>'),
        (".des", "1\nNOSUCHPARM:Name:Group:Parm: 1\n"),
    ]:
        invalid = design.with_name("unknown" + suffix)
        invalid.write_text(contents)
        with pytest.raises(query.NativeQueryError) as raised:
            query.query_model(
                QueryRequest(
                    kind="design_variables",
                    geometry_file=str(source),
                    design_variables_file=str(invalid),
                )
            )
        assert raised.value.phase == "load_design_variables"
        assert "Unknown design variable parameter" in str(raised.value)
