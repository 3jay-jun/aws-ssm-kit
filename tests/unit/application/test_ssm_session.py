from pathlib import Path
from unittest.mock import Mock

import pytest

from aws_connect.application.operations import OperationCancelled
from aws_connect.application.ports import PluginDiagnostic, StartedSsmSession
from aws_connect.application.ssm_session import ForegroundSsmSessionRunner
from aws_connect.domain.aws_profile import PlainCredentials
from aws_connect.domain.errors import PluginExecutionError


def build() -> tuple[ForegroundSsmSessionRunner, Mock, Mock, PlainCredentials]:
    gateway = Mock()
    gateway.start_session.return_value = StartedSsmSession(
        "session-test", "wss://test.invalid", "transport-token"
    )
    plugin = Mock()
    plugin.diagnose.return_value = PluginDiagnostic(
        Path("plugin.exe"), True, "1.2.707.0", True, True
    )
    plugin.run.return_value = 0
    credentials = PlainCredentials("ACCESSKEYTEST0001", "test-secret-value-long", "token")
    return ForegroundSsmSessionRunner(gateway, plugin), gateway, plugin, credentials


def test_remote_host_request_is_shared_by_sdk_and_plugin() -> None:
    runner, gateway, plugin, credentials = build()
    parameters = {
        "host": ["db.example.internal"],
        "portNumber": ["3306"],
        "localPortNumber": ["13306"],
    }

    result = runner.run(
        credentials,
        "ap-northeast-2",
        "i-0123456789abcdef0",
        document_name="AWS-StartPortForwardingSessionToRemoteHost",
        parameters=parameters,
    )

    assert result.session_id == "session-test"
    gateway.start_session.assert_called_once_with(
        credentials,
        "ap-northeast-2",
        "i-0123456789abcdef0",
        "AWS-StartPortForwardingSessionToRemoteHost",
        parameters,
    )
    assert plugin.run.call_args.args[0].parameters == parameters
    gateway.end_session.assert_called_once()


@pytest.mark.parametrize("failure", [7, KeyboardInterrupt()])
def test_failure_and_ctrl_c_always_cleanup(failure: object) -> None:
    runner, gateway, plugin, credentials = build()
    if isinstance(failure, int):
        plugin.run.return_value = failure
        expected: type[BaseException] = PluginExecutionError
    else:
        plugin.run.side_effect = failure
        expected = KeyboardInterrupt

    with pytest.raises(expected):
        runner.run(credentials, "ap-northeast-2", "i-0123456789abcdef0")

    gateway.end_session.assert_called_once_with(credentials, "ap-northeast-2", "session-test")


@pytest.mark.parametrize("cancelled", [KeyboardInterrupt(), OperationCancelled()])
def test_cancel_with_end_session_failure_is_not_reported_as_clean_cancel(
    cancelled: BaseException,
) -> None:
    runner, gateway, plugin, credentials = build()
    plugin.run.side_effect = cancelled
    gateway.end_session.side_effect = RuntimeError("sensitive cleanup detail")

    with pytest.raises(PluginExecutionError) as caught:
        runner.run(credentials, "ap-northeast-2", "i-0123456789abcdef0")

    assert caught.value.message_code == "session.cancel.cleanup_failed"
    assert "sensitive cleanup detail" not in caught.value.technical_cause
    gateway.end_session.assert_called_once()


def test_plugin_diagnostic_failure_blocks_aws_start() -> None:
    runner, gateway, plugin, credentials = build()
    plugin.diagnose.return_value = PluginDiagnostic(Path("missing"), False, None, False, True)
    with pytest.raises(PluginExecutionError, match="plugin.not_found"):
        runner.run(credentials, "ap-northeast-2", "i-0123456789abcdef0")
    gateway.start_session.assert_not_called()
