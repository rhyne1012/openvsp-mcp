"""Exercise the eleven legacy tools; batch_smoke.py covers the five batch tools."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import sys
from datetime import timedelta
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from openvsp_mcp import __version__
from openvsp_mcp.models import OpenVSPRequest, OpenVSPResponse, VSPAeroSettings


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


async def main() -> None:
    out = Path(os.environ.get("OPENVSP_SMOKE_OUTPUT", "smoke_outputs")).resolve()
    out.mkdir(parents=True, exist_ok=True)
    server = StdioServerParameters(
        command=sys.executable, args=["-m", "openvsp_mcp"], env=dict(os.environ)
    )
    evidence = {}
    with (out / "mcp.log").open("w") as errlog:
        async with (
            stdio_client(server, errlog=errlog) as (read, write),
            ClientSession(read, write) as session,
        ):
            initialized = await session.initialize()
            assert initialized.serverInfo.version == __version__
            names = {tool.name for tool in (await session.list_tools()).tools}
            assert names == {
                "openvsp." + name
                for name in [
                    "health",
                    "create_model",
                    "inspect",
                    "modify",
                    "preview",
                    "preflight",
                    "run_vspaero",
                    "sweep",
                    "query",
                    "set_parameters",
                    "read_results",
                    "batch_submit",
                    "batch_status",
                    "batch_cancel",
                    "batch_resume",
                    "batch_export",
                ]
            }

            async def call(name, request=None):
                result = await session.call_tool(
                    "openvsp." + name,
                    {} if request is None else {"request": request},
                    read_timeout_seconds=timedelta(seconds=1260),
                )
                assert not result.isError, result.content
                return result.structuredContent

            health = await call("health")
            assert health["status"] == "ok", health
            evidence["health"] = health
            evidence["capabilities"] = await call("query", {"kind": "capabilities"})
            assert "VSPAEROSweep" in evidence["capabilities"]["analyses"]
            cached = await call("query", {"kind": "capabilities"})
            assert cached["cache_hit"]
            evidence["analysis_schema"] = await call("query", {"kind": "analysis"})
            assert "FixedWakeFlag" in {
                item["name"] for item in evidence["analysis_schema"]["inputs"]
            }
            created = await call("create_model", {"output_dir": str(out)})
            evidence["create"] = created
            model = Path(created["geometry_path"])
            before = digest(model)
            analysis = VSPAeroSettings(
                thick_geom_set=3,
                thin_geom_set=4,
                sref=12,
                bref=10,
                cref=1.2444444444,
                xcg=3,
                length_unit="m",
            )
            request = OpenVSPRequest(
                geometry_file=str(model),
                case_name="simple_aircraft",
                output_dir=str(out),
                analysis=analysis,
            ).model_dump()
            info = await call("inspect", {"geometry_file": str(model)})
            assert len(info["geom_ids"]) == 4
            evidence["preview"] = await call("preview", request)
            for ext in ["svg", "stl"]:
                assert Path(evidence["preview"]["artifacts"][f"preview.{ext}"]).stat().st_size
            preflight = await call("preflight", request)
            assert len(preflight["preflight"]["thick_geom_ids"]) == 1
            assert len(preflight["preflight"]["thin_geom_ids"]) == 3
            evidence["preflight"] = preflight
            assert digest(model) == before

            for label, thick, thin in [("absent", 999, 4), ("empty", 19, 4), ("overlap", 0, 4)]:
                invalid = request | {
                    "case_name": label,
                    "analysis": analysis.model_dump()
                    | {"thick_geom_set": thick, "thin_geom_set": thin},
                }
                failed = await session.call_tool("openvsp.run_vspaero", {"request": invalid})
                assert failed.isError and "PREFLIGHT_ERROR:" in str(failed.content)
                run = max(out.glob(label + "-*"), key=lambda p: p.stat().st_mtime)
                assert not list(run.glob("*.polar")) and not (run / "solver.log").exists()

            editable = out / "editable_aircraft.vsp3"
            shutil.copy2(model, editable)
            await call(
                "modify",
                {
                    "geometry_file": str(editable),
                    "output_dir": str(out),
                    "case_name": "rename",
                    "set_commands": [
                        {"command": 'SetGeomName(FindGeom("Main_Wing",0),"Renamed_Wing")'}
                    ],
                },
            )
            renamed = await call("inspect", {"geometry_file": str(editable)})
            assert "Renamed_Wing" in renamed["wing_names"]
            parameters = await call(
                "query", {"kind": "parameters", "geometry_file": str(editable), "limit": 200}
            )
            length = next(
                p
                for p in parameters["parameters"]
                if p["name"] == "Length" and p["group"] == "Design"
            )
            evidence["parameters"] = await call(
                "set_parameters",
                {
                    "geometry_file": str(editable),
                    "output_dir": str(out),
                    "edits": [{"parm_id": length["id"], "value": 8.1}],
                },
            )
            assert abs(evidence["parameters"]["parameter_values"][length["id"]] - 8.1) < 1e-9
            readback = await call(
                "query",
                {"kind": "parameters", "geometry_file": str(editable), "parm_ids": [length["id"]]},
            )
            assert abs(readback["parameters"][0]["value"] - 8.1) < 1e-9
            after_edit = digest(editable)
            for edit in [
                {"parm_id": "missing", "value": 1},
                {"parm_id": length["id"], "value": length["lower"] - 1},
            ]:
                failed = await session.call_tool(
                    "openvsp.set_parameters",
                    {
                        "request": {
                            "geometry_file": str(editable),
                            "output_dir": str(out),
                            "edits": [edit],
                        }
                    },
                )
                assert failed.isError and digest(editable) == after_edit
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
            )
            assert failed.isError, "Invalid API call was reported as success"
            assert digest(editable) == after_edit

            response = OpenVSPResponse.model_validate(await call("run_vspaero", request))
            assert all(Path(p).is_file() for p in response.artifacts.values())
            # Broad workflow smoke bounds, not aerodynamic accuracy validation.
            assert 0.1 < response.coefficients["CLtot"] < 0.4
            assert 0 < response.coefficients["CDtot"] < 0.1
            assert response.numerical_quality["convergence_status"] == "not_assessed"
            assert response.numerical_quality["history_status"] == "available"
            evidence["solve"] = response.model_dump()
            assert response.effective_settings["status"] == "verified"
            evidence["saved_results"] = await call(
                "read_results",
                {
                    "manifest_file": response.manifest_path,
                    "coefficient_names": ["CLtot", "CDtot"],
                    "log": "solver",
                    "log_tail_lines": 5,
                },
            )
            assert (
                evidence["saved_results"]["coefficients"]["CLtot"] == response.coefficients["CLtot"]
            )
            evidence["fixed_wake"] = await call(
                "run_vspaero",
                request
                | {
                    "case_name": "fixed_wake",
                    "analysis": analysis.model_dump()
                    | {
                        "fixed_wake": True,
                        "forward_gmres_tolerance_factor": 0.001,
                    },
                },
            )
            assert (
                evidence["fixed_wake"]["effective_settings"]["solver_file_values"]["WakeIters"] == 0
            )
            assert (
                abs(
                    evidence["fixed_wake"]["effective_settings"]["solver_file_values"][
                        "ForwardGMRESConvergenceFactor"
                    ]
                    - 0.001
                )
                < 1e-12
            )
            sweep = await call(
                "sweep",
                {
                    "geometry_file": str(model),
                    "output_dir": str(out),
                    "timeout_seconds": 1200,
                    "conditions": [analysis.model_dump() | {"alpha": a} for a in [0, 3]],
                },
            )
            assert sweep["status"] == "success" and len(sweep["results"]) == 2
            for point, alpha in zip(sweep["results"], [0, 3]):
                assert point["coefficients"]["AoA"] == alpha
                assert point["numerical_quality"]["convergence_status"] == "not_assessed"
            evidence["sweep"] = sweep
    assert digest(model) == before
    (out / "smoke_result.json").write_text(json.dumps(evidence, indent=2) + "\n")
    print(
        json.dumps(
            {
                "status": "PASS",
                "transport": "MCP stdio",
                "version": __version__,
                "tools": sorted(names),
                "checks": [
                    "source preserved",
                    "modify saved",
                    "API error rejected",
                    "native capability cache and analysis inputs",
                    "parameter query, edit/readback and rejected invalid edits",
                    "absent/empty/overlapping sets rejected before solver",
                    "VSPAERO solve",
                    "fixed wake and GMRES solver input verified",
                    "saved result read without solver rerun",
                    "two-point sweep",
                ],
                "CL": response.coefficients["CLtot"],
                "CD": response.coefficients["CDtot"],
                "evidence": str(out / "smoke_result.json"),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
