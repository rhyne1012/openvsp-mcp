"""Batch isolation, resource limits, durable recovery and real transport cancellation."""

import asyncio
import csv
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from pydantic import ValidationError

from openvsp_mcp import batch, runtime
from openvsp_mcp.fastapi_app import create_app
from openvsp_mcp.models import (
    BatchCancelRequest,
    BatchExportRequest,
    BatchRequest,
    BatchResumeRequest,
    BatchStatusRequest,
    OpenVSPResponse,
)

MODEL = "<Vsp_Geometry><Vehicle><Geom><ParmContainer><ID>w</ID><Name>Wing</Name></ParmContainer><GeomBase><TypeName>Wing</TypeName></GeomBase></Geom></Vehicle></Vsp_Geometry>"


def wait_done(directory):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        result = batch.batch_status(BatchStatusRequest(batch_directory=directory))
        if not result["active"]:
            return result
        time.sleep(0.02)
    pytest.fail("Batch did not finish")


@pytest.fixture
def runner(monkeypatch, tmp_path):
    source = tmp_path / "model.vsp3"
    source.write_text(MODEL)
    monkeypatch.setattr(batch, "_identity", lambda: {"test_identity": 1})
    pool = runtime.CpuPool(4)
    monkeypatch.setattr(runtime, "cpu_pool", pool)
    calls, failed = [], set()
    delay = [0.08]

    def solve(request, **kwargs):
        calls.append((request.case_name, request.analysis.alpha, request.parameter_edits))
        until = time.monotonic() + delay[0]
        while time.monotonic() < until:
            runtime.check_cancelled()
            time.sleep(0.005)
        if request.case_name in failed:
            raise RuntimeError("intentional failure")
        run = Path(tempfile.mkdtemp(prefix=request.case_name, dir=tmp_path))
        # The stub still writes only inside this batch's output tree.
        run.rmdir()
        parent = Path(request.output_dir)
        parent.mkdir(exist_ok=True)
        run = Path(tempfile.mkdtemp(prefix=request.case_name, dir=parent))
        artifact = run / "manifest.json"
        artifact.write_text('{"status":"success"}')
        return OpenVSPResponse(
            script_path=str(run / "script"),
            geometry_path=request.geometry_file,
            run_directory=str(run),
            log_path=str(run / "log"),
            manifest_path=str(artifact),
            artifacts={"manifest.json": str(artifact)},
            coefficients={"CLtot": request.analysis.alpha * 0.1},
            versions={"test": "1"},
            numerical_quality={"convergence_status": "not_assessed"},
        )

    monkeypatch.setattr(batch.core, "execute_openvsp", solve)

    def submit(count=4, **kwargs):
        args = {
            "geometry_file": str(source),
            "output_dir": str(tmp_path),
            "cpu_budget": 4,
            "max_parallel_jobs": 2,
            "cases": [
                {"case_id": f"c{i}", "analysis": {"ncpu": 2, "alpha": i}} for i in range(count)
            ],
        }
        args.update(kwargs)
        return batch.submit_batch(BatchRequest(**args))["batch_directory"]

    yield submit, calls, failed, delay, source, pool
    batch.shutdown_batches()
    assert pool.used == 0


@pytest.mark.parametrize(
    "change",
    [
        {"cases": [{"case_id": "same"}, {"case_id": "same"}]},
        {"cpu_budget": 1, "cases": [{"case_id": "a", "analysis": {"ncpu": 2}}]},
        {"cases": [{"case_id": "a", "parameter_edits": [{"parm_id": "x", "value": 1}] * 2}]},
    ],
)
def test_invalid_batch_contract(change):
    with pytest.raises(ValidationError):
        BatchRequest(geometry_file="unused", **change)


