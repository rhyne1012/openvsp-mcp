"""Native metadata probes share solver admission without blocking cached reads."""

import json
import os
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import pytest

from openvsp_mcp import core, health, query, runtime
from openvsp_mcp.models import QueryRequest


class NotifyingCpuPool(runtime.CpuPool):
    def __init__(self):
        super().__init__(1)
        self.attempted = threading.Event()
        self.reservations = []

    @contextmanager
    def reserve(self, count):
        self.reservations.append(count)
        self.attempted.set()
        with super().reserve(count):
            yield


@pytest.fixture
def probes(monkeypatch, tmp_path):
    pool = NotifyingCpuPool()
    calls = []
    binary = tmp_path / "openvsp"
    binary.write_text("first binary")
    monkeypatch.setattr(runtime, "cpu_pool", pool)
    monkeypatch.setattr(health, "cpu_pool", pool)
    monkeypatch.setattr(core, "OPENVSP_BIN", str(binary))
    monkeypatch.setattr(core, "VSPAERO_BIN", str(tmp_path / "vspaero"))
    monkeypatch.setattr(health, "_executable", lambda value: value)
    monkeypatch.setattr(health, "native_identity", lambda *args, **kwargs: {})
    monkeypatch.setattr(health, "validation_metadata", lambda *args: {})
    monkeypatch.setattr(query, "_cache", {})

    def native(script, log, timeout):
        assert pool.used == 1
        calls.append(script.name)
        if script.name == "probe.vspscript":
            log.write_text("OpenVSP 3.55.0\nOPENVSP_HEALTH_API_OK\n")
        else:
            token = script.read_text().split('Print("MCP_QUERY_')[1].split(":")[0]
            log.write_text(
                "MCP_QUERY_"
                + token
                + ":"
                + json.dumps({"openvsp_version": "3.55.0", "analyses": ["VSPAEROSweep"]})
            )
        return 0

    def version(*args, **kwargs):
        assert pool.used == 1
        assert 0 < kwargs["timeout"] <= 10
        calls.append("vspaero-version")
        return subprocess.CompletedProcess([], 1, "VSPAERO 7.3.1\n", "")

    monkeypatch.setattr(core, "_run_script", native)
    monkeypatch.setattr(health.subprocess, "run", version)
    return pool, calls, binary


@pytest.fixture(params=["health", "query"])
def probe(request):
    if request.param == "health":
        return health.health_check
    return lambda: query.query_model(QueryRequest())


def test_probes_wait_for_solver_cpu_budget(probes, probe):
    pool, calls, _ = probes
    with ThreadPoolExecutor(max_workers=1) as executor:
        with pool.reserve(1):
            pool.attempted.clear()
            future = executor.submit(probe)
            assert pool.attempted.wait(1)
            assert calls == []
            assert not future.done()
        future.result(timeout=2)
    assert calls
    assert pool.peak == 1
    assert pool.used == 0


def test_cpu_queue_cancellation_does_not_launch_or_leak(probes, probe):
    pool, calls, _ = probes
    cancelled = threading.Event()

    def run():
        with runtime.cancellation_scope(cancelled):
            return probe()

    with ThreadPoolExecutor(max_workers=1) as executor, pool.reserve(1):
        pool.attempted.clear()
        future = executor.submit(run)
        assert pool.attempted.wait(1)
        cancelled.set()
        with pytest.raises(runtime.OperationCancelled):
            future.result(timeout=2)
        assert calls == []
        assert pool.used == 1
    assert pool.used == 0


def test_existing_cpu_lease_is_reused(probes, probe):
    pool, calls, _ = probes
    with runtime.cpu_allocation(1), runtime.deadline_scope(time.monotonic() + 1):
        probe()
        assert pool.used == 1
    assert calls
    assert pool.reservations == [1]
    assert pool.used == 0


def test_query_deadline_includes_cpu_queue(probes):
    pool, calls, _ = probes
    with ThreadPoolExecutor(max_workers=1) as executor, pool.reserve(1):
        future = executor.submit(query.query_model, QueryRequest(timeout_seconds=1))
        with pytest.raises(RuntimeError, match="total time budget"):
            future.result(timeout=3)
        assert calls == []
    assert pool.used == 0


@pytest.mark.parametrize("name,budget", [("openvsp", 15), ("vspaero", 10)])
def test_health_deadlines_include_cpu_queue(monkeypatch, probes, name, budget):
    pool, calls, _ = probes
    monotonic = time.monotonic
    elapsed = [0]
    monkeypatch.setattr(health.time, "monotonic", lambda: monotonic() + elapsed[0])
    selected = core.OPENVSP_BIN if name == "openvsp" else core.VSPAERO_BIN

    def executable(value):
        if value != selected:
            raise RuntimeError("Other binary unavailable")
        return value

    monkeypatch.setattr(health, "_executable", executable)
    with ThreadPoolExecutor(max_workers=1) as executor, pool.reserve(1):
        pool.attempted.clear()
        future = executor.submit(health.health_check)
        assert pool.attempted.wait(1)
        elapsed[0] = budget + 1
        with pool.condition:
            pool.condition.notify_all()
        result = future.result(timeout=2)
        assert "total time budget" in result["checks"][name]["error"]
        assert calls == []
    assert pool.used == 0


def test_cached_capabilities_need_no_cpu_lease(probes):
    pool, calls, _ = probes
    first = query.query_model(QueryRequest())
    assert not first["cache_hit"]
    with pool.reserve(1), runtime.deadline_scope(time.monotonic() + 1):
        reservations = list(pool.reservations)
        cached = query.query_model(QueryRequest())
        assert cached["cache_hit"]
        assert cached["analyses"] == first["analyses"]
        assert pool.reservations == reservations
    assert len(calls) == 1


def test_capability_cache_invalidates_same_size_binary_rewrite(probes):
    _, calls, binary = probes
    query.query_model(QueryRequest())
    before = binary.stat()
    binary.write_text("other binary")
    os.utime(binary, ns=(before.st_atime_ns, before.st_mtime_ns))
    after = binary.stat()
    assert after.st_size == before.st_size
    assert after.st_mtime_ns == before.st_mtime_ns
    assert after.st_ino == before.st_ino
    assert after.st_ctime_ns != before.st_ctime_ns
    assert not query.query_model(QueryRequest())["cache_hit"]
    assert len(calls) == 2


def test_health_preserves_cancellation_after_version_probe(monkeypatch, probes):
    pool, _, _ = probes
    cancelled = threading.Event()

    def version(*args, **kwargs):
        cancelled.set()
        return subprocess.CompletedProcess([], 1, "VSPAERO 7.3.1\n", "")

    monkeypatch.setattr(health.subprocess, "run", version)
    with runtime.cancellation_scope(cancelled), pytest.raises(runtime.OperationCancelled):
        health.health_check()
    assert pool.used == 0
