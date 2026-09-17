import json
import subprocess

import pytest

import aws_connect.infrastructure.session_manager_plugin as plugin_module
from aws_connect.application.ports import PluginInvocation, StartedSsmSession
from aws_connect.infrastructure.masking import REDACTED
from aws_connect.infrastructure.session_manager_plugin import (
    SessionManagerPlugin,
    external_terminal_argv,
)


class FakeProcess:
    def __init__(self, *, exit_code: int = 0, interrupt: bool = False) -> None:
        self.exit_code = exit_code
        self.interrupt = interrupt
        self.terminated = False
        self.killed = False
        self.wait_calls: list[float | None] = []

    @property
    def pid(self) -> int:
        return 7000

    def poll(self) -> int | None:
        return None if not self.terminated and not self.killed else self.exit_code

    def wait(self, timeout: float | None = None) -> int:
        self.wait_calls.append(timeout)
        if self.interrupt:
            self.interrupt = False
            raise KeyboardInterrupt
        return self.exit_code

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True


def invocation() -> PluginInvocation:
    return PluginInvocation(
        StartedSsmSession(
            "developer-session-test",
            "wss://stream.test.invalid/sensitive-path",
            "transport-token-value",
        ),
        "ap-northeast-2",
        "i-online",
    )


def test_diagnose_reports_missing_old_and_supported_versions(tmp_path) -> None:
    path = tmp_path / "session-manager-plugin.exe"
    missing = SessionManagerPlugin(path, windows_environment=True)
    assert not missing.diagnose().exists

    path.touch()
    old = SessionManagerPlugin(
        path, version_reader=lambda _: "1.1.0.0", windows_environment=True
    ).diagnose()
    assert old.exists and not old.supported

    supported = SessionManagerPlugin(
        path, version_reader=lambda _: "session-manager-plugin 1.2.707.0", windows_environment=True
    ).diagnose()
    assert supported.supported
    assert supported.environment_ready


def test_plugin_argv_matches_contract_and_safe_snapshot_masks_transport_secrets(
    tmp_path,
) -> None:
    path = tmp_path / "session-manager-plugin.exe"
    path.touch()
    plugin = SessionManagerPlugin(path, windows_environment=True)
    raw = plugin.build_argv(invocation())

    assert raw[0] == str(path.resolve())
    assert json.loads(raw[1]) == {
        "SessionId": "developer-session-test",
        "StreamUrl": "wss://stream.test.invalid/sensitive-path",
        "TokenValue": "transport-token-value",
    }
    assert raw[2:] == [
        "ap-northeast-2",
        "StartSession",
        "",
        '{"Target":"i-online"}',
        "https://ssm.ap-northeast-2.amazonaws.com",
    ]
    safe = " ".join(plugin.safe_argv(invocation()))
    assert "transport-token-value" not in safe
    assert "sensitive-path" not in safe
    assert REDACTED in safe


def test_remote_host_parameters_are_valid_json_not_shell_quoting(tmp_path) -> None:
    path = tmp_path / "session-manager-plugin.exe"
    path.touch()
    plugin = SessionManagerPlugin(path, windows_environment=True)
    remote = PluginInvocation(
        invocation().session,
        "ap-northeast-2",
        "i-online",
        "AWS-StartPortForwardingSessionToRemoteHost",
        {
            "host": ["db.example.internal"],
            "portNumber": ["3306"],
            "localPortNumber": ["13306"],
        },
    )
    request = json.loads(plugin.build_argv(remote)[5])
    assert request["Parameters"]["host"] == ["db.example.internal"]
    assert '"host"' in plugin.build_argv(remote)[5]


def test_plugin_inherits_console_and_returns_process_exit(tmp_path) -> None:
    path = tmp_path / "session-manager-plugin.exe"
    path.touch()
    process = FakeProcess(exit_code=7)
    received: list[list[str]] = []

    def factory(argv):
        received.append(list(argv))
        return process

    plugin = SessionManagerPlugin(path, process_factory=factory, windows_environment=True)
    assert plugin.run(invocation()) == 7
    assert received[0][0] == str(path.resolve())


def test_external_launch_uses_distinct_terminal_process_boundary(tmp_path) -> None:
    path = tmp_path / "session-manager-plugin.exe"
    path.touch()
    foreground = FakeProcess()
    external = FakeProcess()
    received: list[list[str]] = []

    def external_factory(argv):
        received.append(list(argv))
        return external

    plugin = SessionManagerPlugin(
        path,
        process_factory=lambda _: foreground,
        external_process_factory=external_factory,
        windows_environment=True,
    )

    launched = plugin.launch(invocation(), external_terminal=True)

    assert launched.wait() == external.exit_code
    assert received[0] == plugin.build_argv(invocation())
    assert not foreground.wait_calls


def test_external_terminal_argv_is_shell_free_and_contains_no_session_secrets() -> None:
    argv = external_terminal_argv("aws-connect-test-pipe", "dpapi-protected-key")

    assert argv[:7] == [
        "wt.exe",
        "-w",
        "new",
        "new-tab",
        "--title",
        "aws-ssm-kit EC2",
        "--suppressApplicationTitle",
    ]
    assert argv[8:10] == ["-m", "aws_connect.session_host"]
    assert argv[-4:] == [
        "--pipe",
        "aws-connect-test-pipe",
        "--auth",
        "dpapi-protected-key",
    ]
    assert "session-json" not in " ".join(argv)
    assert "--wait" not in argv


