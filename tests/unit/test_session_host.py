import signal

from aws_connect import session_host


class FakeConnection:
    def __init__(self) -> None:
        self.sent: list[object] = []
        self.requests = [{"plugin_argv": ["plugin.exe", "opaque-session"]}]
        self.closed = False

    def recv(self):
        return self.requests.pop(0)

    def send(self, value) -> None:
        self.sent.append(value)

    def poll(self, timeout=None) -> bool:
        return False

    def close(self) -> None:
        self.closed = True


class FakeProcess:
    pid = 9912
    returncode = 0

    def __init__(self) -> None:
        self.polls = 0
        self.terminated = False
        self.killed = False

    def poll(self):
        self.polls += 1
        return None if self.polls == 1 else self.returncode

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True

    def wait(self, timeout=None) -> int:
        return self.returncode


def test_session_host_receives_plugin_contract_over_pipe_and_reports_lifecycle(
    monkeypatch,
) -> None:
    connection = FakeConnection()
    process = FakeProcess()
    received: list[list[str]] = []
    client_authkeys: list[bytes] = []

    def client_factory(address, *, family, authkey):
        client_authkeys.append(authkey)
        return connection

    monkeypatch.setattr(session_host, "Client", client_factory)
    monkeypatch.setattr(
        session_host, "unprotect_current_user_bytes", lambda value: b"one-use-auth-key"
    )
    monkeypatch.setattr(
        session_host.subprocess,
        "Popen",
        lambda argv, shell: (received.append(argv), process)[1],
    )

    exit_code = session_host.main(["--pipe", "aws-connect-test", "--auth", "cHJvdGVjdGVk"])

    assert exit_code == 0
    assert received == [["plugin.exe", "opaque-session"]]
    assert client_authkeys == [b"one-use-auth-key"]
    assert connection.sent == [
        {"event": "started", "process_id": 9912},
        {"event": "exited", "exit_code": 0},
    ]
    assert connection.closed
    assert not process.terminated and not process.killed


def test_session_host_ignores_ctrl_c_only_after_plugin_has_inherited_console_handler(
    monkeypatch,
) -> None:
    connection = FakeConnection()
    process = FakeProcess()
    events: list[object] = []
    previous_handler = object()

    monkeypatch.setattr(session_host, "Client", lambda *args, **kwargs: connection)
    monkeypatch.setattr(
        session_host, "unprotect_current_user_bytes", lambda value: b"one-use-auth-key"
    )
    monkeypatch.setattr(
        session_host.subprocess,
        "Popen",
        lambda *args, **kwargs: (events.append("plugin-started"), process)[1],
    )
    monkeypatch.setattr(session_host.signal, "getsignal", lambda _signal: previous_handler)

    def install_handler(received_signal, handler):
        events.append((received_signal, handler))
        return previous_handler

    monkeypatch.setattr(session_host.signal, "signal", install_handler)

    assert session_host.main(["--pipe", "aws-connect-test", "--auth", "cHJvdGVjdGVk"]) == 0
    assert events == [
        "plugin-started",
        (signal.SIGINT, signal.SIG_IGN),
        (signal.SIGINT, previous_handler),
    ]


def test_session_host_stops_plugin_when_started_event_cannot_transfer_ownership(
    monkeypatch,
) -> None:
    class BrokenConnection(FakeConnection):
        def send(self, value) -> None:
            raise BrokenPipeError

    connection = BrokenConnection()
    process = FakeProcess()
    monkeypatch.setattr(session_host, "Client", lambda *args, **kwargs: connection)
    monkeypatch.setattr(
        session_host, "unprotect_current_user_bytes", lambda value: b"one-use-auth-key"
    )
    monkeypatch.setattr(session_host.subprocess, "Popen", lambda *args, **kwargs: process)

    exit_code = session_host.main(["--pipe", "aws-connect-test", "--auth", "cHJvdGVjdGVk"])

    assert exit_code == 252
    assert process.terminated
    assert connection.closed


def test_console_close_handler_reports_only_explicit_close(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock

    register = Mock(return_value=1)
    monkeypatch.setattr(
        session_host.ctypes,
        "WinDLL",
        lambda *a, **k: SimpleNamespace(SetConsoleCtrlHandler=register),
    )
    sent = []
    unregister = session_host._register_console_close(sent.append)
    callback = register.call_args.args[0]
    callback(0)
    assert sent == []
    callback(2)
    assert sent == [{"event": "console_closed"}]
    unregister()
    assert register.call_args.args[1] == 0
