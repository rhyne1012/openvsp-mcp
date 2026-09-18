"""Read geometry metadata directly, without launching OpenVSP or rewriting the file."""

from pathlib import Path
from xml.etree import ElementTree as ET

from .models import OpenVSPInspectResponse


def describe_geometry(geometry_file: str) -> OpenVSPInspectResponse:
    try:
        root = ET.parse(Path(geometry_file).expanduser()).getroot()
    except (OSError, ET.ParseError) as exc:
        raise RuntimeError(f"Cannot read OpenVSP geometry: {exc}") from exc
    if root.tag != "Vsp_Geometry":
        raise RuntimeError("Not an OpenVSP .vsp3 geometry")
    ids, wings, lines = [], [], []
    for geom in root.findall("./Vehicle/Geom"):
        gid = geom.findtext("ParmContainer/ID", "")
        name = geom.findtext("ParmContainer/Name", "")
        kind = geom.findtext("GeomBase/TypeName", "")
        if not gid:
            continue
        ids.append(gid)
        if kind.lower() == "wing":
            wings.append(name)
        lines.append(f"{gid}:{name}:{kind}")
    return OpenVSPInspectResponse(geom_ids=ids, wing_names=wings, info_log="\n".join(lines))