def test_frozen_external_terminal_uses_sibling_helper_without_session_payload(
    monkeypatch,
) -> None:
    monkeypatch.setattr(plugin_module.sys, "frozen", True, raising=False)
    monkeypatch.setattr(
        plugin_module.sys,
        "executable",
        r"C:\배포 경로\aws_connect.exe",
    )

    argv = external_terminal_argv("one-use-pipe", "dpapi-ciphertext")

    assert argv[7] == r"C:\배포 경로\aws_connect_session_host.exe"
    joined = " ".join(argv)
    assert "StreamUrl" not in joined
    assert "TokenValue" not in joined
    assert "session-json" not in joined


class FakeConnection:
    def __init__(self, response: object) -> None:
        self.response = response
        self.sent: list[object] = []
        self.closed = False

    def send(self, value: object) -> None:
        self.sent.append(value)

    def poll(self, timeout: float | None = None) -> bool:
        return True

    def recv(self) -> object:
        return self.response

    def close(self) -> None:
        self.closed = True


class FakeListener:
    def __init__(self, connection: FakeConnection, *, fail: bool = False) -> None:
        self.connection = connection
        self.fail = fail
        self.closed = False

    def accept(self) -> FakeConnection:
        if self.fail:
            raise OSError("accept failed")
        return self.connection

    def close(self) -> None:
        self.closed = True


def test_terminal_bridge_uses_one_use_dpapi_key_and_pipe_only_for_plugin_payload(
    monkeypatch,
) -> None:
    connection = FakeConnection({"event": "started", "process_id": 8123})
    listener = FakeListener(connection)
    captured_listener: list[tuple[str, bytes]] = []
    terminal_argv: list[str] = []
    dispatch = FakeProcess()
    raw_authkey = b"a" * 32

    def listener_factory(address, *, family, authkey):
        captured_listener.append((address, authkey))
        return listener

    def popen(argv, *, shell):
        terminal_argv.extend(argv)
        return dispatch

    monkeypatch.setattr(plugin_module, "Listener", listener_factory)
    monkeypatch.setattr(plugin_module, "token_bytes", lambda _size: raw_authkey)
    monkeypatch.setattr(
        plugin_module, "protect_current_user_bytes", lambda value: b"dpapi:" + value
    )
    monkeypatch.setattr(plugin_module.subprocess, "Popen", popen)

    process = plugin_module._start_terminal_process(
        ["plugin.exe", "StreamUrl=sensitive", "TokenValue=sensitive"]
    )

    assert process.pid == 8123
    assert captured_listener[0][1] == raw_authkey
    assert raw_authkey.decode("ascii") not in " ".join(terminal_argv)
    assert "StreamUrl=sensitive" not in terminal_argv
    assert "TokenValue=sensitive" not in terminal_argv
    assert connection.sent == [
        {"plugin_argv": ["plugin.exe", "StreamUrl=sensitive", "TokenValue=sensitive"]}
    ]
    assert listener.closed


def test_terminal_bridge_accept_failure_stops_dispatch_and_closes_listener(monkeypatch) -> None:
    connection = FakeConnection({"event": "started", "process_id": 8123})
    listener = FakeListener(connection, fail=True)
    dispatch = FakeProcess()
    monkeypatch.setattr(plugin_module, "Listener", lambda *args, **kwargs: listener)
    monkeypatch.setattr(plugin_module, "token_bytes", lambda _size: b"a" * 32)
    monkeypatch.setattr(plugin_module, "protect_current_user_bytes", lambda value: value)
    monkeypatch.setattr(plugin_module.subprocess, "Popen", lambda *args, **kwargs: dispatch)

    with pytest.raises(OSError, match="connection failed"):
        plugin_module._start_terminal_process(["plugin.exe", "opaque"])

    assert dispatch.terminated
    assert dispatch.wait_calls == [5]
    assert listener.closed


def test_terminal_process_reports_helper_pipe_loss_and_closes_connection() -> None:
    class LostConnection(FakeConnection):
        def poll(self, timeout=None):
            raise EOFError

    connection = LostConnection(None)
    process = plugin_module._TerminalSessionProcess(connection, 9123)

    assert process.poll() == 252
    assert process.wait() == 252
    assert connection.closed


def test_ctrl_c_terminates_plugin_process_before_propagating(tmp_path) -> None:
    path = tmp_path / "session-manager-plugin.exe"
    path.touch()
    process = FakeProcess(interrupt=True)
    plugin = SessionManagerPlugin(path, process_factory=lambda _: process, windows_environment=True)

    with pytest.raises(KeyboardInterrupt):
        plugin.run(invocation())

    assert process.terminated
    assert process.wait_calls == [None, 5]
    assert not process.killed


def test_ctrl_c_kills_plugin_when_graceful_stop_times_out(tmp_path) -> None:
    path = tmp_path / "session-manager-plugin.exe"
    path.touch()

    class HungProcess(FakeProcess):
        def wait(self, timeout=None):
            self.wait_calls.append(timeout)
            if timeout is None:
                raise KeyboardInterrupt
            if not self.killed:
                raise subprocess.TimeoutExpired("plugin", timeout)
            return 0

    process = HungProcess()
    plugin = SessionManagerPlugin(path, process_factory=lambda _: process, windows_environment=True)
    with pytest.raises(KeyboardInterrupt):
        plugin.run(invocation())
    assert process.terminated and process.killed
