"""Keep MCP responsive while synchronous native operations run in bounded workers."""

from contextvars import ContextVar
from functools import partial
from threading import BoundedSemaphore, Event, Lock

import anyio

_cancel: ContextVar[Event | None] = ContextVar("openvsp_cancel", default=None)
_slots = BoundedSemaphore(2)
commit_lock = Lock()


class OperationCancelled(RuntimeError):
    """The client cancelled an operation before its next commit point."""


def check_cancelled():
    event = _cancel.get()
    if event is not None and event.is_set():
        raise OperationCancelled("OpenVSP operation cancelled")


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
