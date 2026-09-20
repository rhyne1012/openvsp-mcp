"""Measure MCP latency and responsiveness; no aerodynamic performance claims."""

import argparse
import asyncio
import json
import math
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def summary(values):
    ordered = sorted(values)
    return {
        "samples_seconds": values,
        "median_seconds": statistics.median(values),
        "p95_seconds": ordered[math.ceil(0.95 * len(ordered)) - 1],
    }


async def measure(source, samples, tmp):
    env = dict(os.environ, PYTHONPATH=str(source / "src"))
    data = {"connect_and_list": [], "inspect": [], "health": [], "query_cached": []}
    model = tmp / "metadata.vsp3"
    model.write_text(
        "<Vsp_Geometry><Vehicle><Geom><ParmContainer><ID>x</ID><Name>Wing</Name></ParmContainer><GeomBase><TypeName>Wing</TypeName></GeomBase></Geom></Vehicle></Vsp_Geometry>"
    )
    for _ in range(samples):
        started = time.perf_counter()
        with (tmp / "stderr.log").open("w") as log:
            async with (
                stdio_client(
                    StdioServerParameters(
                        command=sys.executable, args=["-m", "openvsp_mcp"], env=env
                    ),
                    errlog=log,
                ) as (read, write),
                ClientSession(read, write) as session,
            ):
                identity = await session.initialize()
                names = {t.name for t in (await session.list_tools()).tools}
                data["connect_and_list"].append(time.perf_counter() - started)
                for name, payload in [
                    ("inspect", {"request": {"geometry_file": str(model)}}),
                    ("health", {}),
                ]:
                    started = time.perf_counter()
                    result = await session.call_tool("openvsp." + name, payload)
                    assert not result.isError
                    if name == "health":
                        assert result.structuredContent["status"] == "ok"
                    data[name].append(time.perf_counter() - started)
                if "openvsp.query" in names:
                    await session.call_tool("openvsp.query", {"request": {"kind": "capabilities"}})
                    started = time.perf_counter()
                    r = await session.call_tool(
                        "openvsp.query", {"request": {"kind": "capabilities"}}
                    )
                    assert not r.isError and r.structuredContent["cache_hit"]
                    data["query_cached"].append(time.perf_counter() - started)
    # Deterministic two-second stand-in isolates transport responsiveness from solver speed.
    fake = tmp / "blocking-openvsp"
    ready = tmp / "ready"
    fake.write_text(
        f"#!{sys.executable}\nfrom pathlib import Path\nimport time\nPath({str(ready)!r}).touch()\ntime.sleep(2)\n"
    )
    fake.chmod(0o755)
    env["OPENVSP_BIN"] = str(fake)
    blocked = []
    for _ in range(samples):
        ready.unlink(missing_ok=True)
        with (tmp / "stderr.log").open("w") as log:
            async with (
                stdio_client(
                    StdioServerParameters(
                        command=sys.executable, args=["-m", "openvsp_mcp"], env=env
                    ),
                    errlog=log,
                ) as (read, write),
                ClientSession(read, write) as session,
            ):
                await session.initialize()
                task = asyncio.create_task(
                    session.call_tool(
                        "openvsp.modify",
                        {"request": {"geometry_file": str(model), "output_dir": str(tmp / "runs")}},
                    )
                )
                for _ in range(250):
                    if ready.exists():
                        break
                    await asyncio.sleep(0.02)
                assert ready.exists()
                started = time.perf_counter()
                await session.send_ping()
                blocked.append(time.perf_counter() - started)
                result = await task
                assert result.isError  # Deliberately no output model/completion marker.
    return {
        "version": identity.serverInfo.version,
        "latencies": {k: summary(v) for k, v in data.items() if v},
        "ping_during_simulated_2s_native_operation": summary(blocked),
    }


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=5)
    args = parser.parse_args()
    if not 1 <= args.samples <= 100:
        parser.error("samples must be 1–100")
    with tempfile.TemporaryDirectory(prefix="openvsp-benchmark-") as tmp:
        result = {
            "scope": "Same host/interpreter/SDK; real health probes; blocking workload is simulated. No solver acceleration, accuracy or memory claim.",
            "baseline": await measure(args.baseline_source, args.samples, Path(tmp)),
            "candidate": await measure(
                Path(__file__).resolve().parents[1], args.samples, Path(tmp)
            ),
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(
        json.dumps(
            {
                k: {m: v["median_seconds"] for m, v in r["latencies"].items()}
                | {
                    "ping_during_work": r["ping_during_simulated_2s_native_operation"][
                        "median_seconds"
                    ]
                }
                for k, r in result.items()
                if isinstance(r, dict)
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
