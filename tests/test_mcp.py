"""Transport contract tests that run the actual server without requiring OpenVSP."""

import asyncio
import json
import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def test_stdio_inspection_validation_and_error_response(tmp_path):
    model = tmp_path / "wing.vsp3"
    original = "<Vsp_Geometry><Vehicle><Geom><ParmContainer><ID>x</ID><Name>Wing</Name></ParmContainer><GeomBase><TypeName>Wing</TypeName></GeomBase></Geom></Vehicle></Vsp_Geometry>"
    model.write_text(original)
    env = dict(
        os.environ,
        OPENVSP_BIN=str(tmp_path / "no_binary"),
        VSPAERO_BIN=str(tmp_path / "no_solver"),
    )

    async def exercise():
        server = StdioServerParameters(command=sys.executable, args=["-m", "openvsp_mcp"], env=env)
        async with (
            stdio_client(server) as (read, write),
            ClientSession(read, write) as session,
        ):
            initialized = await session.initialize()
            assert initialized.serverInfo.version == "0.5.0"
            tools = {t.name: t for t in (await session.list_tools()).tools}
            assert set(tools) == {
                "openvsp.inspect",
                "openvsp.modify",
                "openvsp.run_vspaero",
                "openvsp.health",
                "openvsp.create_model",
                "openvsp.preview",
                "openvsp.preflight",
                "openvsp.sweep",
                "openvsp.query",
                "openvsp.read_results",
                "openvsp.set_parameters",
            }
            assert "analysis" in json.dumps(tools["openvsp.run_vspaero"].inputSchema)
            assert all(tool.outputSchema for tool in tools.values())
            health = await session.call_tool("openvsp.health", {})
            assert health.structuredContent["status"] == "error"
            assert health.structuredContent["package_version"] == initialized.serverInfo.version
            inspected = await session.call_tool(
                "openvsp.inspect", {"request": {"geometry_file": str(model)}}
            )
            assert not inspected.isError
            assert inspected.structuredContent["geom_ids"] == ["x"]
            assert model.read_text() == original
            invalid = await session.call_tool(
                "openvsp.run_vspaero",
                {"request": {"geometry_file": str(model), "analysis": {"mach": -1}}},
            )
            assert invalid.isError
            failed = await session.call_tool(
                "openvsp.modify", {"request": {"geometry_file": str(model)}}
            )
            assert failed.isError
            assert "Run artifacts:" in str(failed.content)
            assert model.read_text() == original

    asyncio.run(exercise())
    manifests = list((tmp_path / "openvsp_runs").glob("*/manifest.json"))
    assert len(manifests) == 1
    assert json.loads(Path(manifests[0]).read_text())["status"] == "failed"
