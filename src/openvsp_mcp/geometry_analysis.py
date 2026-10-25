"""Audited CompGeom and Projection operations; no caller-selected native analysis."""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path

from . import core
from .analysis_models import GeometryAnalysisRequest
from .operations import run_operation, script_end, script_prelude


def _script(request: GeometryAnalysisRequest, snapshot: Path, run: Path, token: str) -> str:
    q = core._quote
    native_name = "CompGeom" if request.analysis == "comp_geom" else "Projection"
    result_name = "Comp_Geom" if request.analysis == "comp_geom" else "Projection"
    mode_expression = '"null"' if request.mode_id is None else f"J({q(request.mode_id)})"
    lines = [script_prelude(snapshot, request), f"SetAnalysisInputDefaults({q(native_name)});"]
    if request.analysis == "comp_geom":
        for name, value in {
            "Set": "selectedSet",
            "DegenSet": "SET_NONE",
            "HalfMeshFlag": "0",
            "SubSurfFlag": "1",
            "UseModeFlag": "0",  # The prelude has already applied the requested mode.
            "WriteCSVFlag": "0",
            "WriteTXTFlag": "0",
        }.items():
            lines.append(f'SetIntAnalysisInput("CompGeom",{q(name)},{{{value}}});')
    else:
        for name, value in {
            "TargetType": "SET_TARGET",
            "TargetSet": "selectedSet",
            "BoundaryType": "NO_BOUNDARY",
            "DirectionType": "VEC_PROJ",
            "TargetHullFlag": "0",
            "BoundaryHullFlag": "0",
            "DiskSegmentBreakdown": "0",
        }.items():
            lines.append(f'SetIntAnalysisInput("Projection",{q(name)},{{{value}}});')
        vector = ",".join(format(value, ".17g") for value in request.direction_vector)
        lines.append(f'SetVec3dAnalysisInput("Projection","Direction",{{vec3d({vector})}});')
    lines += [
        'if(CheckOperationErrors("analysis_inputs")!=0) return 33;',
        f"string rid=ExecAnalysis({q(native_name)});",
        'if(CheckOperationErrors("analysis_execute")!=0) return 34;',
        f"if(rid.length()==0 || GetResultsName(rid)!={q(result_name)}) return 30;",
        # Save every native result field/block, including projection paths, outside stdout.
        'string raw="{\\"result_id\\":"+J(rid)+",\\"result_name\\":"+J(GetResultsName(rid))+",\\"data\\":{";',
        "array<string> names=GetAllDataNames(rid);",
        'for(uint i=0;i<names.length();i++) { if(i>0) raw+=",";',
        'string name=names[i]; int type=GetResultsType(rid,name); string typeName="";',
        'if(type==INT_DATA) typeName="int"; else if(type==DOUBLE_DATA) typeName="double";',
        'else if(type==STRING_DATA) typeName="string"; else if(type==VEC3D_DATA) typeName="vec3d";',
        "else return 31;",
        'raw+=J(name)+":{\\"type\\":"+J(typeName)+",\\"type_code\\":"+formatInt(type)+",\\"blocks\\":[";',
        'for(int j=0;j<GetNumData(rid,name);j++) { if(j>0) raw+=",";',
        "if(type==INT_DATA) raw+=Ints(GetIntResults(rid,name,j));",
        "else if(type==DOUBLE_DATA) raw+=Doubles(GetDoubleResults(rid,name,j));",
        "else if(type==STRING_DATA) raw+=Strings(GetStringResults(rid,name,j));",
        'else { array<vec3d> values=GetVec3dResults(rid,name,j); raw+="[";',
        'for(uint k=0;k<values.length();k++) { if(k>0) raw+=",";',
        'raw+="["+N(values[k].x())+","+N(values[k].y())+","+N(values[k].z())+"]"; } raw+="]"; }',
        '} raw+="]}"; } raw+="}}";',
        f"WriteResultsCSVFile(rid,{q(run / 'raw_results.csv')});",
        f'file rawFile; if(rawFile.open({q(run / "raw_results.json")},"w")<0) return 32;',
        "rawFile.writeString(raw); rawFile.close();",
        'string result="{\\"openvsp_version\\":"+J(GetVSPVersion())+",\\"result_id\\":"+J(rid);',
        'result+=",\\"result_name\\":"+J(GetResultsName(rid));',
        'result+=",\\"selection\\":{\\"geom_set\\":"+formatInt(selectedSet)+",\\"geom_ids\\":"+Strings(selectedIDs);',
        f'result+=",\\"mode_id\\":"+{mode_expression};',
        'result+="}}";',
        script_end(token, "result"),
    ]
    return "\n".join(lines)


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _load_raw(run: Path, payload: dict, expected_name: str) -> dict:
    try:
        raw = json.loads((run / "raw_results.json").read_text(encoding="utf-8"))
        if (
            raw["result_name"] != expected_name
            or raw["result_id"] != payload["result_id"]
            or payload["result_name"] != expected_name
            or not raw["result_id"]
        ):
            raise ValueError("result identity differs")
        if not isinstance(raw["data"], dict) or not raw["data"]:
            raise ValueError("empty Results data")
        for field in raw["data"].values():
            kind = field["type"]
            if kind not in ("int", "double", "string", "vec3d"):
                raise ValueError("unsupported Results data type")
            if not isinstance(field["blocks"], list) or not field["blocks"]:
                raise ValueError("missing Results data blocks")
            for block in field["blocks"]:
                if not isinstance(block, list):
                    raise TypeError("invalid Results data block")
                for value in block:
                    valid = (
                        (kind == "int" and type(value) is int)
                        or (kind == "double" and _number(value))
                        or (kind == "string" and isinstance(value, str))
                        or (
                            kind == "vec3d"
                            and isinstance(value, list)
                            and len(value) == 3
                            and all(_number(coordinate) for coordinate in value)
                        )
                    )
                    if not valid:
                        raise ValueError("nonfinite or incorrectly typed Results data")
        with (run / "raw_results.csv").open(encoding="utf-8", newline="") as stream:
            rows = list(csv.reader(stream))
            if not rows or rows[0] != ["Results_Name", expected_name]:
                raise ValueError("CSV result identity differs")
        # Native CSV is an independent retained export. Reject truncated or conflicting
        # numeric columns, rather than treating the existence of its header as success.
        for name, field in raw["data"].items():
            if field["type"] not in ("int", "double"):
                continue
            columns = [row[1:] for row in rows if row and row[0] == name]
            if len(columns) != len(field["blocks"]):
                raise ValueError(f"CSV data blocks differ for {name}")
            for column, block in zip(columns, field["blocks"]):
                numbers = [float(value) for value in column]
                if len(numbers) != len(block) or not all(
                    math.isfinite(actual)
                    and math.isclose(actual, expected, rel_tol=1e-14, abs_tol=0)
                    for actual, expected in zip(numbers, block)
                ):
                    raise ValueError(f"CSV numeric data differs for {name}")
        return raw
    except (OSError, ValueError, KeyError, TypeError, OverflowError, csv.Error) as exc:
        raise RuntimeError(f"Invalid native geometry analysis Results: {exc}") from exc


