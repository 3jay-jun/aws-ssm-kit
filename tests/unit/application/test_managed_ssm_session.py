from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock

import pytest

from aws_connect.application.operations import OperationContext, OperationState
from aws_connect.application.ports import PluginDiagnostic, StartedSsmSession
from aws_connect.application.ssm_session import ManagedSsmSessionRunner
from aws_connect.domain.aws_profile import PlainCredentials
from aws_connect.domain.errors import PluginExecutionError


class FakeProcess:
    pid = 7123

    def __init__(self) -> None:
        self.exit_code: int | None = None
        self.terminated = False
        self.killed = False

    def poll(self) -> int | None:
        return self.exit_code

    def wait(self, timeout: float | None = None) -> int:
        assert timeout == 5
        self.exit_code = 0
        return 0

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True


class FakeGateway:
    def __init__(self) -> None:
        self.ended: list[str] = []

    def start_session(self, credentials, region, target, document_name=None, parameters=None):
        return StartedSsmSession("session-managed", "wss://test.invalid", "token")

    def end_session(self, credentials, region, session_id) -> None:
        self.ended.append(session_id)


class FakePlugin:
    def __init__(self, process: FakeProcess) -> None:
        self.process = process
        self.external_terminal: list[bool] = []

    def diagnose(self) -> PluginDiagnostic:
        return PluginDiagnostic(Path("plugin.exe"), True, "1.2.707.0", True, True)

    def run(self, invocation) -> int:
        raise AssertionError("managed sessions must not use blocking run")

    def launch(self, invocation, *, external_terminal=False):
        self.external_terminal.append(external_terminal)
        return self.process


def test_managed_runner_tracks_process_and_ends_aws_session_once() -> None:
    gateway = FakeGateway()
    process = FakeProcess()
    plugin = FakePlugin(process)
    runner = ManagedSsmSessionRunner(gateway, plugin)  # type: ignore[arg-type]
    credentials = PlainCredentials("ACCESSKEYTEST0001", "test-secret-value-long", "test-token")

    started = runner.start(
        credentials,
        "ap-northeast-2",
        "i-0123456789abcdef0",
        external_terminal=True,
    )
    running = runner.status(started.operation_id)
    stopped = runner.stop(started.operation_id)
    assert plugin.external_terminal == [True]
    assert running.state is OperationState.RUNNING
    assert stopped.state is OperationState.CANCELLED
    assert process.terminated and not process.killed
    assert gateway.ended == ["session-managed"]
    with pytest.raises(PluginExecutionError, match="session.operation.not_found"):
        runner.status(started.operation_id)


def test_natural_nonzero_exit_is_failed_and_is_cleaned() -> None:
    gateway = FakeGateway()
    process = FakeProcess()
    process.exit_code = 7
    runner = ManagedSsmSessionRunner(gateway, FakePlugin(process))  # type: ignore[arg-type]
    started = runner.start(
        PlainCredentials("ACCESSKEYTEST0001", "test-secret-value-long", "test-token"),
        "ap-northeast-2",
        "i-0123456789abcdef0",
    )

    status = runner.status(started.operation_id)

    assert status.state is OperationState.FAILED
    assert status.exit_code == 7
    assert gateway.ended == ["session-managed"]


def test_managed_runner_ends_aws_session_when_process_cleanup_fails() -> None:
    gateway = FakeGateway()
    process = FakeProcess()
    process.terminate = Mock(side_effect=OSError("pipe lost"))  # type: ignore[method-assign]
    runner = ManagedSsmSessionRunner(gateway, FakePlugin(process))  # type: ignore[arg-type]
    started = runner.start(
        PlainCredentials("ACCESSKEYTEST0001", "test-secret-value-long", "test-token"),
        "ap-northeast-2",
        "i-0123456789abcdef0",
    )

    stopped = runner.stop(started.operation_id)

    assert stopped.state is OperationState.FAILED
    assert stopped.error is not None
    assert stopped.error.message_code == "plugin.stop.failed"
    assert process.killed
    assert gateway.ended == ["session-managed"]


def test_terminal_snapshot_is_returned_once_and_combines_plugin_and_end_failures() -> None:
    gateway = FakeGateway()
    gateway.end_session = Mock(side_effect=OSError("endpoint unavailable"))  # type: ignore[method-assign]
    process = FakeProcess()
    process.exit_code = 9
    runner = ManagedSsmSessionRunner(gateway, FakePlugin(process))  # type: ignore[arg-type]
    started = runner.start(
        PlainCredentials("ACCESSKEYTEST0001", "test-secret-value-long", "test-token"),
        "ap-northeast-2",
        "i-0123456789abcdef0",
    )

    terminal = runner.status(started.operation_id)

    assert terminal.state is OperationState.FAILED
    assert terminal.error is not None
    assert terminal.error.message_code == "plugin.exit.nonzero"
    assert "session.end.failed" in terminal.error.technical_cause
    gateway.end_session.assert_called_once()
    assert started.operation_id not in runner._owned
    with pytest.raises(PluginExecutionError, match="session.operation.not_found"):
        runner.status(started.operation_id)


