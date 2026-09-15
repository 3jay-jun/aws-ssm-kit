"""Qt thread-pool adapter for blocking Application Service calls."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot

from aws_connect.application.operations import CancellationToken
from aws_connect.domain.errors import ApplicationError, ConfigurationError


class TaskSignals(QObject):
    succeeded = Signal(object)
    failed = Signal(object)
    finished = Signal()
    progress = Signal(object)


class TaskHandle:
    """Presentation handle that delegates cancellation to the shared contract."""

    def __init__(self, token: CancellationToken) -> None:
        self._token = token

    def cancel(self) -> None:
        self._token.cancel()


class ApplicationTask(QRunnable):
    """Execute one blocking use case without touching widgets."""

    def __init__(
        self,
        operation: Callable[[], object],
        token: CancellationToken,
        *,
        emit_cancelled_result: bool = False,
    ) -> None:
        super().__init__()
        self._operation = operation
        self._token = token
        self._emit_cancelled_result = emit_cancelled_result
        self.signals = TaskSignals()

    @Slot()
    def run(self) -> None:
        if self._token.is_cancellation_requested and not self._emit_cancelled_result:
            self.signals.finished.emit()
            return
        try:
            result = self._operation()
        except ApplicationError as error:
            self.signals.failed.emit(error)
        except Exception as error:
            self.signals.failed.emit(
                ConfigurationError(
                    message_code="gui.task.unexpected",
                    # Arbitrary adapter exceptions may contain credentials or payloads.
                    technical_cause=type(error).__name__,
                )
            )
        else:
            if self._emit_cancelled_result or not self._token.is_cancellation_requested:
                self.signals.succeeded.emit(result)
        finally:
            self.signals.finished.emit()


class GuiTaskRunner(QObject):
    """Own task lifetimes and marshal results back to the GUI thread."""

    busy_changed = Signal(bool)

    def __init__(self, pool: QThreadPool | None = None) -> None:
        super().__init__()
        self._pool = pool or QThreadPool.globalInstance()
        self._tasks: set[ApplicationTask] = set()

    def submit(
        self,
        operation: Callable[[], object],
        on_success: Callable[[Any], None],
        on_error: Callable[[ApplicationError], None],
    ) -> TaskHandle:
        token = CancellationToken()
        task = ApplicationTask(operation, token)
        self._tasks.add(task)
        task.signals.succeeded.connect(on_success)
        task.signals.failed.connect(on_error)
        task.signals.finished.connect(lambda: self._complete(task))
        if len(self._tasks) == 1:
            self.busy_changed.emit(True)
        self._pool.start(task)
        return TaskHandle(token)

    def submit_cancellable(
        self,
        operation: Callable[[CancellationToken, Callable[[object], None]], object],
        on_success: Callable[[Any], None],
        on_error: Callable[[ApplicationError], None],
        on_progress: Callable[[Any], None],
    ) -> TaskHandle:
        """Run a cancellable Application operation and marshal progress to the GUI thread."""

        token = CancellationToken()
        task = ApplicationTask(
            lambda: operation(token, task.signals.progress.emit),
            token,
            emit_cancelled_result=True,
        )
        self._tasks.add(task)
        task.signals.succeeded.connect(on_success)
        task.signals.failed.connect(on_error)
        task.signals.progress.connect(on_progress)
        task.signals.finished.connect(lambda: self._complete(task))
        if len(self._tasks) == 1:
            self.busy_changed.emit(True)
        self._pool.start(task)
        return TaskHandle(token)

    def _complete(self, task: ApplicationTask) -> None:
        self._tasks.discard(task)
        if not self._tasks:
            self.busy_changed.emit(False)
