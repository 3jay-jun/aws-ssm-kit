"""Shared foreground Session Manager lifecycle."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from threading import Lock

from aws_connect.application.operations import OperationCancelled, OperationContext, OperationState
from aws_connect.application.ports import (
    ManagedInstanceGateway,
    PluginDiagnostic,
    PluginInvocation,
    SessionPlugin,
    SessionProcess,
)
from aws_connect.domain.aws_profile import PlainCredentials
from aws_connect.domain.errors import ApplicationError, PluginExecutionError


@dataclass(frozen=True, slots=True)
class SsmRunResult:
    session_id: str
    plugin_exit_code: int


@dataclass(frozen=True, slots=True)
class ManagedSsmSession:
    operation_id: str
    process_id: int
    session_id: str
    state: OperationState
    exit_code: int | None = None
    error: ApplicationError | None = None


@dataclass(slots=True)
class _OwnedSession:
    credentials: PlainCredentials
    region: str
    session_id: str
    process: SessionProcess
    ended: bool = False


class ForegroundSsmSessionRunner:
    """Own the plugin process and SSM session until foreground completion."""

    def __init__(self, sessions: ManagedInstanceGateway, plugin: SessionPlugin) -> None:
        self._sessions = sessions
        self._plugin = plugin

    def diagnose(self) -> PluginDiagnostic:
        return self._plugin.diagnose()

    def run(
        self,
        credentials: PlainCredentials,
        region: str,
        target: str,
        *,
        document_name: str | None = None,
        parameters: Mapping[str, Sequence[str]] | None = None,
        context: OperationContext | None = None,
    ) -> SsmRunResult:
        context = context or OperationContext()
        context.raise_if_cancelled()
        context.report("plugin-check", "session.plugin.check", target=target)
        self._ensure_plugin_ready()
        context.raise_if_cancelled()
        context.report("session-start", "session.aws.start", target=target)
        started = self._sessions.start_session(
            credentials, region, target, document_name, parameters
        )
        invocation = PluginInvocation(started, region, target, document_name, parameters)
        try:
            context.raise_if_cancelled()
            context.report("running", "session.running", target=target)
            exit_code = self._plugin.run(invocation)
            if exit_code != 0:
                raise self._plugin_error(
                    "plugin.exit.nonzero", f"Session Manager Plugin exited with {exit_code}"
                )
        except BaseException as primary:
            self._raise_after_end(credentials, region, started.session_id, primary)
        try:
            self._sessions.end_session(credentials, region, started.session_id)
        except Exception as error:
            raise PluginExecutionError("session.end.failed", type(error).__name__) from error
        context.report("completed", "session.completed", completed=1, total=1, target=target)
        return SsmRunResult(started.session_id, exit_code)

    def _raise_after_end(
        self,
        credentials: PlainCredentials,
        region: str,
        session_id: str,
        primary: BaseException,
    ) -> None:
        """Preserve the primary failure while making cleanup failure observable."""

        cleanup: Exception | None = None
        try:
            self._sessions.end_session(credentials, region, session_id)
        except Exception as error:
            cleanup = error
        if isinstance(primary, (KeyboardInterrupt, OperationCancelled)):
            if cleanup is not None:
                raise PluginExecutionError(
                    "session.cancel.cleanup_failed",
                    f"{type(primary).__name__}; EndSession failed ({type(cleanup).__name__})",
                ) from cleanup
            raise primary
        if isinstance(primary, ApplicationError):
            if cleanup is not None:
                raise _with_cleanup_failure(primary, cleanup) from primary
            raise primary
        if isinstance(primary, Exception):
            converted = PluginExecutionError(
                "plugin.execute.failed",
                f"{type(primary).__name__}: {primary}",
            )
            if cleanup is not None:
                converted = _with_cleanup_failure(converted, cleanup)
            raise converted from primary
        raise primary

    def _ensure_plugin_ready(self) -> None:
        _ensure_plugin_ready(self._plugin)

    @staticmethod
    def _plugin_error(code: str, cause: str) -> PluginExecutionError:
        return PluginExecutionError(message_code=code, technical_cause=cause)


class ManagedSsmSessionRunner:
    """Start and explicitly own non-blocking plugin and AWS session lifetimes."""

    def __init__(self, sessions: ManagedInstanceGateway, plugin: SessionPlugin) -> None:
        self._sessions = sessions
        self._plugin = plugin
        self._owned: dict[str, _OwnedSession] = {}
        self._lock = Lock()

    def start(
        self,
        credentials: PlainCredentials,
        region: str,
        target: str,
        *,
        document_name: str | None = None,
        parameters: Mapping[str, Sequence[str]] | None = None,
        external_terminal: bool = False,
        context: OperationContext | None = None,
    ) -> ManagedSsmSession:
        context = context or OperationContext()
        context.raise_if_cancelled()
        context.report("plugin-check", "session.plugin.check", target=target)
        self._ensure_plugin_ready()
        context.raise_if_cancelled()
        context.report("session-start", "session.aws.start", target=target)
        started = self._sessions.start_session(
            credentials, region, target, document_name, parameters
        )
        invocation = PluginInvocation(started, region, target, document_name, parameters)
        try:
            context.raise_if_cancelled()
            context.report("plugin-launch", "session.plugin.launch", target=target)
            process = self._plugin.launch(invocation, external_terminal=external_terminal)
        except BaseException as primary:
            self._raise_start_failure(credentials, region, started.session_id, primary)
        operation_id = context.operation_id
        with self._lock:
            self._owned[operation_id] = _OwnedSession(
                credentials, region, started.session_id, process
            )
        try:
            context.report("running", "session.running", completed=1, total=1, target=target)
        except BaseException as primary:
            # The observer is outside the ownership boundary. Reclaim both resources
            # before propagating its failure so credentials/processes are not retained.
            cleanup_error: ApplicationError | None = None
            stopped = self.stop(operation_id)
            cleanup_error = stopped.error
            if cleanup_error is not None:
                primary.add_note(f"Managed session cleanup failed ({cleanup_error.message_code})")
            raise primary
        return ManagedSsmSession(
            operation_id,
            process.pid,
            started.session_id,
            OperationState.RUNNING,
        )

    def status(self, operation_id: str) -> ManagedSsmSession:
        with self._lock:
            owned = self._require_owned(operation_id)
            try:
                exit_code = owned.process.poll()
            except Exception as primary:
                process_error = PluginExecutionError(
                    "plugin.status.failed", f"{type(primary).__name__}: {primary}"
                )
                _exit_code, cleanup_error = self._stop_process(owned)
                end_error = self._capture_end_error(owned)
                self._owned.pop(operation_id, None)
                return self._snapshot(
                    operation_id,
                    owned,
                    OperationState.FAILED,
                    None,
                    _combine_errors(process_error, cleanup_error, end_error),
                )
            if exit_code is None:
                return self._snapshot(operation_id, owned, OperationState.RUNNING, None)
            state = OperationState.SUCCEEDED if exit_code == 0 else OperationState.FAILED
            plugin_error = (
                PluginExecutionError(
                    "plugin.exit.nonzero", f"Session Manager Plugin exited with {exit_code}"
                )
                if exit_code != 0
                else None
            )
            end_error = self._capture_end_error(owned)
            if end_error is not None:
                plugin_error = _combine_errors(plugin_error, end_error)
                state = OperationState.FAILED
            self._owned.pop(operation_id, None)
            return self._snapshot(operation_id, owned, state, exit_code, plugin_error)

    def stop(self, operation_id: str) -> ManagedSsmSession:
        with self._lock:
            owned = self._require_owned(operation_id)
            process_error: PluginExecutionError | None = None
            try:
                exit_code = owned.process.poll()
            except Exception as primary:
                exit_code = None
                process_error = PluginExecutionError(
                    "plugin.status.failed", f"{type(primary).__name__}: {primary}"
                )
            naturally_finished = exit_code is not None
            if exit_code is None:
                exit_code, stop_error = self._stop_process(owned)
                process_error = _combine_errors(process_error, stop_error)
            end_error = self._capture_end_error(owned)
            self._owned.pop(operation_id, None)
            if naturally_finished:
                state = OperationState.SUCCEEDED if exit_code == 0 else OperationState.FAILED
            else:
                state = OperationState.CANCELLED
            plugin_error = (
                PluginExecutionError(
                    "plugin.exit.nonzero", f"Session Manager Plugin exited with {exit_code}"
                )
                if state is OperationState.FAILED
                else None
            )
            error = _combine_errors(plugin_error, process_error, end_error)
            if error is not None:
                state = OperationState.FAILED
            return self._snapshot(operation_id, owned, state, exit_code, error)

    def _raise_start_failure(
        self,
        credentials: PlainCredentials,
        region: str,
        session_id: str,
        primary: BaseException,
    ) -> None:
        cleanup: Exception | None = None
        try:
            self._sessions.end_session(credentials, region, session_id)
        except Exception as error:
            cleanup = error
        if isinstance(primary, ApplicationError):
            if cleanup is not None:
                raise _with_cleanup_failure(primary, cleanup) from primary
            raise primary
        if isinstance(primary, OperationCancelled):
            if cleanup is not None:
                raise PluginExecutionError(
                    "session.cancel.cleanup_failed", type(cleanup).__name__
                ) from cleanup
            raise primary
        if isinstance(primary, Exception):
            converted = PluginExecutionError(
                "plugin.launch.failed", f"{type(primary).__name__}: {primary}"
            )
            if cleanup is not None:
                converted = _with_cleanup_failure(converted, cleanup)
            raise converted from primary
        if cleanup is not None:
            primary.add_note(f"EndSession failed ({type(cleanup).__name__})")
        raise primary

    @staticmethod
    def _stop_process(
        owned: _OwnedSession,
    ) -> tuple[int | None, PluginExecutionError | None]:
        try:
            owned.process.terminate()
            try:
                return owned.process.wait(timeout=5), None
            except TimeoutError:
                owned.process.kill()
                return owned.process.wait(timeout=5), None
        except Exception as primary:
            recovery_notes: list[str] = []
            try:
                owned.process.kill()
            except Exception as error:
                recovery_notes.append(f"kill failed ({type(error).__name__})")
            exit_code: int | None = None
            try:
                exit_code = owned.process.wait(timeout=5)
            except Exception as error:
                recovery_notes.append(f"wait failed ({type(error).__name__})")
            detail = f"{type(primary).__name__}: {primary}"
            if recovery_notes:
                detail += "; " + "; ".join(recovery_notes)
            return exit_code, PluginExecutionError("plugin.stop.failed", detail)

    def _capture_end_error(self, owned: _OwnedSession) -> PluginExecutionError | None:
        try:
            self._end_once(owned)
        except Exception as error:
            return PluginExecutionError("session.end.failed", type(error).__name__)
        return None

    def _end_once(self, owned: _OwnedSession) -> None:
        if not owned.ended:
            # Mark before the boundary call: even a failing request is attempted once.
            owned.ended = True
            self._sessions.end_session(owned.credentials, owned.region, owned.session_id)

    @staticmethod
    def _snapshot(
        operation_id: str,
        owned: _OwnedSession,
        state: OperationState,
        exit_code: int | None,
        error: PluginExecutionError | None = None,
    ) -> ManagedSsmSession:
        return ManagedSsmSession(
            operation_id, owned.process.pid, owned.session_id, state, exit_code, error
        )

    def _require_owned(self, operation_id: str) -> _OwnedSession:
        try:
            return self._owned[operation_id]
        except KeyError as error:
            raise PluginExecutionError(
                message_code="session.operation.not_found",
                technical_cause="Managed SSM session is not owned by this process",
            ) from error

    def _ensure_plugin_ready(self) -> None:
        _ensure_plugin_ready(self._plugin)


def _ensure_plugin_ready(plugin: SessionPlugin) -> None:
    diagnostic = plugin.diagnose()
    if not diagnostic.exists:
        raise ForegroundSsmSessionRunner._plugin_error(
            "plugin.not_found", "Session Manager Plugin is missing"
        )
    if not diagnostic.environment_ready:
        raise ForegroundSsmSessionRunner._plugin_error(
            "plugin.environment.invalid", "Plugin execution environment is not supported"
        )
    if not diagnostic.supported:
        raise ForegroundSsmSessionRunner._plugin_error(
            "plugin.version.unsupported", "Session Manager Plugin version is too old"
        )


def _with_cleanup_failure(primary: ApplicationError, cleanup: Exception) -> PluginExecutionError:
    return PluginExecutionError(
        message_code=primary.message_code,
        technical_cause=(
            f"{primary.technical_cause}; EndSession failed ({type(cleanup).__name__})"
        ),
        retryable=primary.retryable,
        aws_service=primary.aws_service,
        aws_action=primary.aws_action,
        correlation_id=primary.correlation_id,
    )


def _combine_errors(
    *errors: PluginExecutionError | None,
) -> PluginExecutionError | None:
    present = [error for error in errors if error is not None]
    if not present:
        return None
    primary = present[0]
    if len(present) == 1:
        return primary
    return PluginExecutionError(
        message_code=primary.message_code,
        technical_cause="; ".join(
            [primary.technical_cause]
            + [f"{error.message_code} ({error.technical_cause})" for error in present[1:]]
        ),
        retryable=primary.retryable,
        aws_service=primary.aws_service,
        aws_action=primary.aws_action,
        correlation_id=primary.correlation_id,
    )
