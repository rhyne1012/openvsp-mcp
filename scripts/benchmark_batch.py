"""Native multi-case throughput/RSS comparison on the bundled rectangular wing."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import platform
import statistics
import sys
import time
from importlib.resources import files
from pathlib import Path

import psutil
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from openvsp_mcp import __version__


async def benchmark(args):
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    model = output / "public_rect_wing.vsp3"
    model.write_bytes((files("openvsp_mcp.data") / "rect_wing.vsp3").read_bytes())
    configurations = [(1, 4), (2, 2), (4, 1)]
    records, reference = [], None
    server = StdioServerParameters(
        command=sys.executable,
        args=["-m", "openvsp_mcp"],
        env=dict(os.environ, OPENVSP_CPU_BUDGET="4"),
    )
    with (output / "mcp.log").open("w") as log:
        async with (
            stdio_client(server, errlog=log) as (read, write),
            ClientSession(read, write) as session,
        ):
            await session.initialize()

            async def call(name, request=None):
                result = await session.call_tool(
                    "openvsp." + name, {} if request is None else {"request": request}
                )
                if result.isError:
                    raise RuntimeError(str(result.content))
                return result.structuredContent

            health = await call("health")
            assert health["status"] == "ok", health
            for repetition in range(args.samples):
                ordered = configurations[repetition % 3 :] + configurations[: repetition % 3]
                for jobs, threads in ordered:
                    measurements, stop = [], asyncio.Event()

                    async def monitor(measurements, stop):
                        process = psutil.Process()
                        while not stop.is_set():
                            total = 0
                            for child in process.children(recursive=True):
                                try:
                                    total += child.memory_info().rss
                                except psutil.NoSuchProcess:
                                    pass
                            measurements.append(total)
                            await asyncio.sleep(0.05)

                    watcher = asyncio.create_task(monitor(measurements, stop))
                    started = time.monotonic()
                    try:
                        submitted = await call(
                            "batch_submit",
                            {
                                "geometry_file": str(model),
                                "output_dir": str(output),
                                "max_parallel_jobs": jobs,
                                "cpu_budget": 4,
                                "cases": [
                                    {
                                        "case_id": f"alpha{a}",
                                        "analysis": {"alpha": a, "ncpu": threads},
                                    }
                                    for a in [1, 2, 3, 4]
                                ],
                            },
                        )
                        directory = submitted["batch_directory"]
                        while True:
                            status = await call("batch_status", {"batch_directory": directory})
                            if not status["active"]:
                                break
                            if time.monotonic() - started > 1200:
                                await call("batch_cancel", {"batch_directory": directory})
                                raise RuntimeError("Benchmark time budget exceeded")
                            await asyncio.sleep(0.05)
                        elapsed = time.monotonic() - started
                    finally:
                        stop.set()
                        await watcher
                    assert status["status"] == "success", status
                    data = json.loads((Path(directory) / "batch.json").read_text())
                    coefficients = [r["response"]["coefficients"] for r in data["cases"]]
                    if reference is None:
                        reference = coefficients
                    max_difference = 0
                    for actual, expected in zip(coefficients, reference):
                        assert actual.keys() == expected.keys()
                        for name in actual:
                            assert math.isclose(
                                actual[name], expected[name], rel_tol=1e-6, abs_tol=1e-8
                            ), name
                            max_difference = max(max_difference, abs(actual[name] - expected[name]))
                    assert len(measurements) > 1 and max(measurements) > 0
                    for row in data["cases"]:
                        native_log = Path(row["response"]["artifacts"]["solver.log"]).read_text()
                        assert f"NumberOfThreads_: {threads} " in native_log
                    record = {
                        "parallel_jobs": jobs,
                        "threads_per_job": threads,
                        "sample": repetition + 1,
                        "wall_seconds": elapsed,
                        "peak_sampled_rss_mib": max(measurements) / 1024**2,
                        "memory_samples": len(measurements),
                        "max_abs_coefficient_difference": max_difference,
                    }
                    records.append(record)
                    print(json.dumps(record), flush=True)
    summaries = []
    for jobs, threads in configurations:
        matches = [r for r in records if r["parallel_jobs"] == jobs]
        summaries.append(
            {
                "parallel_jobs": jobs,
                "threads_per_job": threads,
                "median_wall_seconds": statistics.median(r["wall_seconds"] for r in matches),
                "median_peak_sampled_rss_mib": statistics.median(
                    r["peak_sampled_rss_mib"] for r in matches
                ),
            }
        )
    result = {
        "package_version": __version__,
        "platform": platform.system(),
        "architecture": platform.machine(),
        "python": platform.python_version(),
        "mcp_sdk": health["mcp_sdk_version"],
        "openvsp_version": health["checks"]["openvsp"]["version"],
        "vspaero_version": health["checks"]["vspaero"]["version"],
        "model": "bundled public rectangular wing",
        "cases_per_sample": 4,
        "cpu_budget": 4,
        "scope": "End-to-end native batch throughput. RSS sums server/descendant processes every 50 ms; shared pages can be double-counted and short peaks missed. No private inputs. No universal speedup or aerodynamic accuracy claim.",
        "coefficient_tolerance": {"relative": 1e-6, "absolute": 1e-8},
        "samples": records,
        "summary": summaries,
    }
    (output / "benchmark.json").write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--samples", type=int, default=3)
    options = parser.parse_args()
    if not 1 <= options.samples <= 10:
        parser.error("--samples must be between 1 and 10")
    asyncio.run(benchmark(options))
