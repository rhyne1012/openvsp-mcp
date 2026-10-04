"""Compare legacy/compact batch metadata on the same synthetic completed cases.

Measures warm polling and atomic index rewrites, not submission, native solves,
detail creation, export, recovery, or total batch throughput.
"""

import argparse
import json
import statistics
import tempfile
import time
from pathlib import Path

from openvsp_mcp.batch import _load
from openvsp_mcp.models import BatchRequest
from openvsp_mcp.storage import atomic_json, compact_index, fingerprint


def measure(function, samples):
    values = []
    for _ in range(samples):
        started = time.perf_counter()
        function()
        values.append(1000 * (time.perf_counter() - started))
    return {"median_ms": statistics.median(values), "samples_ms": values}


def benchmark(cases=1000, samples=7):
    with tempfile.TemporaryDirectory(prefix="openvsp-metadata-") as temporary:
        root = Path(temporary).resolve()
        request = BatchRequest(
            geometry_file="/benchmark/public.vsp3",
            cases=[{"case_id": f"case{i}", "analysis": {"alpha": 3}} for i in range(cases)],
        )
        spec = {"request": request.model_dump(), "identity": {}, "source_sha256": "0" * 64}
        data = {
            "schema_version": 1,
            "batch_directory": str(root),
            "spec": spec,
            "fingerprint": fingerprint(spec),
            "cases": [
                {
                    "case_id": f"case{i}",
                    "status": "success",
                    "attempts": [{"status": "success"}],
                    "response": {
                        "coefficients": {f"C{j}": 0.01 * j for j in range(40)},
                        "parameter_values": {f"parm{j}": j for j in range(100)},
                        "timings": {"total_seconds": 5.0},
                    },
                    "artifact_hashes": {f"runs/case{i}/artifact{j}": "a" * 64 for j in range(15)},
                    "response_sha256": "b" * 64,
                    "detail_file": f"cases/case{i}/record.json",
                    "detail_sha256": "c" * 64,
                }
                for i in range(cases)
            ],
        }
        path = root / "batch.json"
        atomic_json(path, data)
        legacy_size = path.stat().st_size
        legacy_read = measure(lambda: _load(root), samples)
        legacy_write = measure(lambda: atomic_json(path, data), samples)
        data["schema_version"] = 2
        atomic_json(root / "spec.json", spec)
        atomic_json(path, compact_index(data))
        compact_size = path.stat().st_size
        _load(root)  # Warm the immutable specification cache explicitly.
        compact_read = measure(lambda: _load(root), samples)
        compact_write = measure(lambda: atomic_json(path, compact_index(data)), samples)
    return {
        "scope": __doc__,
        "cases": cases,
        "samples": samples,
        "synthetic_per_case": {"coefficients": 40, "parameter_values": 100, "artifact_hashes": 15},
        "legacy": {"index_bytes": legacy_size, "poll": legacy_read, "rewrite": legacy_write},
        "compact": {"index_bytes": compact_size, "poll": compact_read, "rewrite": compact_write},
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = benchmark()
    Path(args.output).write_text(json.dumps(result, indent=2) + "\n")
    print(
        json.dumps(
            {
                name: {key: value for key, value in result[name].items()}
                for name in ["legacy", "compact"]
            },
            indent=2,
        )
    )