def test_resource_budget_is_shared_across_batches_and_direct_work(runner, monkeypatch):
    submit, calls, _, _, source, pool = runner
    entered, release = threading.Event(), threading.Event()
    admitted = []
    solve = batch.core.execute_openvsp

    def gated_solve(request, **kwargs):
        admitted.append(request.case_name)
        entered.set()
        assert release.wait(10), "Test did not release the admitted solver"
        return solve(request, **kwargs)

    monkeypatch.setattr(batch.core, "execute_openvsp", gated_solve)
    # A direct operation reserves half the budget while two batches compete.
    # Wait for actual solver admission and hold it until the assertions finish.
    try:
        with runtime.cpu_allocation(2):
            first, second = submit(), submit()
            assert entered.wait(10), "No batch solver was admitted"
            assert pool.used == 4
            assert len(admitted) == 1
    finally:
        release.set()
    for directory in [first, second]:
        status = wait_done(directory)
        assert status["status"] == "success" and status["completed"] == 4
        assert status["peak_reserved_threads"] <= 4
    assert pool.peak == 4
    assert len(calls) == 8
    assert source.read_text() == MODEL


def test_failure_stop_continue_and_resume_reuses_success(runner):
    submit, calls, failed, _, _, _ = runner
    failed.add("c1")
    directory = submit(max_parallel_jobs=1)
    assert wait_done(directory)["counts"] == {
        "queued": 0,
        "waiting_resources": 0,
        "running": 0,
        "cancelling": 0,
        "success": 1,
        "failed": 1,
        "cancelled": 0,
        "skipped": 2,
    }
    failed.clear()
    batch.resume_batch(BatchResumeRequest(batch_directory=directory))
    assert wait_done(directory)["status"] == "success"
    assert [c[0] for c in calls].count("c0") == 1
    assert [c[0] for c in calls].count("c1") == 2
    failed.add("c1")
    other = submit(failure_policy="continue")
    result = wait_done(other)
    assert result["counts"]["failed"] == 1 and result["counts"]["success"] == 3


@pytest.mark.parametrize("target", ["source", "snapshot", "spec", "response", "artifact", "binary"])
def test_resume_rejects_changed_evidence(runner, monkeypatch, target):
    submit, _, _, _, source, _ = runner
    directory = submit(count=1)
    wait_done(directory)
    root = Path(directory)
    data = json.loads((root / "batch.json").read_text())
    if target == "source":
        source.write_text(MODEL + "\n")
    elif target == "snapshot":
        (root / "source.vsp3").write_text("modified")
    elif target == "binary":
        monkeypatch.setattr(batch, "_identity", lambda: {"test_identity": 2})
    elif target == "artifact":
        Path(data["cases"][0]["response"]["manifest_path"]).write_text("modified")
    else:
        if target == "spec":
            data["spec"]["request"]["cases"][0]["analysis"]["alpha"] = 17
        else:
            data["cases"][0]["response"]["coefficients"]["CLtot"] = 17
        (root / "batch.json").write_text(json.dumps(data))
    with pytest.raises(RuntimeError):
        batch.resume_batch(BatchResumeRequest(batch_directory=directory))


def test_cancel_case_cancel_batch_and_explicit_retry(runner):
    submit, _, _, delay, _, _ = runner
    delay[0] = 0.3
    directory = submit(max_parallel_jobs=1)
    batch.cancel_batch(BatchCancelRequest(batch_directory=directory, case_ids=["c0", "c2"]))
    result = wait_done(directory)
    assert result["counts"]["cancelled"] == 2 and result["counts"]["success"] == 2
    batch.resume_batch(BatchResumeRequest(batch_directory=directory, case_ids=["c2"]))
    result = wait_done(directory)
    assert result["counts"]["cancelled"] == 1 and result["counts"]["success"] == 3
    other = submit()
    batch.cancel_batch(BatchCancelRequest(batch_directory=other))
    assert wait_done(other)["status"] == "cancelled"


def test_live_resume_is_locked_and_unknown_cancel_does_not_stop_work(runner):
    submit, _, _, delay, _, _ = runner
    delay[0] = 0.3
    directory = submit()
    with pytest.raises(RuntimeError, match="active runner"):
        batch.resume_batch(BatchResumeRequest(batch_directory=directory))
    with pytest.raises(RuntimeError, match="Unknown"):
        batch.cancel_batch(BatchCancelRequest(batch_directory=directory, case_ids=["typo"]))
    assert wait_done(directory)["status"] == "success"


