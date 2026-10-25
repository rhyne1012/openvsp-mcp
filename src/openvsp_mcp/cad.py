"""Generated CAD export scripts and conservative checks of produced CAD files."""

import re
from collections import Counter
from pathlib import Path

from . import core
from .cad_models import ExportRequest
from .native import digest_file
from .operations import run_operation, script_end, script_prelude

_UNITS = {"mm": "LEN_MM", "cm": "LEN_CM", "m": "LEN_M", "in": "LEN_IN", "ft": "LEN_FT"}
_IGES_UNIT_CODES = {"in": 1, "mm": 2, "ft": 4, "m": 6, "cm": 10}

_SETTINGS_HELPER = r"""
bool SetCADParm(string container, string name, string group, double value) {
 string p=FindParm(container,name,group);
 if(!ValidParm(p)) { CheckOperationErrors("cad_settings"); return false; }
 SetParmVal(p,value);
 double scale=abs(value); if(scale<1.0) scale=1.0;
 if(abs(GetParmVal(p)-value)>scale*1e-12) {
  Print("MCP_NATIVE_ERROR:{\"code\":null,\"phase\":\"cad_settings\",\"message\":"+
        J("Native CAD setting did not retain requested value: "+group+"."+name)+"}");
  return false;
 }
 return true;
}
"""


def _script(request: ExportRequest, snapshot: Path, run: Path, token: str) -> str:
    q = core._quote
    extension = "stp" if request.format == "step" else "igs"
    native_format = "EXPORT_" + request.format.upper()
    lines = [_SETTINGS_HELPER, script_prelude(snapshot, request)]
    unit = _UNITS[request.length_unit]
    if request.export_mode == "standard":
        group = request.format.upper() + "Settings"
        lines.append('string cadContainer=FindContainer("Vehicle",0);')
        settings = [
            ("LenUnit", unit),
            ("SplitSurfs", "1"),
            ("SplitSubSurfs", "0"),
            ("ToCubic", "0"),
            ("TrimTE", "0"),
            ("MergeLETE", "0"),
            ("ExportPropMainSurf", "0"),
            ("LabelID", "1"),
            ("LabelName", "1"),
        ]
        if request.format == "step":
            settings += [("Tolerance", repr(request.effective_tolerance)), ("MergePoints", "0")]
        for name, value in settings:
            lines.append(f"if(!SetCADParm(cadContainer,{q(name)},{q(group)},{value})) return 20;")
    else:
        native_format += "_STITCH"
        lines.append('string cadContainer=FindContainer("SurfaceIntersectSettings",0);')
        settings = [
            ("CADLenUnit", "ExportIntersect", unit),
            ("CADLabelID", "ExportIntersect", "1"),
            ("CADLabelName", "ExportIntersect", "1"),
            ("IntersectSubSurfs", "Global", "0"),
            ("DemoteSurfsCubicFlag", "Global", "0"),
        ]
        if request.format == "step":
            settings += [
                ("STEPTol", "ExportIntersect", repr(request.effective_tolerance)),
                ("STEPMergePoints", "ExportIntersect", "0"),
                (
                    "STEPRepresentation",
                    "ExportIntersect",
                    "STEP_" + request.effective_representation.upper(),
                ),
            ]
        for name, group, value in settings:
            lines.append(f"if(!SetCADParm(cadContainer,{q(name)},{q(group)},{value})) return 20;")
    lines += [
        "Update();",
        f"ExportFile({q(run / ('geometry.' + extension))},selectedSet,{native_format});",
        'string result="{\\"openvsp_version\\":"+J(GetVSPVersion());',
        'result+=",\\"selected_set\\":"+formatInt(selectedSet);',
        'result+=",\\"selected_geometry_ids\\":"+Strings(selectedIDs)+"}";',
        script_end(token, "result"),
    ]
    return "\n".join(lines)


def _iges_fields(text: str) -> list[str]:
    """Read the native writer's comma-delimited global record, including Hollerith strings."""
    if not text.startswith(",,"):
        raise ValueError("Unexpected IGES global delimiters")
    fields = []
    position = 0
    while position < len(text):
        hollerith = re.match(r"(\d+)H", text[position:])
        if hollerith:
            start = position + hollerith.end()
            position = start + int(hollerith.group(1))
            if position >= len(text):
                raise ValueError("Truncated IGES global string")
            fields.append(text[start:position])
        else:
            match = re.search(r"[,;]", text[position:])
            if match is None:
                raise ValueError("Unterminated IGES global record")
            end = position + match.start()
            fields.append(text[position:end])
            position = end
        delimiter = text[position]
        if delimiter == ";":
            return fields
        if delimiter != ",":
            raise ValueError("Invalid IGES field separator")
        position += 1
    raise ValueError("Unterminated IGES global record")


