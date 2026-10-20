"""Run blocking backend calls off the UI thread."""

from __future__ import annotations

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal


class _Signals(QObject):
    done = Signal(object)
    failed = Signal(str)


class _Task(QRunnable):
    def __init__(self, fn, *args):
        super().__init__()
        self.fn, self.args = fn, args
        self.signals = _Signals()

    def run(self):
        try:
            result = self.fn(*self.args)
        except Exception as e:  # noqa: BLE001 - surfaced in the UI
            self.signals.failed.emit(str(e))
        else:
            self.signals.done.emit(result)


_active: set[_Task] = set()


def run_async(fn, *args, on_done=None, on_error=None) -> None:
    task = _Task(fn, *args)
    task.setAutoDelete(False)
    _active.add(task)

    def finish(cb, value):
        _active.discard(task)
        if cb:
            cb(value)

    task.signals.done.connect(lambda v: finish(on_done, v))
    task.signals.failed.connect(lambda msg: finish(on_error, msg))
    QThreadPool.globalInstance().start(task)
