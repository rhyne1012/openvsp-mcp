"""Evidence retention and admission semantics shared by geometry-only operations."""

import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from openvsp_mcp import operations, runtime
from openvsp_mcp.operations import GeometryOperationRequest, NativeOperationError, run_operation


@pytest.fixture
def runner(tmp_path, monkeypatch):
    source = tmp_path / "model.vsp3"
    source.write_text("<Vsp_Geometry><Vehicle/></Vsp_Geometry>")
    original = source.read_bytes()
    monkeypatch.setattr(operations, "executable_identity", lambda _: {"sha256": "test"})
    request = GeometryOperationRequest(geometry_file=str(source))

    def build(snapshot, run, token):
        assert snapshot.read_bytes() == original
        return token

    def native(script, log, timeout):
        assert timeout > 0
        log.write_text("MCP_OPERATION_" + script.read_text() + ':{"openvsp_version":"test"}\n')
        return 0

    monkeypatch.setattr(operations.core, "_run_script", native)
    return request, build, source, original


def test_success_retains_snapshot_and_evidence(runner):
    request, build, source, original = runner
    result = run_operation(request, "test", build, lambda run, payload: {"measurement": 12.0})
    assert result["status"] == "ok"
    assert result["measurement"] == 12
    assert source.read_bytes() == original
    assert Path(result["run_directory"], "input/source.vsp3").read_bytes() == original
    saved = json.loads(Path(result["manifest_path"]).read_text())
    assert saved == result
    assert saved["versions"]["openvsp_version"] == "test"
    assert saved["input_sha256"]


@pytest.mark.parametrize("output", ["", "{}\n{}", '{"area":NaN}', '{"area":1e999}', "[]"])
def test_reject_missing_duplicate_nonfinite_or_wrong_payload(runner, monkeypatch, output):
    request, build, source, original = runner

    def native(script, log, timeout):
        prefix = "MCP_OPERATION_" + script.read_text() + ":"
        log.write_text("\n".join(prefix + line for line in output.splitlines()))
        return 0

    monkeypatch.setattr(operations.core, "_run_script", native)
    with pytest.raises(NativeOperationError) as caught:
        run_operation(request, "test", build, lambda *_: {})
    assert caught.value.details["status"] == "error"
    assert source.read_bytes() == original
    assert Path(caught.value.details["manifest_path"]).is_file()


def test_native_errors_include_code_phase_and_paths(runner, monkeypatch):
    request, build, _, _ = runner

    def native(script, log, timeout):
        log.write_text(' MCP_NATIVE_ERROR:{"code":7,"message":"bad set","phase":"selection"}\n')
        return 3

    monkeypatch.setattr(operations.core, "_run_script", native)
    with pytest.raises(NativeOperationError, match=re.escape("selection [7]: bad set")) as caught:
        run_operation(request, "test", build, lambda *_: {})
    assert caught.value.details["native_errors"][0]["code"] == 7


def test_cpu_admission_is_included_in_deadline(runner, monkeypatch):
    request, build, _, _ = runner
    monkeypatch.setattr(runtime, "cpu_pool", runtime.CpuPool(1))
    request.timeout_seconds = 1
    called = threading.Event()
    monkeypatch.setattr(operations.core, "_run_script", lambda *_: called.set())
    with ThreadPoolExecutor(max_workers=1) as executor, runtime.cpu_allocation(1):
        future = executor.submit(run_operation, request, "test", build, lambda *_: {})
        with pytest.raises(NativeOperationError, match="total time budget"):
            future.result(timeout=3)
    assert not called.is_set()


def test_cancellation_preserves_type_and_manifest(runner):
    request, build, _, _ = runner
    event = threading.Event()

    def cancelled(snapshot, run, token):
        event.set()
        return build(snapshot, run, token)

    with runtime.cancellation_scope(event), pytest.raises(runtime.OperationCancelled):
        run_operation(request, "test", cancelled, lambda *_: {})
    path = next((Path(request.geometry_file).parent / "openvsp_runs").glob("*/manifest.json"))
    assert json.loads(path.read_text())["status"] == "cancelled"


def test_mode_and_set_are_exclusive():
    with pytest.raises(ValueError, match="either geom_set or mode_id"):
        GeometryOperationRequest(geometry_file="a.vsp3", geom_set=0, mode_id="mode")


def test_worker_admission_is_bounded_without_launching_queued_work():
    import asyncio
    import time

    async def exercise():
        release = threading.Event()
        entered = [threading.Event(), threading.Event()]
        queued_called = threading.Event()

        def block(index):
            entered[index].set()
            release.wait(5)

        jobs = [asyncio.create_task(runtime.run_async(block, index)) for index in range(2)]
        try:
            while not all(event.is_set() for event in entered):
                await asyncio.sleep(0.01)
            request = GeometryOperationRequest(geometry_file="unused.vsp3", timeout_seconds=1)
            start = time.monotonic()
            with pytest.raises(RuntimeError, match="total time budget"):
                await runtime.run_bounded(lambda _: queued_called.set(), request)
            assert time.monotonic() - start < 2
            assert not queued_called.is_set()
        finally:
            release.set()
            await asyncio.gather(*jobs)
        assert not queued_called.is_set()

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "route,body",
    [
        ("/vsp/export", {"format": "step", "length_unit": "m"}),
        ("/vsp/geometry-analysis", {"analysis": "comp_geom", "length_unit": "m"}),
    ],
)
def test_http_geometry_failure_retains_structured_evidence(tmp_path, monkeypatch, route, body):
    from fastapi.testclient import TestClient

    from openvsp_mcp.fastapi_app import create_app

    source = tmp_path / "model.vsp3"
    source.write_text("<Vsp_Geometry><Vehicle/></Vsp_Geometry>")
    monkeypatch.setattr(operations.core, "OPENVSP_BIN", str(tmp_path / "missing"))
    response = TestClient(create_app()).post(route, json={"geometry_file": str(source), **body})
    assert response.status_code == 500
    details = response.json()["detail"]
    assert details["status"] == "error"
    assert Path(details["manifest_path"]).is_file()
    assert source.read_text() == "<Vsp_Geometry><Vehicle/></Vsp_Geometry>"
