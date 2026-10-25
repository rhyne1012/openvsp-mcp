"""Exercise 0.10 geometry workflows over real stdio; requires OpenVSP 3.55."""

import argparse
import asyncio
import hashlib
import json
import os
import sys
from datetime import timedelta
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def exercise(output):
    output.mkdir(parents=True, exist_ok=True)
    evidence = {"transport": "stdio", "calls": {}}
    server = StdioServerParameters(
        command=sys.executable,
        args=["-I", "-m", "openvsp_mcp"],
        env={
            key: os.environ[key]
            for key in ("OPENVSP_BIN", "VSPAERO_BIN", "OPENVSP_CPU_BUDGET")
            if key in os.environ
        },
    )
    with (output / "stdio.log").open("w") as log:
        async with (
            stdio_client(server, errlog=log) as (read, write),
            ClientSession(read, write) as session,
        ):
            evidence["initialize"] = (await session.initialize()).model_dump(mode="json")
            catalog = (await session.list_tools()).tools
            assert len(catalog) == 18
            evidence["tools"] = sorted(tool.name for tool in catalog)

            async def call(name, request, label=None, error=False):
                result = await session.call_tool(
                    "openvsp." + name,
                    {"request": request},
                    read_timeout_seconds=timedelta(seconds=360),
                )
                assert bool(result.isError) == error, result.content
                data = result.model_dump(mode="json") if error else result.structuredContent
                evidence["calls"][label or name] = data
                (output / "geometry-smoke.json").write_text(json.dumps(evidence, indent=2) + "\n")
                return data

            created = await call("create_model", {"output_dir": str(output), "case_name": "source"})
            source = Path(created["geometry_path"])
            before = hashlib.sha256(source.read_bytes()).hexdigest()
            common = {"geometry_file": str(source), "output_dir": str(output), "length_unit": "m"}
            for kind in ("geometry", "containers", "sets", "modes", "design_variables"):
                await call("query", {"geometry_file": str(source), "kind": kind}, kind)
            await call(
                "query",
                {
                    "geometry_file": str(source),
                    "kind": "parameters",
                    "container_name": "VSPAEROSettings",
                    "limit": 10,
                },
                "settings_parameters",
            )
            for fmt in ("step", "iges"):
                for mode in ("standard", "split_and_stitch"):
                    result = await call(
                        "export_geometry",
                        {
                            **common,
                            "format": fmt,
                            "export_mode": mode,
                        },
                        fmt + "_" + mode,
                    )
                    assert result["status"] == "ok"
                    artifact = result["artifacts"]["cad"]
                    assert (
                        hashlib.sha256(Path(artifact["path"]).read_bytes()).hexdigest()
                        == artifact["sha256"]
                    )
                    saved = await call(
                        "read_results",
                        {
                            "manifest_file": result["manifest_path"],
                            "log": "openvsp",
                        },
                        fmt + "_" + mode + "_saved",
                    )
                    assert saved["artifacts"] == result["artifacts"]
            for analysis in ("comp_geom", "projection"):
                request = {**common, "analysis": analysis}
                if analysis == "projection":
                    request["projection_direction"] = "z"
                result = await call("geometry_analysis", request, analysis)
                assert result["status"] == "ok"
                saved = await call(
                    "read_results",
                    {
                        "manifest_file": result["manifest_path"],
                    },
                    analysis + "_saved",
                )
                assert saved["summary"] == result["summary"]
            await call(
                "geometry_analysis",
                {
                    **common,
                    "analysis": "comp_geom",
                    "geom_set": 99999,
                },
                "invalid_set",
                error=True,
            )
            after = hashlib.sha256(source.read_bytes()).hexdigest()
            assert before == after
            evidence["source_sha256"] = before
            evidence["source_preserved"] = True
            await session.send_ping()
    evidence["status"] = "PASS"
    (output / "geometry-smoke.json").write_text(json.dumps(evidence, indent=2) + "\n")
    print(json.dumps({"status": "PASS", "evidence": str(output / "geometry-smoke.json")}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(exercise(args.output.resolve()))
