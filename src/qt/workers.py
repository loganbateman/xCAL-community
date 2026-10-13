"""
Background worker for Qt — run a blocking callable off the UI thread and deliver
the result (or error) back on the UI thread via signals.

Keeps Supabase / Google Drive calls from freezing the interface, the Qt-correct
way (no manual thread-marshalling in the views).
"""
from __future__ import annotations
from typing import Callable, Any
from PySide6.QtCore import QObject, QThread, Signal, Slot, Qt


def check_interruption():
    if QThread.currentThread().isInterruptionRequested():
        raise InterruptedError("Transfer stopped because xCAL is closing.")


def _closing(parent):
    while parent is not None:
        if getattr(parent, "_closing", False):
            return True
        parent = parent.parent()
    return False


class Worker(QObject):
    done = Signal(object)      # emitted with the function's return value
    failed = Signal(str)       # emitted with the error message

    def __init__(self, fn: Callable[..., Any], *args, **kwargs):
        super().__init__()
        self._fn = fn
        self._args = args
        self._kwargs = kwargs

    def run(self):
        try:
            result = self._fn(*self._args, **self._kwargs)
        except Exception as exc:  # noqa: BLE001 - surface any backend error to UI
            from utils.debug import log
            log.exception("Background task failed: %s",
                          getattr(self._fn, "__name__", self._fn))
            self.failed.emit(str(exc))
        else:
            self.done.emit(result)


class ProgressWorker(QObject):
    """Worker whose callable receives a thread-safe integer progress callback."""

    done = Signal(object)
    failed = Signal(str)
    progress = Signal(int)

    def __init__(self, fn: Callable[[Callable[[int], None]], Any]):
        super().__init__()
        self._fn = fn

    def run(self):
        try:
            result = self._fn(self.progress.emit)
        except Exception as exc:  # noqa: BLE001 - surface backend errors to UI
            from utils.debug import log
            log.exception("Background progress task failed: %s",
                          getattr(self._fn, "__name__", self._fn))
            self.failed.emit(str(exc))
        else:
            self.done.emit(result)


class _CallbackProxy(QObject):
    """UI-thread QObject that safely invokes ordinary Python callbacks."""

    def __init__(self, on_done=None, on_error=None, on_progress=None, parent=None):
        super().__init__(parent)
        self._on_done = on_done
        self._on_error = on_error
        self._on_progress = on_progress

    @Slot(object)
    def handle_done(self, result):
        if _closing(self):
            return
        if self._on_done:
            self._on_done(result)

    @Slot(str)
    def handle_error(self, message):
        if _closing(self):
            return
        if self._on_error:
            self._on_error(message)

    @Slot(int)
    def handle_progress(self, percent):
        if _closing(self):
            return
        if self._on_progress:
            self._on_progress(percent)


def run_async(parent: QObject, fn: Callable[..., Any], on_done=None, on_error=None,
              *args, **kwargs) -> QThread:
    """Run `fn(*args, **kwargs)` on a QThread; call on_done/on_error on the UI thread.

    IMPORTANT: we keep a strong reference to BOTH the thread and the worker on the
    parent until the thread finishes. Without holding the worker, Python garbage-
    collects it right after this function returns (before `started` runs), so the
    task never executes and the QObject dies inside a live thread (crash).
    """
    thread = QThread(parent)
    worker = Worker(fn, *args, **kwargs)
    # Created in the caller/UI thread and parented to a UI object, so queued
    # signals below always execute callbacks on the UI thread. Connecting a
    # worker signal directly to a plain Python closure can execute that closure
    # in the worker thread, which is illegal for widget/dialog operations.
    proxy = _CallbackProxy(on_done, on_error, parent=parent)
    worker.moveToThread(thread)

    thread.started.connect(worker.run)
    if on_done:
        worker.done.connect(proxy.handle_done, Qt.QueuedConnection)
    if on_error:
        worker.failed.connect(proxy.handle_error, Qt.QueuedConnection)

    # Stop the thread once the work signals, then clean up on finish.
    worker.done.connect(thread.quit)
    worker.failed.connect(thread.quit)
    thread.finished.connect(worker.deleteLater)
    thread.finished.connect(proxy.deleteLater)
    thread.finished.connect(thread.deleteLater)

    # Keep strong refs to thread AND worker until the thread finishes.
    reg = getattr(parent, "_async_reg", None)
    if reg is None:
        reg = set()
        parent._async_reg = reg
    holder = (thread, worker, proxy)
    reg.add(holder)
    thread.finished.connect(lambda: reg.discard(holder))

    thread.start()
    return thread


def run_async_progress(parent: QObject, fn, on_done=None, on_error=None,
                       on_progress=None) -> QThread:
    """Run a callable that accepts ``progress(int)`` on a background thread."""
    thread = QThread(parent)
    worker = ProgressWorker(fn)
    proxy = _CallbackProxy(on_done, on_error, on_progress, parent)
    worker.moveToThread(thread)

    thread.started.connect(worker.run)
    worker.done.connect(proxy.handle_done, Qt.QueuedConnection)
    worker.failed.connect(proxy.handle_error, Qt.QueuedConnection)
    worker.progress.connect(proxy.handle_progress, Qt.QueuedConnection)
    worker.done.connect(thread.quit)
    worker.failed.connect(thread.quit)
    thread.finished.connect(worker.deleteLater)
    thread.finished.connect(proxy.deleteLater)
    thread.finished.connect(thread.deleteLater)

    reg = getattr(parent, "_async_reg", None)
    if reg is None:
        reg = set()
        parent._async_reg = reg
    holder = (thread, worker, proxy)
    reg.add(holder)
    thread.finished.connect(lambda: reg.discard(holder))

    thread.start()
    return thread
