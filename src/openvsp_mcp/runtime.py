"""Keep MCP responsive while synchronous native operations run in bounded workers."""

import os
import time
from contextlib import contextmanager
from contextvars import ContextVar
from functools import partial
from threading import BoundedSemaphore, Condition, Event, Lock

import anyio

_cancel: ContextVar[Event | None] = ContextVar("openvsp_cancel", default=None)
_slots = BoundedSemaphore(2)
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


async def run_async(function, *args, **kwargs):
    """Cancellation signals the worker and waits briefly for process-group cleanup."""
    cancel, finished = Event(), Event()

    def work():
        token = _cancel.set(cancel)
        acquired = False
        try:
            while not acquired:
                check_cancelled()
                acquired = _slots.acquire(timeout=0.05)
            check_cancelled()
            return function(*args, **kwargs)
        finally:
            if acquired:
                _slots.release()
            _cancel.reset(token)
            finished.set()

    try:
        return await anyio.to_thread.run_sync(partial(work), abandon_on_cancel=True)
    except anyio.get_cancelled_exc_class():
        cancel.set()
        with anyio.CancelScope(shield=True):
            with anyio.move_on_after(3):
                while not finished.is_set():
                    await anyio.sleep(0.05)
        raise


async def run_control(function, *args):
    """Status/cancel must remain available when both legacy worker slots are occupied."""
    return await anyio.to_thread.run_sync(partial(function, *args))
