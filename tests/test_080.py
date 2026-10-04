"""Regression coverage for isolated admission, provenance and undefined ratios."""

import asyncio
import json
import math
import os
import threading

import anyio
import pytest

from openvsp_mcp import native, runtime
from openvsp_mcp.models import ResultRequest
from openvsp_mcp.numeric import finite_row
from openvsp_mcp.quality import history_diagnostics
from openvsp_mcp.results import read_results


@pytest.mark.parametrize(
    "ratio,denominator", [("L/D", "CDtot"), ("E", "CDi"), ("LoDw", "CDwtot"), ("Ew", "CDiw")]
)
@pytest.mark.parametrize("invalid", [math.nan, math.inf, -math.inf])
def test_only_undefined_named_ratios_are_omitted(ratio, denominator, invalid):
    row, unavailable = finite_row(["CLtot", denominator, ratio], [0, 0, invalid])
    assert row == {"CLtot": 0, denominator: 0}
    assert denominator in unavailable[ratio]
    for columns, values in [
        (["CLtot", denominator, ratio], [0, 0.01, invalid]),
        (["CLtot", ratio], [0, invalid]),
        (["CLtot", denominator, ratio], [invalid, 0, invalid]),
        (["Mach", denominator, ratio], [invalid, 0, invalid]),
    ]:
        with pytest.raises(ValueError, match="Non-finite"):
            finite_row(columns, values)


def test_history_retains_valid_forces_when_efficiency_is_undefined(tmp_path):
    path = tmp_path / "case.history"
    path.write_text(
        "Iter Mach AoA Beta CLtot CDtot CMytot CDi E\n1 .1 0 0 0 .01 0 0 nan\n2 .1 0 0 0 .01 0 0 nan\n"
    )
    quality = history_diagnostics(path)
    assert quality["history_status"] == "available"
    assert quality["unavailable_history_fields"]["E"]["line_numbers"] == [2, 3]
    path.write_text(path.read_text().replace("0 .01 0 0 nan", "nan .01 0 0 nan"))
    assert history_diagnostics(path)["history_status"] == "invalid"


def test_saved_result_distinguishes_unknown_and_unavailable(tmp_path):
    path = tmp_path / "manifest.json"
    data = {
        "status": "success",
        "operation": "run_vspaero",
        "coefficients": {"CLtot": 0},
        "numerical_quality": {"unavailable_coefficients": {"E": "CDi is zero"}},
    }
    path.write_text(json.dumps(data))
    result = read_results(ResultRequest(manifest_file=str(path), coefficient_names=["CLtot", "E"]))
    assert result["coefficients"] == {"CLtot": 0}
    assert result["unavailable_coefficients"] == {"E": "CDi is zero"}
    with pytest.raises(RuntimeError, match="Unknown"):
        read_results(ResultRequest(manifest_file=str(path), coefficient_names=["typo"]))
    data["numerical_quality"]["unavailable_coefficients"] = ["E"]
    path.write_text(json.dumps(data))
    with pytest.raises(RuntimeError, match="Invalid saved unavailable"):
        read_results(ResultRequest(manifest_file=str(path)))


def test_binary_hash_cache_invalidates_on_rewrite_and_refresh(tmp_path, monkeypatch):
    path = tmp_path / "binary"
    path.write_bytes(b"first")
    digest, calls = native.digest_file, []

    def tracked(value):
        calls.append(value)
        return digest(value)

    monkeypatch.setattr(native, "digest_file", tracked)
    first = native.executable_identity(str(path))
    assert native.executable_identity(str(path)) == first
    assert len(calls) == 1
    stamp = path.stat()
    path.write_bytes(b"other")
    os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    assert native.executable_identity(str(path)) != first
    assert len(calls) == 2
    native.refresh_identities()
    native.executable_identity(str(path))
    assert len(calls) == 3


def test_queued_native_calls_do_not_starve_read_control_or_cancel():
    async def scenario():
        release = threading.Event()
        entered = []

        def blocked():
            entered.append(runtime.worker_queue_seconds())
            assert release.wait(5)

        tasks = [asyncio.create_task(runtime.run_async(blocked)) for _ in range(48)]
        try:
            with anyio.fail_after(2):
                while len(entered) < 2:
                    await anyio.sleep(0.001)
                assert await runtime.run_read(lambda: "read") == "read"
                assert await runtime.run_control(lambda: "control") == "control"
                tasks[-1].cancel()
                with pytest.raises(asyncio.CancelledError):
                    await tasks[-1]
            assert len(entered) == 2
        finally:
            release.set()
            await asyncio.gather(*tasks, return_exceptions=True)
        assert len(entered) == 47
        assert all(seconds >= 0 for seconds in entered)

    asyncio.run(scenario())


def test_queue_time_is_reported_after_admission():
    async def scenario():
        release = threading.Event()
        entered = []

        def block():
            entered.append(True)
            release.wait(5)

        tasks = [asyncio.create_task(runtime.run_async(block)) for _ in range(2)]
        try:
            with anyio.fail_after(2):
                while len(entered) < 2:
                    await anyio.sleep(0.001)
            queued = asyncio.create_task(runtime.run_async(runtime.worker_queue_seconds))
            await anyio.sleep(0.05)
            assert not queued.done()
            release.set()
            assert await queued >= 0.04
        finally:
            release.set()
            await asyncio.gather(*tasks)

    asyncio.run(scenario())