def test_launch_and_progress_observer_faults_reclaim_aws_and_process_resources() -> None:
    credentials = PlainCredentials("ACCESSKEYTEST0001", "test-secret-value-long", "test-token")
    gateway = FakeGateway()
    launch_process = FakeProcess()
    launch_plugin = FakePlugin(launch_process)
    launch_plugin.launch = Mock(side_effect=KeyboardInterrupt())  # type: ignore[method-assign]
    launch_runner = ManagedSsmSessionRunner(gateway, launch_plugin)  # type: ignore[arg-type]

    with pytest.raises(KeyboardInterrupt):
        launch_runner.start(credentials, "ap-northeast-2", "i-test")
    assert gateway.ended == ["session-managed"]

    progress_gateway = FakeGateway()
    progress_process = FakeProcess()
    progress_runner = ManagedSsmSessionRunner(
        progress_gateway,
        FakePlugin(progress_process),  # type: ignore[arg-type]
    )

    def observer(event) -> None:
        if event.phase == "running":
            raise RuntimeError("observer disconnected")

    with pytest.raises(RuntimeError, match="observer disconnected"):
        progress_runner.start(
            credentials,
            "ap-northeast-2",
            "i-test",
            context=OperationContext(operation_id="progress-fault", progress=observer),
        )
    assert progress_process.terminated
    assert progress_gateway.ended == ["session-managed"]
    assert "progress-fault" not in progress_runner._owned


def test_terminal_status_race_ends_session_exactly_once() -> None:
    gateway = FakeGateway()
    process = FakeProcess()
    process.exit_code = 0
    runner = ManagedSsmSessionRunner(gateway, FakePlugin(process))  # type: ignore[arg-type]
    started = runner.start(
        PlainCredentials("ACCESSKEYTEST0001", "test-secret-value-long", "test-token"),
        "ap-northeast-2",
        "i-test",
    )

    def poll_once(_index: int) -> str:
        try:
            return runner.status(started.operation_id).state.value
        except PluginExecutionError as error:
            return error.message_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(poll_once, range(2)))

    assert sorted(outcomes) == ["SUCCEEDED", "session.operation.not_found"]
    assert gateway.ended == ["session-managed"]


def test_typed_launch_failure_keeps_primary_code_and_end_session_diagnostic() -> None:
    gateway = FakeGateway()
    gateway.end_session = Mock(side_effect=OSError("endpoint unavailable"))  # type: ignore[method-assign]
    plugin = FakePlugin(FakeProcess())
    plugin.launch = Mock(  # type: ignore[method-assign]
        side_effect=PluginExecutionError("plugin.launch.refused", "launcher rejected request")
    )
    runner = ManagedSsmSessionRunner(gateway, plugin)  # type: ignore[arg-type]

    with pytest.raises(PluginExecutionError) as raised:
        runner.start(
            PlainCredentials("ACCESSKEYTEST0001", "test-secret-value-long", "test-token"),
            "ap-northeast-2",
            "i-test",
        )

    assert raised.value.message_code == "plugin.launch.refused"
    assert "launcher rejected request" in raised.value.technical_cause
    assert "EndSession failed (OSError)" in raised.value.technical_cause
    assert runner._owned == {}
    gateway.end_session.assert_called_once()


def test_status_fault_keeps_first_error_and_all_cleanup_diagnostics() -> None:
    gateway = FakeGateway()
    gateway.end_session = Mock(side_effect=OSError("endpoint unavailable"))  # type: ignore[method-assign]
    process = FakeProcess()
    process.poll = Mock(side_effect=OSError("process handle lost"))  # type: ignore[method-assign]
    process.terminate = Mock(side_effect=OSError("terminate failed"))  # type: ignore[method-assign]
    process.kill = Mock(side_effect=OSError("kill failed"))  # type: ignore[method-assign]
    process.wait = Mock(side_effect=OSError("wait failed"))  # type: ignore[method-assign]
    runner = ManagedSsmSessionRunner(gateway, FakePlugin(process))  # type: ignore[arg-type]
    started = runner.start(
        PlainCredentials("ACCESSKEYTEST0001", "test-secret-value-long", "test-token"),
        "ap-northeast-2",
        "i-test",
    )

    terminal = runner.status(started.operation_id)

    assert terminal.state is OperationState.FAILED
    assert terminal.error is not None
    assert terminal.error.message_code == "plugin.status.failed"
    assert "plugin.stop.failed" in terminal.error.technical_cause
    assert "session.end.failed" in terminal.error.technical_cause
    assert started.operation_id not in runner._owned
    gateway.end_session.assert_called_once()


def test_observer_typed_failure_reclaims_owned_credentials_and_reports_cleanup() -> None:
    gateway = FakeGateway()
    gateway.end_session = Mock(side_effect=OSError("endpoint unavailable"))  # type: ignore[method-assign]
    process = FakeProcess()
    runner = ManagedSsmSessionRunner(gateway, FakePlugin(process))  # type: ignore[arg-type]

    def observer(event) -> None:
        if event.phase == "running":
            raise PluginExecutionError("observer.failed", "observer disconnected")

    with pytest.raises(PluginExecutionError) as raised:
        runner.start(
            PlainCredentials("ACCESSKEYTEST0001", "test-secret-value-long", "test-token"),
            "ap-northeast-2",
            "i-test",
            context=OperationContext(operation_id="observer-fault", progress=observer),
        )

    assert raised.value.message_code == "observer.failed"
    assert any("session.end.failed" in note for note in raised.value.__notes__)
    assert "observer-fault" not in runner._owned
    assert process.terminated
    gateway.end_session.assert_called_once()


def test_normal_external_exit_with_cleanup_failure_is_warning_not_session_failure():
    gateway, process = FakeGateway(), FakeProcess()
    gateway.end_session = Mock(side_effect=PluginExecutionError("session.end.failed", "cleanup"))
    runner = ManagedSsmSessionRunner(gateway, FakePlugin(process))
    session = runner.start(
        PlainCredentials("ACCESSKEYTEST0001", "local-test-secret", "local-test-token"),
        "us-east-1",
        "i-test",
        external_terminal=True,
    )
    process.exit_code = 0
    result = runner.status(session.operation_id)
    assert result.state is OperationState.SUCCEEDED and result.error is None
    assert result.warning is not None
    gateway.end_session.assert_called_once()