def _check_iges(text: str, unit: str) -> dict:
    rows = text.splitlines()
    if not rows or any(len(row) != 80 for row in rows):
        raise ValueError("IGES records must have 80 columns")
    sections = [row[72] for row in rows]
    if any(section not in "SGDPT" for section in sections):
        raise ValueError("Unknown IGES record section")
    counts = Counter(sections)
    if any(counts[section] == 0 for section in "SGDPT") or counts["T"] != 1:
        raise ValueError("Missing or duplicate IGES sections")
    if sections != sorted(sections, key="SGDPT".index) or counts["D"] % 2:
        raise ValueError("Invalid IGES record order or directory pairs")
    for section in "SGDPT":
        seq = [int(row[73:]) for row in rows if row[72] == section]
        if seq != list(range(1, counts[section] + 1)):
            raise ValueError("Invalid IGES record sequence")
    terminator = rows[-1]
    for index, section in enumerate("SGDP"):
        field = terminator[index * 8 : (index + 1) * 8]
        if field[0] != section or int(field[1:]) != counts[section]:
            raise ValueError("IGES terminate counts disagree with file")
    fields = _iges_fields("".join(row[:72] for row in rows if row[72] == "G"))
    if len(fields) < 15 or float(fields[12]) != 1 or int(fields[13]) != _IGES_UNIT_CODES[unit]:
        raise ValueError("IGES model scale or declared length unit does not match request")
    directory = [row for row in rows if row[72] == "D"]
    surface_count = sum(int(row[:8]) in (128, 143, 144, 186) for row in directory[::2])
    if not surface_count:
        raise ValueError("IGES contains no exported CAD surfaces")
    return {
        "records": len(rows),
        "directory_entities": counts["D"] // 2,
        "surface_entities": surface_count,
        "length_unit": unit,
        "model_scale": 1,
    }


def _check_step(text: str, unit: str) -> dict:
    if not text.startswith("ISO-10303-21;") or not text.rstrip().endswith("END-ISO-10303-21;"):
        raise ValueError("Missing STEP exchange-file envelope")
    if not re.search(r"HEADER\s*;.*?ENDSEC\s*;\s*DATA\s*;.*?ENDSEC\s*;", text, re.DOTALL):
        raise ValueError("Missing STEP header or data section")
    if not re.search(r"#\d+\s*=.*?B_SPLINE_SURFACE", text, re.DOTALL):
        raise ValueError("STEP contains no exported CAD surfaces")
    # OpenVSP uses .UNSET. for the metre prefix. Independent importers normalize it.
    prefix = {"mm": r"\.MILLI\.", "cm": r"\.CENTI\.", "m": r"(?:\.UNSET\.|\$)"}
    if unit in prefix:
        matched = re.search(r"SI_UNIT\(\s*" + prefix[unit] + r"\s*,\s*\.METRE\.\s*\)", text)
    else:
        name = "INCH" if unit == "in" else "FOOT"
        matched = re.search(r"CONVERSION_BASED_UNIT\(\s*'" + name + r"'\s*,", text, re.IGNORECASE)
    if not matched:
        raise ValueError("STEP declared length unit does not match request")
    return {"data_entities": len(re.findall(r"(?m)^#\d+\s*=", text)), "length_unit": unit}


def _validate(request: ExportRequest, run: Path, payload: dict) -> dict:
    extension = "stp" if request.format == "step" else "igs"
    artifact = run / ("geometry." + extension)
    text = artifact.read_text(encoding="utf-8", errors="strict")
    try:
        evidence = (_check_step if request.format == "step" else _check_iges)(
            text, request.length_unit
        )
    except (ValueError, IndexError) as exc:
        raise RuntimeError(f"CAD export verification failed: {exc}") from exc
    payload.update(
        artifacts={
            "cad": {
                "path": str(artifact),
                "format": request.format,
                "bytes": artifact.stat().st_size,
                "sha256": digest_file(artifact),
            }
        },
        settings={
            "export_mode": request.export_mode,
            "length_unit": request.length_unit,
            "coordinate_scaling": "unchanged",
            "tolerance": request.effective_tolerance,
            "step_representation": request.effective_representation,
        },
        verification={
            "native_errors": "none",
            "file_structure": "passed",
            "evidence": evidence,
            "topology": "not_assessed",
            "watertight": None,
            "scope": "File structure and declared units checked. Reopen with an independent CAD kernel to assess topology, dimensions and closed shells. Split-and-stitch does not intersect or fuse different components.",
        },
    )
    return payload


def export_geometry(request: ExportRequest) -> dict:
    """Export selected geometry from a private model copy with explicit unit semantics."""
    return run_operation(
        request,
        "export_geometry",
        lambda snapshot, run, token: _script(request, snapshot, run, token),
        lambda run, payload: _validate(request, run, payload),
    )
