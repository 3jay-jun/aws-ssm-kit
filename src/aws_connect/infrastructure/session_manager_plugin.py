"""Session Manager Plugin discovery, diagnostics and foreground process adapter."""

from __future__ import annotations

import json
import os
import re
import subprocess  # nosec B404
import sys
from base64 import urlsafe_b64encode
from collections.abc import Callable, Sequence
from contextlib import suppress
from multiprocessing.connection import Connection, Listener
from pathlib import Path
from secrets import token_bytes
from threading import Event, Thread
from time import monotonic
from typing import Protocol
from uuid import uuid4

from aws_connect import APPLICATION_NAME
from aws_connect.application.ports import PluginDiagnostic, PluginInvocation, SessionProcess
from aws_connect.domain.errors import PluginExecutionError
from aws_connect.infrastructure.data_protection import protect_current_user_bytes
from aws_connect.infrastructure.masking import mask

MINIMUM_PLUGIN_VERSION = (1, 2, 0, 0)
_VERSION = re.compile(r"(\d+)\.(\d+)\.(\d+)\.(\d+)")


class PluginProcess(Protocol):
    @property
    def pid(self) -> int: ...
    def poll(self) -> int | None: ...
    def wait(self, timeout: float | None = None) -> int: ...
    def terminate(self) -> None: ...
    def kill(self) -> None: ...


ProcessFactory = Callable[[Sequence[str]], PluginProcess]
VersionReader = Callable[[Path], str]


class _SessionProcessAdapter:
    """Normalize subprocess timeout behavior to the Application port contract."""

    def __init__(self, process: PluginProcess) -> None:
        self._process = process

    @property
    def pid(self) -> int:
        return self._process.pid

    def poll(self) -> int | None:
        return self._process.poll()

    def wait(self, timeout: float | None = None) -> int:
        try:
            return self._process.wait(timeout=timeout)
        except subprocess.TimeoutExpired as error:
            raise TimeoutError("Session Manager Plugin did not exit in time") from error

    def terminate(self) -> None:
        self._process.terminate()

    def kill(self) -> None:
        self._process.kill()


class _TerminalSessionProcess:
    """Track the plugin hosted inside Windows Terminal over a local named pipe."""

    def __init__(self, connection: Connection, process_id: int) -> None:
        self._connection = connection
        self._process_id = process_id
        self._exit_code: int | None = None
        self._closed = False

    @property
    def pid(self) -> int:
        return self._process_id

    def poll(self) -> int | None:
        if self._exit_code is None:
            try:
                has_message = self._connection.poll()
            except (EOFError, OSError):
                self._record_exit(252)
                return self._exit_code
            if has_message:
                try:
                    message = self._connection.recv()
                except (EOFError, OSError):
                    self._record_exit(252)
                    return self._exit_code
                if isinstance(message, dict) and message.get("event") == "exited":
                    try:
                        self._record_exit(int(message["exit_code"]))
                    except (KeyError, TypeError, ValueError):
                        self._record_exit(252)
        return self._exit_code

    def wait(self, timeout: float | None = None) -> int:
        deadline = None if timeout is None else monotonic() + timeout
        while self.poll() is None:
            if deadline is not None and monotonic() >= deadline:
                raise TimeoutError("Windows Terminal session did not exit in time")
            Event().wait(0.05)
        exit_code = self._exit_code
        if exit_code is None:  # Defensive guard for alternative Connection implementations.
            raise OSError("Windows Terminal session host returned no exit status")
        return exit_code

    def terminate(self) -> None:
        self._send_command("terminate")

    def kill(self) -> None:
        self._send_command("kill")

    def _send_command(self, command: str) -> None:
        if self._exit_code is not None:
            return
        try:
            self._connection.send({"command": command})
        except (BrokenPipeError, EOFError, OSError):
            self._record_exit(252)

    def _record_exit(self, exit_code: int) -> None:
        self._exit_code = exit_code
        if not self._closed:
            with suppress(OSError):
                self._connection.close()
            self._closed = True


