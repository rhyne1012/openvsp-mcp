"""Keep MCP responsive while synchronous native operations run in bounded workers."""

import os
import time
from contextlib import contextmanager
from contextvars import ContextVar
from functools import partial
from threading import Condition, Event, Lock

import anyio
from anyio.lowlevel import RunVar

_cancel: ContextVar[Event | None] = ContextVar("openvsp_cancel", default=None)
_limiters = RunVar("openvsp_worker_limiters")
_admission: ContextVar[float] = ContextVar("openvsp_worker_queue_seconds", default=0.0)
commit_lock = Lock()
_lease: ContextVar[int] = ContextVar("openvsp_cpu_lease", default=0)
_observer: ContextVar[object] = ContextVar("openvsp_process_observer", default=None)
_deadline: ContextVar[float | None] = ContextVar("openvsp_operation_deadline", default=None)


class CpuPool:
    """Weighted process-local CPU admission shared by batch and direct operations."""

    def __init__(self, capacity):
        self.capacity = capacity
        self.used = 0
        self.peak = 0
        self.condition = Condition()

    @contextmanager
    def reserve(self, count):
        if count > self.capacity:
            raise RuntimeError(
                f"Requested {count} CPU threads exceeds server budget {self.capacity}; "
                "reduce ncpu or configure OPENVSP_CPU_BUDGET before starting the server"
            )
        with self.condition:
            while self.used + count > self.capacity:
                check_cancelled()
                self.condition.wait(0.05)
            check_cancelled()
            self.used += count
            self.peak = max(self.peak, self.used)
        try:
            yield
        finally:
            with self.condition:
                self.used -= count
                self.condition.notify_all()


cpu_pool = CpuPool(int(os.environ.get("OPENVSP_CPU_BUDGET", max(4, os.cpu_count() or 4))))
if not 1 <= cpu_pool.capacity <= 4096:
    raise RuntimeError("OPENVSP_CPU_BUDGET must be between 1 and 4096")


@contextmanager
def cancellation_scope(event, observer=None):
    token = _cancel.set(event)
    process_token = _observer.set(observer)
    try:
        check_cancelled()
        yield
    finally:
        _observer.reset(process_token)
        _cancel.reset(token)


def observe_process(pid):
    observer = _observer.get()
    if observer is not None:
        observer(pid)


@contextmanager
def deadline_scope(deadline):
    current = _deadline.get()
    token = _deadline.set(min(current, deadline) if current is not None else deadline)
    try:
        check_cancelled()
        yield
        check_cancelled()
    finally:
        _deadline.reset(token)


@contextmanager
def cpu_allocation(count):
    if _lease.get() >= count:
        yield
        return
    if _lease.get():
        raise RuntimeError("Cannot enlarge a CPU allocation inside an active operation")
    with cpu_pool.reserve(count):
        token = _lease.set(count)
        try:
            yield
        finally:
            _lease.reset(token)


class OperationCancelled(RuntimeError):
    """The client cancelled an operation before its next commit point."""


def check_cancelled():
    event = _cancel.get()
    if event is not None and event.is_set():
        raise OperationCancelled("OpenVSP operation cancelled")
    deadline = _deadline.get()
    if deadline is not None and time.monotonic() >= deadline:
        raise RuntimeError("OpenVSP operation exceeded its total time budget")


def worker_queue_seconds():
    return _admission.get()


async def _dispatch(function, args, kwargs, lane):
    """Cancellation signals the worker and waits briefly for process-group cleanup."""
    cancel, entered, finished = Event(), Event(), Event()
    started = time.monotonic()
    try:
        limiters = _limiters.get()
    except LookupError:
        limiters = {
            name: anyio.CapacityLimiter(size)
            for name, size in [("native", 2), ("read", 4), ("control", 4)]
        }
        _limiters.set(limiters)

    def work():
        entered.set()
        token = _cancel.set(cancel)
        admission = _admission.set(time.monotonic() - started)
        try:
            check_cancelled()
            return function(*args, **kwargs)
        finally:
            _admission.reset(admission)
            _cancel.reset(token)
            finished.set()

    try:
        return await anyio.to_thread.run_sync(
            partial(work), abandon_on_cancel=True, limiter=limiters[lane]
        )
    except anyio.get_cancelled_exc_class():
        cancel.set()
        with anyio.CancelScope(shield=True):
            with anyio.move_on_after(3):
                while entered.is_set() and not finished.is_set():
                    await anyio.sleep(0.05)
        raise


async def run_async(function, *args, **kwargs):
    """Bound native operations separately from reads and lifecycle controls."""
    return await _dispatch(function, args, kwargs, "native")


async def run_read(function, *args):
    """File reads do not wait for native jobs or occupy their worker capacity."""
    return await _dispatch(function, args, {}, "read")


async def run_control(function, *args):
    """Batch lifecycle operations have their own capacity, including AnyIO admission."""
    return await _dispatch(function, args, {}, "control")