def test_interrupted_job_is_explicitly_resumed_and_live_pid_is_rejected(runner):
    submit, _, _, _, _, _ = runner
    directory = submit(count=1)
    wait_done(directory)
    path = Path(directory) / "batch.json"
    data = json.loads(path.read_text())
    data["status"] = "running"
    data["cases"][0].update(status="running", native_pid=os.getpid())
    path.write_text(json.dumps(data))
    assert (
        batch.batch_status(BatchStatusRequest(batch_directory=directory))["status"] == "interrupted"
    )
    with pytest.raises(RuntimeError, match="live native process"):
        batch.resume_batch(BatchResumeRequest(batch_directory=directory))
    data["cases"][0]["native_pid"] = None
    path.write_text(json.dumps(data))
    batch.resume_batch(BatchResumeRequest(batch_directory=directory))
    assert wait_done(directory)["status"] == "success"


def test_export_partial_results_metadata_and_pagination(runner):
    submit, calls, failed, _, _, _ = runner
    failed.add("c1")
    directory = submit(failure_policy="continue")
    wait_done(directory)
    assert (
        len(
            batch.batch_status(BatchStatusRequest(batch_directory=directory, offset=1, limit=2))[
                "cases"
            ]
        )
        == 2
    )
    before = len(calls)
    exported = batch.export_batch(BatchExportRequest(batch_directory=directory))
    data = json.loads(Path(exported["json_path"]).read_text())
    assert data["metadata"]["angles"] == "degrees"
    assert data["cases"][1]["status"] == "failed" and "coefficient.CLtot" not in data["cases"][1]
    with Path(exported["csv_path"]).open() as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 4 and rows[1]["coefficient.CLtot"] == ""
    assert len(calls) == before


def test_changed_snapshot_stops_new_cases(runner):
    submit, calls, _, delay, _, _ = runner
    delay[0] = 0.2
    directory = submit(count=3, max_parallel_jobs=1)
    deadline = time.monotonic() + 3
    while not calls and time.monotonic() < deadline:
        time.sleep(0.01)
    assert len(calls) == 1
    (Path(directory) / "source.vsp3").write_text("changed")
    status = wait_done(directory)
    assert status["counts"]["success"] == 1
    assert status["counts"]["failed"] == 1 and status["counts"]["skipped"] == 1
    assert len(calls) == 1


def test_control_requests_bypass_occupied_legacy_slots():
    release, entered = threading.Event(), threading.Event()
    count = 0
    lock = threading.Lock()

    def occupied():
        nonlocal count
        with lock:
            count += 1
            if count == 2:
                entered.set()
        assert release.wait(3)

    async def run():
        tasks = [asyncio.create_task(runtime.run_async(occupied)) for _ in range(2)]
        try:
            while not entered.is_set():
                await asyncio.sleep(0.01)
            assert (
                await asyncio.wait_for(runtime.run_control(lambda: "responsive"), 1) == "responsive"
            )
        finally:
            release.set()
            await asyncio.gather(*tasks)

    asyncio.run(run())


def test_resource_wait_honors_total_deadline(monkeypatch):
    pool = runtime.CpuPool(1)
    monkeypatch.setattr(runtime, "cpu_pool", pool)
    with (
        pool.reserve(1),
        pytest.raises(RuntimeError, match="total time budget"),
        runtime.deadline_scope(time.monotonic() + 0.05),
        runtime.cpu_allocation(1),
    ):
        pytest.fail("A second allocation exceeded the budget")


def test_moved_batch_rejected(runner, tmp_path):
    submit, _, _, _, _, _ = runner
    directory = submit(count=1)
    wait_done(directory)
    target = tmp_path / "moved"
    Path(directory).rename(target)
    with pytest.raises(RuntimeError, match="directory moved"):
        batch.batch_status(BatchStatusRequest(batch_directory=str(target)))


def test_resume_finalizes_interrupted_job_with_all_cases_saved(runner):
    submit, calls, _, _, _, _ = runner
    directory = submit(count=1)
    wait_done(directory)
    path = Path(directory) / "batch.json"
    data = json.loads(path.read_text())
    data["status"] = "running"
    path.write_text(json.dumps(data))
    result = batch.resume_batch(BatchResumeRequest(batch_directory=directory))
    assert result["status"] == "success" and not result["active"]
    assert len(calls) == 1


