"""Durable, explicitly resumed batches of independent steady VSPAERO solves."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import tempfile
import threading
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from shutil import copy2, which

import anyio

from . import core, runtime
from .describe import describe_geometry
from .models import (
    BatchCancelRequest,
    BatchExportRequest,
    BatchRequest,
    BatchResumeRequest,
    BatchStatusRequest,
    OpenVSPRequest,
)
from .version import version_info

_jobs = {}
_jobs_lock = threading.RLock()
_ACTIVE = {"queued", "waiting_resources", "running", "cancelling"}
_TERMINAL = {"success", "failed", "cancelled", "skipped"}


def _now():
    return datetime.now(timezone.utc).isoformat()


def _digest(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def _nonfinite(value):
    raise ValueError(f"Non-finite manifest value: {value}")


def _identity():
    result = version_info()
    for label, configured in [("openvsp", core.OPENVSP_BIN), ("vspaero", core.VSPAERO_BIN)]:
        path = Path(which(configured) or configured).resolve()
        result[label] = {"path": str(path), "sha256": _digest(path)}
    return result


def _write(path, data):
    fd, temporary = tempfile.mkstemp(prefix=".batch-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


class _FileLock:
    """OS ownership lock; released after crashes, never removed while readers exist."""

    def __init__(self, directory):
        self.stream = (directory / ".batch.lock").open("a+b")
        try:
            if os.name == "posix":
                import fcntl

                fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            else:
                import msvcrt

                if self.stream.seek(0, 2) == 0:
                    self.stream.write(b"0")
                    self.stream.flush()
                self.stream.seek(0)
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            self.stream.close()
            raise RuntimeError("Batch is already owned by another active runner or reader") from exc

    def close(self):
        self.stream.close()


def _owned(directory):
    try:
        lock = _FileLock(directory)
    except RuntimeError:
        return True
    lock.close()
    return False


def _load(directory):
    try:
        with (directory / "batch.json").open("rb") as stream:
            raw = stream.read(64 * 1024 * 1024 + 1)
        if len(raw) > 64 * 1024 * 1024:
            raise ValueError("Batch manifest exceeds 64 MiB")
        data = json.loads(raw, parse_constant=_nonfinite)
        if data.get("batch_directory") != str(directory):
            raise ValueError("Batch directory moved; restore its original absolute location")
        if data["schema_version"] != 1 or data["fingerprint"] != _fingerprint(data["spec"]):
            raise ValueError("Batch schema or specification fingerprint mismatch")
        spec = BatchRequest.model_validate(data["spec"]["request"])
        if [r["case_id"] for r in data["cases"]] != [c.case_id for c in spec.cases]:
            raise ValueError("Batch case IDs do not match the saved specification")
        if any(r["status"] not in _ACTIVE | _TERMINAL for r in data["cases"]):
            raise ValueError("Invalid case state")
        return data
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise RuntimeError(f"Cannot read batch manifest: {exc}") from exc


def _validate_resume(directory, data):
    spec = data["spec"]
    try:
        source = Path(spec["request"]["geometry_file"])
        if _digest(source) != spec["source_sha256"]:
            raise RuntimeError("Original model changed; submit a new batch")
        if _digest(directory / "source.vsp3") != spec["source_sha256"]:
            raise RuntimeError("Batch model snapshot changed; submit a new batch")
        if _identity() != spec["identity"]:
            raise RuntimeError("Package or native executable identity changed; submit a new batch")
        for row in data["cases"]:
            pid = row.get("native_pid")
            if pid:
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    pass
                else:
                    raise RuntimeError(
                        f"Case {row['case_id']} may still have a live native process ({pid}); "
                        "confirm it has stopped before resuming"
                    )
            if row["status"] == "success":
                if not row.get("artifact_hashes") or not row.get("response"):
                    raise RuntimeError("Successful case is missing verification evidence")
                if row.get("response_sha256") != _fingerprint(row["response"]):
                    raise RuntimeError("Saved case response changed; refusing to reuse it")
                for relative, expected in row["artifact_hashes"].items():
                    artifact = (directory / relative).resolve()
                    if not artifact.is_relative_to(directory) or _digest(artifact) != expected:
                        raise RuntimeError(f"Saved artifact changed: {relative}")
    except OSError as exc:
        raise RuntimeError(f"Cannot verify batch inputs/results: {exc}") from exc


class _Job:
    def __init__(self, directory, data, ownership):
        self.directory, self.data, self.ownership = directory, data, ownership
        self.request = BatchRequest.model_validate(data["spec"]["request"])
        self.lock = threading.RLock()
        self.events = {c.case_id: threading.Event() for c in self.request.cases}
        self.workers = {}
        self.stop = None
        self.thread = threading.Thread(target=self._run, daemon=True, name="openvsp-batch")

    def save(self):
        self.data["updated_at"] = _now()
        _write(self.directory / "batch.json", self.data)

    def cancel(self, ids):
        with self.lock:
            if not ids:
                self.stop = "cancelled"
            for row in self.data["cases"]:
                if (not ids or row["case_id"] in ids) and row["status"] in _ACTIVE:
                    self.events[row["case_id"]].set()
                    row["status"] = "cancelling" if row["case_id"] in self.workers else "cancelled"
            self.save()

    def _case(self, index):
        case = self.request.cases[index]
        row = self.data["cases"][index]
        started = time.monotonic()

        def observe(pid):
            with self.lock:
                row["native_pid"] = pid
                self.save()

        try:
            with runtime.cancellation_scope(self.events[case.case_id], observe):
                with runtime.cpu_allocation(case.analysis.ncpu):
                    if (
                        _digest(self.directory / "source.vsp3")
                        != self.data["spec"]["source_sha256"]
                    ):
                        raise RuntimeError("Batch snapshot changed before case execution")
                    if _identity() != self.data["spec"]["identity"]:
                        raise RuntimeError(
                            "Executable/package identity changed during batch execution"
                        )
                    with self.lock:
                        row.update(status="running", started_at=_now())
                        self.save()
                    response = core.execute_openvsp(
                        OpenVSPRequest(
                            geometry_file=str(self.directory / "source.vsp3"),
                            output_dir=str(self.directory / "runs"),
                            case_name=case.case_id,
                            analysis=case.analysis,
                            parameter_edits=case.parameter_edits,
                            timeout_seconds=case.timeout_seconds,
                        ),
                        operation="run_vspaero",
                    ).model_dump()
                # An operation that already validated may finish before a late cancellation.
                artifacts = {}
                for value in response["artifacts"].values():
                    path = Path(value).resolve()
                    if not path.is_relative_to(self.directory):
                        raise RuntimeError("Result artifact escaped the batch directory")
                    artifacts[str(path.relative_to(self.directory))] = _digest(path)
                with self.lock:
                    row.update(
                        status="success",
                        response=response,
                        artifact_hashes=artifacts,
                        response_sha256=_fingerprint(response),
                        error=None,
                    )
        except Exception as exc:  # noqa: BLE001 - persist background failures for the caller
            with self.lock:
                row.update(
                    status="cancelled" if isinstance(exc, runtime.OperationCancelled) else "failed",
                    error=str(exc),
                )
                if row["status"] == "failed" and self.request.failure_policy == "stop":
                    self.stop = self.stop or "failed"
        finally:
            with self.lock:
                row.update(finished_at=_now(), elapsed_seconds=time.monotonic() - started)
                row["attempts"].append(
                    {
                        "status": row["status"],
                        "finished_at": row["finished_at"],
                        "elapsed_seconds": row["elapsed_seconds"],
                        "error": row.get("error"),
                        "manifest_path": row.get("response", {}).get("manifest_path"),
                    }
                )
                self.save()

    def _run(self):
        started = time.monotonic()
        try:
            while True:
                with self.lock:
                    for key, (thread, _) in list(self.workers.items()):
                        if not thread.is_alive():
                            thread.join()
                            del self.workers[key]
                    used = sum(n for _, n in self.workers.values())
                    self.data["peak_reserved_threads"] = max(
                        self.data.get("peak_reserved_threads", 0), used
                    )
                    for index, row in enumerate(self.data["cases"]):
                        if row["status"] != "queued":
                            continue
                        if self.stop:
                            row["status"] = "cancelled" if self.stop == "cancelled" else "skipped"
                            continue
                        ncpu = self.request.cases[index].analysis.ncpu
                        if len(self.workers) >= self.request.max_parallel_jobs:
                            break
                        if used + ncpu > self.request.cpu_budget:
                            continue
                        row["status"] = "waiting_resources"
                        thread = threading.Thread(target=self._case, args=(index,), daemon=True)
                        self.workers[row["case_id"]] = (thread, ncpu)
                        used += ncpu
                        self.data["peak_reserved_threads"] = max(
                            self.data.get("peak_reserved_threads", 0), used
                        )
                        self.save()
                        thread.start()
                    if not self.workers and not any(
                        r["status"] == "queued" for r in self.data["cases"]
                    ):
                        break
                time.sleep(0.03)
            with self.lock:
                states = {r["status"] for r in self.data["cases"]}
                status = "success" if states == {"success"} else "partial"
                if "failed" in states:
                    status = "failed"
                if self.stop == "cancelled":
                    status = "cancelled"
                self.data.update(status=status, finished_at=_now())
        except Exception as exc:  # noqa: BLE001 - persist background failures for the caller
            for event in self.events.values():
                event.set()
            for thread, _ in list(self.workers.values()):
                thread.join()
            self.data.update(status="failed", error=str(exc), finished_at=_now())
        finally:
            try:
                with self.lock:
                    self.data["last_run_seconds"] = time.monotonic() - started
                    self.save()
            finally:
                self.ownership.close()
                with _jobs_lock:
                    if _jobs.get(str(self.directory)) is self:
                        del _jobs[str(self.directory)]


def _launch(directory, data, ownership):
    request = BatchRequest.model_validate(data["spec"]["request"])
    if request.cpu_budget > runtime.cpu_pool.capacity:
        raise RuntimeError(
            f"Batch cpu_budget exceeds server budget {runtime.cpu_pool.capacity}; "
            "configure OPENVSP_CPU_BUDGET or reduce the batch budget"
        )
    if len(_jobs) >= 16:
        raise RuntimeError("At most 16 active batches are allowed per server")
    data.update(status="running", started_at=_now(), finished_at=None, error=None)
    job = _Job(directory, data, ownership)
    job.save()
    _jobs[str(directory)] = job
    try:
        job.thread.start()
    except BaseException:
        del _jobs[str(directory)]
        raise


def submit_batch(request: BatchRequest) -> dict:
    """Return a durable handle promptly; disconnects do not cancel a submitted batch."""
    runtime.check_cancelled()
    ownership = None
    try:
        source = Path(request.geometry_file).expanduser().resolve()
        if source.suffix.lower() != ".vsp3" or not describe_geometry(str(source)).geom_ids:
            raise RuntimeError("Batch input must be a nonempty .vsp3 model")
        if request.cpu_budget > runtime.cpu_pool.capacity:
            raise RuntimeError(
                f"Batch cpu_budget exceeds server budget {runtime.cpu_pool.capacity}"
            )
        identity, source_hash = _identity(), _digest(source)
        parent = (
            Path(request.output_dir).expanduser().resolve()
            if request.output_dir
            else source.parent / "openvsp_runs"
        )
        parent.mkdir(parents=True, exist_ok=True)
        directory = Path(tempfile.mkdtemp(prefix="batch-", dir=parent)).resolve()
        ownership = _FileLock(directory)
        copy2(source, directory / "source.vsp3")
        if _digest(directory / "source.vsp3") != source_hash or _digest(source) != source_hash:
            raise RuntimeError("Input changed while taking the batch snapshot")
        normalized = request.model_copy(
            update={"geometry_file": str(source), "output_dir": str(parent)}
        )
        spec = {
            "request": normalized.model_dump(),
            "identity": identity,
            "source_sha256": source_hash,
        }
        data = {
            "schema_version": 1,
            "batch_directory": str(directory),
            "batch_id": directory.name,
            "created_at": _now(),
            "spec": spec,
            "fingerprint": _fingerprint(spec),
            "cases": [
                {"case_id": c.case_id, "status": "queued", "attempts": []} for c in request.cases
            ],
        }
        runtime.check_cancelled()
        with _jobs_lock:
            _launch(directory, data, ownership)
        ownership = None
        return batch_status(BatchStatusRequest(batch_directory=str(directory)))
    except OSError as exc:
        raise RuntimeError(f"Cannot submit batch: {exc}") from exc
    finally:
        if ownership:
            ownership.close()


def batch_status(request: BatchStatusRequest) -> dict:
    directory = Path(request.batch_directory).expanduser().resolve()
    ownership = None
    try:
        try:
            ownership = _FileLock(directory)
        except RuntimeError:
            pass
        # Hold an idle directory's lock while reading. Otherwise a runner can
        # finalize between reading "running" and probing an already released lock.
        data = _load(directory)
        active = ownership is None
    except OSError as exc:
        raise RuntimeError(f"Cannot inspect batch directory: {exc}") from exc
    finally:
        if ownership:
            ownership.close()
    status = "interrupted" if data["status"] == "running" and not active else data["status"]
    counts = {
        key: sum(r["status"] == key for r in data["cases"]) for key in sorted(_ACTIVE | _TERMINAL)
    }
    return {
        "batch_directory": str(directory),
        "manifest_path": str(directory / "batch.json"),
        "batch_id": data["batch_id"],
        "status": status,
        "active": active,
        "counts": counts,
        "total": len(data["cases"]),
        "completed": sum(counts[k] for k in _TERMINAL),
        "server_cpu_budget": runtime.cpu_pool.capacity,
        "batch_cpu_budget": data["spec"]["request"]["cpu_budget"],
        "max_parallel_jobs": data["spec"]["request"]["max_parallel_jobs"],
        "peak_reserved_threads": data.get("peak_reserved_threads", 0),
        "created_at": data["created_at"],
        "updated_at": data["updated_at"],
        "last_run_seconds": data.get("last_run_seconds"),
        "error": data.get("error"),
        "offset": request.offset,
        "limit": request.limit,
        "cases": [
            {
                k: row[k]
                for k in (
                    "case_id",
                    "status",
                    "error",
                    "started_at",
                    "finished_at",
                    "elapsed_seconds",
                )
                if k in row
            }
            | {"attempt_count": len(row["attempts"])}
            for row in data["cases"][request.offset : request.offset + request.limit]
        ],
    }


def cancel_batch(request: BatchCancelRequest) -> dict:
    directory = Path(request.batch_directory).expanduser().resolve()
    with _jobs_lock:
        data = _load(directory)
        unknown = set(request.case_ids) - {r["case_id"] for r in data["cases"]}
        if unknown:
            raise RuntimeError(f"Unknown case IDs: {sorted(unknown)}")
        job = _jobs.get(str(directory))
        if job:
            job.cancel(set(request.case_ids))
        elif _owned(directory):
            raise RuntimeError("Batch belongs to another server; cancel through its owner")
    return batch_status(BatchStatusRequest(batch_directory=str(directory)))


def resume_batch(request: BatchResumeRequest) -> dict:
    directory = Path(request.batch_directory).expanduser().resolve()
    ownership = None
    try:
        with _jobs_lock:
            ownership = _FileLock(directory)
            data = _load(directory)
            _validate_resume(directory, data)
            unknown = set(request.case_ids) - {r["case_id"] for r in data["cases"]}
            if unknown:
                raise RuntimeError(f"Unknown case IDs: {sorted(unknown)}")
            selected = 0
            for row in data["cases"]:
                if row["status"] == "success":
                    continue
                if request.case_ids and row["case_id"] not in request.case_ids:
                    if row["status"] in _ACTIVE:
                        row["status"] = "skipped"
                    continue
                if row["status"] in _ACTIVE and row.get("started_at"):
                    row["attempts"].append(
                        {"status": "interrupted", "started_at": row["started_at"]}
                    )
                if len(row["attempts"]) >= 100:
                    raise RuntimeError("Case attempt limit reached; submit a new batch")
                for key in (
                    "response",
                    "artifact_hashes",
                    "response_sha256",
                    "started_at",
                    "finished_at",
                    "elapsed_seconds",
                ):
                    row.pop(key, None)
                row.update(status="queued", error=None, native_pid=None)
                selected += 1
            if selected:
                _launch(directory, data, ownership)
                ownership = None
            elif data["status"] == "running":
                # A crash can occur after the last case was saved but before job finalization.
                states = {row["status"] for row in data["cases"]}
                status = "success" if states == {"success"} else "partial"
                if "failed" in states:
                    status = "failed"
                data.update(status=status, finished_at=_now(), updated_at=_now())
                _write(directory / "batch.json", data)
    except OSError as exc:
        raise RuntimeError(f"Cannot resume batch: {exc}") from exc
    finally:
        if ownership:
            ownership.close()
    return batch_status(BatchStatusRequest(batch_directory=str(directory)))


def export_batch(request: BatchExportRequest) -> dict:
    """Export a consistent manifest snapshot; pending/failed rows remain explicit."""
    directory = Path(request.batch_directory).expanduser().resolve()
    data = _load(directory)
    rows = []
    for case, state in zip(data["spec"]["request"]["cases"], data["cases"]):
        response = state.get("response", {})
        row = {
            "case_id": case["case_id"],
            "status": state["status"],
            "error": state.get("error"),
            "elapsed_seconds": state.get("elapsed_seconds"),
            "attempt_count": len(state["attempts"]),
        }
        row.update({"input." + k: v for k, v in case["analysis"].items()})
        row.update({"coefficient." + k: v for k, v in response.get("coefficients", {}).items()})
        for key in [
            "versions",
            "numerical_quality",
            "effective_settings",
            "parameter_values",
            "timings",
        ]:
            row[key] = response.get(key)
        row["requested_parameter_edits"] = case["parameter_edits"]
        rows.append(row)
    export_dir = directory / ("export-" + uuid.uuid4().hex[:12])
    try:
        export_dir.mkdir()
        metadata = {
            "schema_version": 1,
            "batch_fingerprint": data["fingerprint"],
            "source_sha256": data["spec"]["source_sha256"],
            "identity": data["spec"]["identity"],
            "exported_at": _now(),
            "angles": "degrees",
            "coefficients": "Native VSPAERO names and conventions, unchanged",
            "dimensional_units": "Per-case length_unit; other dimensional inputs must use consistent units",
            "numerical_validation": "Workflow checks do not establish mesh/solver convergence or accuracy",
        }
        _write(export_dir / "results.json", {"metadata": metadata, "cases": rows})
        fields = sorted({key for row in rows for key in row})
        with (export_dir / "results.csv").open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            for row in rows:
                formatted = {}
                for key, value in row.items():
                    if isinstance(value, (dict, list)):
                        value = json.dumps(value, sort_keys=True)
                    if isinstance(value, str) and value.startswith(("=", "+", "-", "@")):
                        value = "'" + value
                    formatted[key] = value
                writer.writerow(formatted)
    except OSError as exc:
        raise RuntimeError(f"Cannot export batch: {exc}") from exc
    return {
        "json_path": str(export_dir / "results.json"),
        "csv_path": str(export_dir / "results.csv"),
        "case_count": len(rows),
        "metadata": metadata,
    }


def shutdown_batches():
    """Used by server lifecycle hooks: request cancellation, then reap all workers."""
    with _jobs_lock:
        jobs = list(_jobs.values())
        for job in jobs:
            job.cancel(set())
    for job in jobs:
        job.thread.join()


@asynccontextmanager
async def batch_lifespan(app):
    try:
        yield {}
    finally:
        with anyio.CancelScope(shield=True):
            await anyio.to_thread.run_sync(shutdown_batches)