class SessionManagerPlugin:
    """Invoke the bundled plugin without AWS CLI or gossm."""

    def __init__(
        self,
        path: Path,
        *,
        process_factory: ProcessFactory | None = None,
        external_process_factory: ProcessFactory | None = None,
        version_reader: VersionReader | None = None,
        windows_environment: bool | None = None,
    ) -> None:
        self._path = path.resolve()
        self._process_factory = process_factory or _start_process
        self._external_process_factory = external_process_factory or _start_terminal_process
        self._version_reader = version_reader or _read_version
        self._windows_environment = (
            os.name == "nt" if windows_environment is None else windows_environment
        )

    def diagnose(self) -> PluginDiagnostic:
        exists = self._path.is_file()
        version: str | None = None
        supported = False
        if exists:
            try:
                version = self._version_reader(self._path)
                parsed = _parse_version(version)
                supported = parsed is not None and parsed >= MINIMUM_PLUGIN_VERSION
            except (OSError, subprocess.SubprocessError):
                version = None
        return PluginDiagnostic(
            path=self._path,
            exists=exists,
            version=version,
            supported=supported,
            environment_ready=self._windows_environment,
        )

    def run(self, invocation: PluginInvocation) -> int:
        process = self.launch(invocation)
        try:
            return process.wait()
        except KeyboardInterrupt:
            self._stop(process)
            raise
        except OSError as error:
            self._stop(process)
            raise PluginExecutionError(
                message_code="plugin.wait.failed",
                technical_cause=type(error).__name__,
            ) from error

    def launch(
        self, invocation: PluginInvocation, *, external_terminal: bool = False
    ) -> SessionProcess:
        """Launch the plugin without waiting; raw transport arguments must not be logged."""

        argv = self.build_argv(invocation)
        try:
            factory = self._external_process_factory if external_terminal else self._process_factory
            return _SessionProcessAdapter(factory(argv))
        except OSError as error:
            raise PluginExecutionError(
                message_code="plugin.start.failed",
                technical_cause=type(error).__name__,
            ) from error

    def build_argv(self, invocation: PluginInvocation) -> list[str]:
        """Build the official plugin contract; callers must never log this raw value."""

        session = json.dumps(
            {
                "SessionId": invocation.session.session_id,
                "StreamUrl": invocation.session.stream_url,
                "TokenValue": invocation.session.token_value,
            },
            separators=(",", ":"),
        )
        request: dict[str, object] = {"Target": invocation.target}
        if invocation.document_name is not None:
            request["DocumentName"] = invocation.document_name
        if invocation.parameters is not None:
            request["Parameters"] = invocation.parameters
        return [
            str(self._path),
            session,
            invocation.region,
            "StartSession",
            "",
            json.dumps(request, separators=(",", ":")),
            f"https://ssm.{invocation.region}.amazonaws.com",
        ]

    def safe_argv(self, invocation: PluginInvocation) -> list[str]:
        """Return a diagnostic snapshot with session transport secrets redacted."""

        return list(
            mask(
                self.build_argv(invocation),
                known_secrets=(
                    invocation.session.stream_url,
                    invocation.session.token_value,
                ),
            )
        )

    @staticmethod
    def _stop(process: SessionProcess) -> None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except TimeoutError:
            process.kill()
            process.wait(timeout=5)


def _start_process(argv: Sequence[str]) -> PluginProcess:
    # Inherited stdio deliberately keeps the interactive session in the current console.
    return subprocess.Popen(list(argv), shell=False)  # noqa: S603  # nosec B603


def _start_terminal_process(argv: Sequence[str]) -> PluginProcess:
    pipe_name = f"aws-connect-{uuid4()}"
    address = rf"\\.\pipe\{pipe_name}"
    authkey = token_bytes(32)
    protected_authkey = urlsafe_b64encode(protect_current_user_bytes(authkey)).decode("ascii")
    listener = Listener(address, family="AF_PIPE", authkey=authkey)
    accepted: list[Connection | BaseException] = []
    ready = Event()

    def accept_host() -> None:
        try:
            accepted.append(listener.accept())
        except BaseException as error:
            accepted.append(error)
        finally:
            ready.set()

    accept_thread = Thread(target=accept_host, daemon=True)
    accept_thread.start()
    dispatch: PluginProcess | None = None
    connection: Connection | None = None
    try:
        dispatch = subprocess.Popen(  # noqa: S603  # nosec B603
            external_terminal_argv(pipe_name, protected_authkey), shell=False
        )
        if not ready.wait(10):
            raise OSError("Windows Terminal session host did not connect")
        if not accepted or isinstance(accepted[0], BaseException):
            raise OSError("Windows Terminal session host connection failed")
        connection = accepted[0]
        connection.send({"plugin_argv": list(argv)})
        if not connection.poll(10):
            raise OSError("Windows Terminal session host did not report its process")
        started = connection.recv()
        if not isinstance(started, dict) or started.get("event") != "started":
            raise OSError("Windows Terminal session host returned an invalid response")
        try:
            process_id = int(started["process_id"])
        except (KeyError, TypeError, ValueError) as error:
            raise OSError("Windows Terminal session host returned an invalid process id") from error
    except BaseException:
        if connection is not None:
            with suppress(BrokenPipeError, EOFError, OSError):
                connection.send({"command": "terminate"})
            with suppress(OSError):
                connection.close()
        if dispatch is not None:
            _stop_dispatch(dispatch)
        raise
    finally:
        with suppress(OSError):
            listener.close()
        accept_thread.join(timeout=1)
    return _TerminalSessionProcess(connection, process_id)


def external_terminal_argv(pipe_name: str, protected_authkey: str) -> list[str]:
    """Build a command with a non-secret endpoint and DPAPI-protected one-use key."""

    if getattr(sys, "frozen", False):
        host = str(session_host_path())
        host_argv = [host]
    else:
        host_argv = [sys.executable, "-m", "aws_connect.session_host"]
    return [
        "wt.exe",
        "-w",
        "new",
        "new-tab",
        "--title",
        f"{APPLICATION_NAME} EC2",
        "--suppressApplicationTitle",
        *host_argv,
        "--pipe",
        pipe_name,
        "--auth",
        protected_authkey,
    ]


def session_host_path() -> Path:
    """Resolve the helper from the frozen sibling layout or current interpreter."""

    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().with_name("aws_connect_session_host.exe")
    return Path(sys.executable).resolve()


def _stop_dispatch(process: PluginProcess) -> None:
    try:
        if process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    except (OSError, subprocess.SubprocessError):
        # Best-effort cleanup must preserve the original handshake failure.
        return


def _read_version(path: Path) -> str:
    completed = subprocess.run(  # noqa: S603  # nosec B603
        [str(path), "--version"],
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
        shell=False,
    )
    return (completed.stdout or completed.stderr).strip()


def _parse_version(value: str) -> tuple[int, int, int, int] | None:
    matched = _VERSION.search(value)
    return tuple(int(part) for part in matched.groups()) if matched else None  # type: ignore[return-value]
