"""Exercise all 18 tools through a local SSH launcher; all model paths are remote."""

import argparse
import asyncio
import json
import math
from datetime import timedelta
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def exercise(args):
    args.output.mkdir(parents=True, exist_ok=True)
    evidence = {"transport": "SSH stdio", "calls": {}, "remote_output": args.remote_output}
    server = StdioServerParameters(command=str(args.command.resolve()))
    called = set()
    with (args.output / "mcp.log").open("w") as log:
        async with (
            stdio_client(server, errlog=log) as (read, write),
            ClientSession(read, write) as session,
        ):
            evidence["initialize"] = (await session.initialize()).model_dump()
            names = {t.name for t in (await session.list_tools()).tools}
            assert len(names) == 18

            async def call(name, request=None, label=None, error=False):
                result = await session.call_tool(
                    "openvsp." + name,
                    {} if request is None else {"request": request},
                    read_timeout_seconds=timedelta(seconds=1260),
                )
                called.add("openvsp." + name)
                assert bool(result.isError) == error, result.content
                value = result.model_dump() if error else result.structuredContent
                evidence["calls"][label or name] = value
                if name != "batch_status":
                    (args.output / "ssh-smoke.json").write_text(
                        json.dumps(evidence, indent=2) + "\n"
                    )
                    print(label or name, flush=True)
                return value

            async def done(directory):
                for _ in range(2400):
                    result = await call("batch_status", {"batch_directory": directory})
                    if not result["active"]:
                        return result
                    await asyncio.sleep(0.5)
                raise AssertionError("Batch exceeded time budget")

            health = await call("health")
            assert health["status"] == "ok"
            created = await call("create_model", {"output_dir": args.remote_output})
            model = created["geometry_path"]
            assert model.startswith("/")
            info = await call("inspect", {"geometry_file": model})
            assert len(info["geom_ids"]) == 4
            settings = {
                "thick_geom_set": 3,
                "thin_geom_set": 4,
                "sref": 12,
                "bref": 10,
                "cref": 1.2444444444,
                "xcg": 3,
                "length_unit": "m",
                "ncpu": 2,
            }
            request = {
                "geometry_file": model,
                "output_dir": args.remote_output,
                "analysis": settings,
            }
            await call("preview", request)
            await call("preflight", request)
            await call(
                "export_geometry",
                {
                    "geometry_file": model,
                    "output_dir": args.remote_output,
                    "format": "step",
                    "export_mode": "split_and_stitch",
                    "length_unit": "m",
                },
            )
            await call(
                "geometry_analysis",
                {
                    "geometry_file": model,
                    "output_dir": args.remote_output,
                    "analysis": "projection",
                    "projection_direction": "z",
                    "length_unit": "m",
                },
            )
            for kind in ("geometry", "containers", "sets", "modes", "design_variables"):
                await call("query", {"kind": kind, "geometry_file": model}, kind)
            await call("query", {"kind": "analysis"}, "analysis")
            params = await call(
                "query", {"kind": "parameters", "geometry_file": model, "limit": 200}, "parameters"
            )
            length = next(
                p for p in params["parameters"] if p["name"] == "Length" and p["group"] == "Design"
            )
            for label, value in [("edit", length["value"] + 0.01), ("restore", length["value"])]:
                changed = await call(
                    "set_parameters",
                    {
                        "geometry_file": model,
                        "output_dir": args.remote_output,
                        "edits": [{"parm_id": length["id"], "value": value}],
                    },
                    label,
                )
                assert abs(changed["parameter_values"][length["id"]] - value) < 1e-9
            await call(
                "modify",
                {
                    "geometry_file": model,
                    "output_dir": args.remote_output,
                    "set_commands": [
                        {"command": 'SetGeomName(FindGeom("Main_Wing",0),"SSH_Wing")'}
                    ],
                },
            )
            await call(
                "set_parameters",
                {
                    "geometry_file": model,
                    "output_dir": args.remote_output,
                    "edits": [{"parm_id": "missing_parameter", "value": 1}],
                },
                "invalid_parameter",
                error=True,
            )
            solved = await call("run_vspaero", request)
            assert all(math.isfinite(v) for v in solved["coefficients"].values())
            assert 0.1 < solved["coefficients"]["CLtot"] < 0.4
            saved = await call(
                "read_results",
                {"manifest_file": solved["manifest_path"], "coefficient_names": ["CLtot", "CDtot"]},
            )
            assert saved["coefficients"]["CLtot"] == solved["coefficients"]["CLtot"]
            swept = await call(
                "sweep",
                {
                    "geometry_file": model,
                    "output_dir": args.remote_output,
                    "timeout_seconds": 1200,
                    "conditions": [settings | {"alpha": a} for a in (0, 3)],
                },
            )
            assert swept["status"] == "success"
            batch = await call(
                "batch_submit",
                {
                    "geometry_file": model,
                    "output_dir": args.remote_output,
                    "cpu_budget": 2,
                    "max_parallel_jobs": 1,
                    "cases": [
                        {"case_id": "first", "analysis": settings},
                        {"case_id": "queued", "analysis": settings | {"alpha": 1}},
                    ],
                },
            )
            directory = batch["batch_directory"]
            await call("batch_cancel", {"batch_directory": directory, "case_ids": ["queued"]})
            status = await done(directory)
            assert status["counts"]["success"] == 1 and status["counts"]["cancelled"] == 1
            await call("batch_resume", {"batch_directory": directory})
            assert (await done(directory))["status"] == "success"
            await call("batch_export", {"batch_directory": directory})
            await session.send_ping()
            assert called == names
        # New SSH process/server: durable batch results must still be readable.
        async with (
            stdio_client(server, errlog=log) as (read, write),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            result = await session.call_tool(
                "openvsp.batch_status", {"request": {"batch_directory": directory}}
            )
            assert not result.isError and result.structuredContent["status"] == "success"
            await session.send_ping()
            evidence["reconnect"] = result.structuredContent
    evidence.update(status="PASS", tools=sorted(called))
    (args.output / "ssh-smoke.json").write_text(json.dumps(evidence, indent=2) + "\n")
    print(
        json.dumps(
            {
                "status": "PASS",
                "tools": len(called),
                "evidence": str(args.output / "ssh-smoke.json"),
            }
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--command", type=Path, required=True, help="Local executable SSH launcher")
    parser.add_argument("--remote-output", required=True, help="Absolute directory on the SSH host")
    parser.add_argument("--output", type=Path, required=True, help="Local evidence directory")
    args = parser.parse_args()
    if not args.remote_output.startswith("/"):
        parser.error("--remote-output must be an absolute POSIX path")
    asyncio.run(exercise(args))
