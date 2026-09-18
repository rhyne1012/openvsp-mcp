"""Build an aircraft, then exercise inspect, modify, failure and solve over MCP stdio."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from openvsp_mcp.core import OPENVSP_BIN
from openvsp_mcp.models import OpenVSPRequest, OpenVSPResponse, VSPAeroSettings


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


async def main() -> None:
    root = Path(__file__).resolve().parent
    out = Path(os.environ.get("OPENVSP_SMOKE_OUTPUT", "smoke_outputs")).resolve()
    out.mkdir(parents=True, exist_ok=True)
    with (out / "build.log").open("w") as log:
        await asyncio.to_thread(
            subprocess.run,
            [OPENVSP_BIN, "-script", str(root / "build.vspscript")],
            cwd=out,
            stdout=log,
            stderr=subprocess.STDOUT,
            check=True,
            timeout=120,
        )
    model = out / "simple_aircraft.vsp3"
    before = digest(model)
    request = OpenVSPRequest(
        geometry_file=str(model),
        case_name="simple_aircraft",
        output_dir=str(out),
        analysis=VSPAeroSettings(
            thick_geom_set=3, thin_geom_set=4, sref=12, bref=10, cref=1.2444444444, xcg=3
        ),
    )
    server = StdioServerParameters(
        command=sys.executable, args=["-m", "openvsp_mcp"], env=dict(os.environ)
    )
    with (out / "mcp.log").open("w") as errlog:
        async with (
            stdio_client(server, errlog=errlog) as (read, write),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            tools = {tool.name for tool in (await session.list_tools()).tools}
            assert tools == {"openvsp.inspect", "openvsp.modify", "openvsp.run_vspaero"}
            info = await session.call_tool(
                "openvsp.inspect", {"request": {"geometry_file": str(model)}}
            )
            assert not info.isError, info.content
            assert len(info.structuredContent["geom_ids"]) == 4
            assert digest(model) == before

            editable = out / "editable_aircraft.vsp3"
            shutil.copy2(model, editable)
            modified = await session.call_tool(
                "openvsp.modify",
                {
                    "request": {
                        "geometry_file": str(editable),
                        "output_dir": str(out),
                        "case_name": "rename",
                        "set_commands": [
                            {"command": 'SetGeomName(FindGeom("Main_Wing",0),"Renamed_Wing")'}
                        ],
                    }
                },
                read_timeout_seconds=timedelta(seconds=120),
            )
            assert not modified.isError, modified.content
            renamed = await session.call_tool(
                "openvsp.inspect", {"request": {"geometry_file": str(editable)}}
            )
            assert "Renamed_Wing" in renamed.structuredContent["wing_names"]
            after_edit = digest(editable)
            failed = await session.call_tool(
                "openvsp.modify",
                {
                    "request": {
                        "geometry_file": str(editable),
                        "output_dir": str(out),
                        "case_name": "bad_parameter",
                        "set_commands": [{"command": 'SetParmVal("missing_parameter_id",1.0)'}],
                    }
                },
                read_timeout_seconds=timedelta(seconds=120),
            )
            assert failed.isError, "Invalid API call was reported as success"
            assert digest(editable) == after_edit

            result = await session.call_tool(
                "openvsp.run_vspaero",
                {"request": request.model_dump()},
                read_timeout_seconds=timedelta(seconds=660),
            )
            assert not result.isError, result.content
            response = OpenVSPResponse.model_validate(result.structuredContent)
    assert digest(model) == before
    assert all(Path(p).is_file() for p in response.artifacts.values())
    # Broad smoke bounds, not a validation of aerodynamic accuracy.
    assert 0.1 < response.coefficients["CLtot"] < 0.4
    assert 0 < response.coefficients["CDtot"] < 0.1
    (out / "smoke_result.json").write_text(response.model_dump_json(indent=2) + "\n")
    print(
        json.dumps(
            {
                "status": "PASS",
                "transport": "MCP stdio",
                "checks": [
                    "inspect read-only",
                    "modify saved",
                    "API error rejected",
                    "VSPAERO solve",
                ],
                "CL": response.coefficients["CLtot"],
                "CD": response.coefficients["CDtot"],
                "run_directory": response.run_directory,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