def _data(raw: dict, name: str, kind: str, count: int | None = None) -> list:
    field = raw["data"].get(name, {})
    blocks = field.get("blocks", [])
    if field.get("type") != kind or len(blocks) != 1:
        raise RuntimeError(f"Expected one native {kind} Results block for {name}")
    values = blocks[0]
    if count is not None and len(values) != count:
        raise RuntimeError(f"Native Results length mismatch for {name}")
    return values


def _areas(values: list) -> list:
    if any(value < 0 for value in values):
        raise RuntimeError("Native geometry analysis returned a negative area")
    return values


def _validate(request: GeometryAnalysisRequest, run: Path, payload: dict) -> dict:
    native_name = "Comp_Geom" if request.analysis == "comp_geom" else "Projection"
    raw = _load_raw(run, payload, native_name)
    warnings = [
        "Measurements use the model's existing tessellation; accuracy depends on tessellation and geometry quality."
    ]
    result = {
        **payload,
        "analysis": request.analysis,
        "length_unit": request.length_unit,
        "units": {"area": f"{request.length_unit}^2", "volume": f"{request.length_unit}^3"},
        "unit_conversion_applied": False,
        "raw_results_csv": str(run / "raw_results.csv"),
        "raw_results_json": str(run / "raw_results.json"),
        "artifacts": {
            "raw_results.csv": str(run / "raw_results.csv"),
            "raw_results.json": str(run / "raw_results.json"),
        },
        "warnings": warnings,
    }
    if request.analysis == "comp_geom":
        names = _data(raw, "Comp_Name", "string")
        if not names:
            raise RuntimeError("CompGeom produced no component measurements")
        values = {
            key: _data(raw, native, "double", len(names))
            for key, native in {
                "wetted_area": "Wet_Area",
                "wetted_volume": "Wet_Vol",
                "theoretical_area": "Theo_Area",
                "theoretical_volume": "Theo_Vol",
            }.items()
        }
        for key in ("wetted_area", "theoretical_area"):
            _areas(values[key])
        result["components"] = [
            {"row_index": i, "name": name, **{key: column[i] for key, column in values.items()}}
            for i, name in enumerate(names)
        ]
        result["summary"] = {
            key: _data(raw, native, "double", 1)[0]
            for key, native in {
                "wetted_area": "Total_Wet_Area",
                "wetted_volume": "Total_Wet_Vol",
                "theoretical_area": "Total_Theo_Area",
                "theoretical_volume": "Total_Theo_Vol",
            }.items()
        }
        _areas([result["summary"][key] for key in ("wetted_area", "theoretical_area")])
        diagnostics = {
            key: _data(raw, name, "int", 1)[0]
            for key, name in {
                "open_meshes_removed": "Num_Open_Meshes_Removed",
                "open_meshes_merged": "Num_Open_Meshes_Merged",
                "degenerate_triangles_removed": "Num_Degen_Tris_Removed",
                "total_meshes": "Total_Num_Meshes",
                "total_triangles": "Total_Num_Tris",
            }.items()
        }
        if any(value < 0 for value in diagnostics.values()):
            raise RuntimeError("Negative CompGeom diagnostic count")
        result["diagnostics"] = diagnostics
        if diagnostics["open_meshes_removed"]:
            warnings.append("CompGeom removed open meshes; totals exclude those meshes.")
        if diagnostics["open_meshes_merged"]:
            warnings.append("CompGeom merged open meshes while constructing its measurement mesh.")
        result["component_semantics"] = (
            "Native CompGeom mesh rows; names are not unique geometry identifiers. "
            "Volumes retain native signs, including negative-volume components."
        )
    else:
        ids = _data(raw, "Comp_IDs", "string")
        names = _data(raw, "Comp_Names", "string", len(ids))
        areas = _areas(_data(raw, "Comp_Areas", "double", len(ids)))
        if not ids:
            raise RuntimeError("Projection produced no component measurements")
        if not set(ids).issubset(payload["selection"]["geom_ids"]):
            raise RuntimeError("Projection returned components outside the selected geometry")
        direction = _data(raw, "Direction", "vec3d", 1)[0]
        if not all(
            math.isclose(actual, expected, abs_tol=1e-12, rel_tol=1e-12)
            for actual, expected in zip(direction, request.direction_vector)
        ):
            raise RuntimeError("Native projection direction differs from the requested direction")
        result["projection_direction"] = direction
        result["components"] = [
            {"geom_id": geom_id, "name": name, "projected_area": area}
            for geom_id, name, area in zip(ids, names, areas)
        ]
        result["summary"] = {"projected_area": _areas(_data(raw, "Area", "double", 1))[0]}
        result["component_semantics"] = (
            "Individual projected component areas; their sum can exceed the union projected area when silhouettes overlap."
        )
    return result


def geometry_analysis(request: GeometryAnalysisRequest) -> dict:
    """Measure a private model copy and retain validated native CSV/JSON artifacts."""
    return run_operation(
        request,
        "geometry_analysis",
        lambda snapshot, run, token: _script(request, snapshot, run, token),
        lambda run, payload: _validate(request, run, payload),
    )
