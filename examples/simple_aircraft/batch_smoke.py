"""Opt-in native batch regression using two public models; no private inputs required."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
from importlib.resources import files
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from openvsp_mcp import __version__


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


async def main():
    output = Path(os.environ.get("OPENVSP_BATCH_SMOKE_OUTPUT", "smoke_outputs/batch")).resolve()
    output.mkdir(parents=True, exist_ok=True)
    server = StdioServerParameters(
        command=sys.executable,
        args=["-m", "openvsp_mcp"],
        env=dict(os.environ, OPENVSP_CPU_BUDGET="4"),
    )
    evidence = {"package_version": __version__, "models": {}}
    with (output / "mcp.log").open("w") as log:
        async with (
            stdio_client(server, errlog=log) as (read, write),
            ClientSession(read, write) as session,
        ):
            await session.initialize()

            async def call(name, request):
                result = await session.call_tool("openvsp." + name, {"request": request})
                assert not result.isError, result.content
                return result.structuredContent

            async def done(directory):
                for _ in range(2400):
                    status = await call("batch_status", {"batch_directory": directory})
                    if not status["active"]:
                        return status
                    await asyncio.sleep(0.25)
                raise AssertionError("Native batch exceeded smoke time budget")

            aircraft = await call("create_model", {"output_dir": str(output)})
            wing = output / "rect_wing.vsp3"
            wing.write_bytes((files("openvsp_mcp.data") / "rect_wing.vsp3").read_bytes())
            for label, model, settings in [
                (
                    "conventional_aircraft",
                    Path(aircraft["geometry_path"]),
                    {
                        "thick_geom_set": 3,
                        "thin_geom_set": 4,
                        "sref": 12,
                        "bref": 10,
                        "cref": 1.2444444444,
                        "xcg": 3,
                        "length_unit": "m",
                        "ncpu": 2,
                    },
                ),
                ("rectangular_wing", wing, {"ncpu": 2, "length_unit": "m"}),
            ]:
                before = digest(model)
                info = await call("inspect", {"geometry_file": str(model)})
                params = await call(
                    "query",
                    {
                        "kind": "parameters",
                        "geometry_file": str(model),
                        "geom_id": info["geom_ids"][0],
                        "limit": 200,
                    },
                )
                parm = next(
                    p
                    for p in params["parameters"]
                    if p["name"] == "X_Rel_Location" and p["group"] == "XForm"
                )
                cases = [
                    {"case_id": "alpha1", "analysis": settings | {"alpha": 1}},
                    {"case_id": "alpha3", "analysis": settings | {"alpha": 3}},
                    {
                        "case_id": "translated",
                        "analysis": settings | {"alpha": 3},
                        "parameter_edits": [{"parm_id": parm["id"], "value": parm["value"] + 0.05}],
                    },
                ]
                submitted = await call(
                    "batch_submit",
                    {
                        "geometry_file": str(model),
                        "output_dir": str(output),
                        "cases": cases,
                        "cpu_budget": 4,
                        "max_parallel_jobs": 2,
                    },
                )
                directory = submitted["batch_directory"]
                # Cancel the queued third case while the first two native operations run.
                await call(
                    "batch_cancel", {"batch_directory": directory, "case_ids": ["translated"]}
                )
                finished = await done(directory)
                assert (
                    finished["counts"]["success"] == 2 and finished["counts"]["cancelled"] == 1
                ), finished
                manifest_path = Path(directory) / "batch.json"
                first = json.loads(manifest_path.read_text())
                original_runs = [r["response"]["run_directory"] for r in first["cases"][:2]]
                await call("batch_resume", {"batch_directory": directory})
                finished = await done(directory)
                assert finished["status"] == "success", finished
                final = json.loads(manifest_path.read_text())
                assert original_runs == [r["response"]["run_directory"] for r in final["cases"][:2]]
                assert (
                    abs(
                        final["cases"][2]["response"]["parameter_values"][parm["id"]]
                        - (parm["value"] + 0.05)
                    )
                    < 1e-9
                )
                assert finished["peak_reserved_threads"] <= 4
                exported = await call("batch_export", {"batch_directory": directory})
                rows = json.loads(Path(exported["json_path"]).read_text())["cases"]
                assert len(rows) == 3 and all(r["status"] == "success" for r in rows)
                if label == "conventional_aircraft":
                    assert abs(rows[1]["coefficient.CLtot"] - 0.233342828966) < 1e-8
                    assert abs(rows[1]["coefficient.CDtot"] - 0.009594823037) < 1e-8
                assert digest(model) == before
                evidence["models"][label] = {
                    "batch_directory": directory,
                    "status": finished,
                    "export": exported,
                }
                # A typo in a parameter is an explicit failed case, not a successful empty result.
                bad_cases = [
                    {
                        "case_id": "invalid_parameter",
                        "analysis": settings,
                        "parameter_edits": [{"parm_id": "invalid_public_smoke_id", "value": 1}],
                    }
                ]
                if label == "rectangular_wing":
                    # At exact zero lift this fixture yields undefined efficiency ratios.
                    bad_cases.append(
                        {"case_id": "undefined_efficiency", "analysis": settings | {"alpha": 0}}
                    )
                bad = await call(
                    "batch_submit",
                    {
                        "geometry_file": str(model),
                        "output_dir": str(output),
                        "cases": bad_cases,
                        "failure_policy": "continue",
                    },
                )
                assert (await done(bad["batch_directory"]))["counts"]["failed"] == len(bad_cases)
                assert digest(model) == before
            await session.send_ping()
    (output / "batch_smoke_result.json").write_text(json.dumps(evidence, indent=2) + "\n")
    print(
        json.dumps(
            {
                "status": "PASS",
                "models": list(evidence["models"]),
                "checks": [
                    "parallel native solves",
                    "private per-case parameter edits",
                    "cancel queued case",
                    "resume reuses successes",
                    "CSV/JSON export",
                    "bad parameter rejection",
                    "source preservation",
                ],
                "evidence": str(output / "batch_smoke_result.json"),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