def test_completion_during_status_read_is_not_reported_as_interruption(runner, monkeypatch):
    submit, _, _, delay, _, _ = runner
    delay[0] = 0.2
    directory = submit(count=1)
    load = batch._load

    def slow_read(root):
        snapshot = load(root)
        if snapshot["status"] == "running":
            deadline = time.monotonic() + 3
            while load(root)["status"] == "running" and time.monotonic() < deadline:
                time.sleep(0.01)
            assert load(root)["status"] == "success"
        return snapshot

    # Simulate a read overlapping final manifest replacement and lock release.
    monkeypatch.setattr(batch, "_load", slow_read)
    status = batch.batch_status(BatchStatusRequest(batch_directory=directory))
    assert status["status"] != "interrupted"
    assert wait_done(directory)["status"] == "success"


@pytest.mark.parametrize(
    "contents,expected",
    [
        ("VSPAERO v.7.2.2 --- Compiled\nNumberOfThreads_: 4\n", ("7.2.2", 4)),
        ("Single threaded build.\n", (None, 1)),
        ("Unknown solver log layout\n", (None, None)),
    ],
)
def test_observed_solver_runtime(tmp_path, contents, expected):
    path = tmp_path / "solver.log"
    path.write_text(contents)
    assert batch.core._solver_runtime(path) == expected


def test_http_batch_routes_and_lifecycle(runner):
    _, _, _, delay, source, _ = runner
    delay[0] = 0.3
    with TestClient(create_app()) as client:
        response = client.post(
            "/vsp/batch/submit", json={"geometry_file": str(source), "cases": [{"case_id": "a"}]}
        )
        assert response.status_code == 200
        request = {"batch_directory": response.json()["batch_directory"]}
        assert client.post("/vsp/batch/status", json=request).status_code == 200
        assert (
            client.post("/vsp/batch/cancel", json=request | {"case_ids": ["typo"]}).status_code
            == 400
        )
    assert wait_done(request["batch_directory"])["status"] == "cancelled"


@pytest.mark.skipif(os.name != "posix", reason="POSIX native process cleanup")
def test_stdio_batch_cancel_restart_resume_and_shutdown(tmp_path):
    binary = tmp_path / "fake-openvsp"
    binary.write_text(f"#!{sys.executable}\nimport time\ntime.sleep(60)\n")
    binary.chmod(0o755)
    model = tmp_path / "source.vsp3"
    model.write_text(MODEL)
    env = dict(os.environ, OPENVSP_BIN=str(binary), VSPAERO_BIN=str(binary), OPENVSP_CPU_BUDGET="4")

    async def run():
        server = StdioServerParameters(command=sys.executable, args=["-m", "openvsp_mcp"], env=env)
        async with stdio_client(server) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(
                "openvsp.batch_submit",
                {
                    "request": {
                        "geometry_file": str(model),
                        "output_dir": str(tmp_path),
                        "cases": [{"case_id": "one"}, {"case_id": "two"}],
                    }
                },
            )
            assert not result.isError, result.content
            directory = result.structuredContent["batch_directory"]
            manifest = Path(directory) / "batch.json"
            for _ in range(100):
                data = json.loads(manifest.read_text())
                pid = data["cases"][0].get("native_pid")
                if pid:
                    break
                await asyncio.sleep(0.02)
            assert pid
            await asyncio.wait_for(session.send_ping(), timeout=1)
            result = await asyncio.wait_for(
                session.call_tool(
                    "openvsp.batch_cancel", {"request": {"batch_directory": directory}}
                ),
                timeout=1,
            )
            assert not result.isError
            for _ in range(100):
                if json.loads(manifest.read_text())["status"] == "cancelled":
                    break
                await asyncio.sleep(0.02)
            assert json.loads(manifest.read_text())["status"] == "cancelled"
            with pytest.raises(ProcessLookupError):
                os.kill(pid, 0)
        # A fresh server can read and resume the durable batch without changing its spec.
        async with stdio_client(server) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(
                "openvsp.batch_resume", {"request": {"batch_directory": directory}}
            )
            assert not result.isError, result.content
            for _ in range(100):
                pid = json.loads(manifest.read_text())["cases"][0].get("native_pid")
                if pid:
                    break
                await asyncio.sleep(0.02)
            assert pid
        # Closing stdio triggers lifecycle cancellation and native cleanup.
        assert json.loads(manifest.read_text())["status"] == "cancelled"
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)

    asyncio.run(run())
