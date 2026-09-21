"""Private Windows Terminal host for one Session Manager Plugin process."""

from __future__ import annotations

import argparse
import ctypes
import os
import signal
import subprocess  # nosec B404
from base64 import urlsafe_b64decode
from collections.abc import Callable, Sequence
from contextlib import suppress
from multiprocessing.connection import Client
from threading import Event, Lock
from typing import Any

from aws_connect.infrastructure.data_protection import unprotect_current_user_bytes


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--pipe", required=True)
    parser.add_argument("--auth", required=True)
    args = parser.parse_args(list(argv) if argv is not None else None)
    address = rf"\\.\pipe\{args.pipe}"
    try:
        protected_authkey = urlsafe_b64decode(args.auth.encode("ascii"))
        authkey = unprotect_current_user_bytes(protected_authkey)
        connection = Client(address, family="AF_PIPE", authkey=authkey)
    except Exception:  # The private helper has no UI/error channel before IPC connects.
        return 252
    try:
        request: Any = connection.recv()
    except (EOFError, OSError):
        connection.close()
        return 252
    plugin_argv = request.get("plugin_argv") if isinstance(request, dict) else None
    if not isinstance(plugin_argv, list) or not all(
        isinstance(value, str) for value in plugin_argv
    ):
        with suppress(BrokenPipeError, EOFError, OSError):
            connection.send({"event": "exited", "exit_code": 252})
        connection.close()
        return 252
    try:
        process = subprocess.Popen(plugin_argv, shell=False)  # noqa: S603  # nosec B603
    except OSError:
        with suppress(BrokenPipeError, EOFError, OSError):
            connection.send({"event": "exited", "exit_code": 252})
        connection.close()
        return 252
    previous_sigint_handler = signal.signal(signal.SIGINT, signal.SIG_IGN)
    send_lock = Lock()

    def send_event(message: dict[str, object]) -> None:
        with send_lock:
            connection.send(message)

    try:
        unregister_close = _register_console_close(send_event)
    except OSError:
        _stop_process(process)
        with suppress(BrokenPipeError, EOFError, OSError):
            send_event({"event": "exited", "exit_code": 252})
        connection.close()
        signal.signal(signal.SIGINT, previous_sigint_handler)
        return 252
    try:
        try:
            send_event({"event": "started", "process_id": process.pid})
        except (BrokenPipeError, EOFError, OSError):
            _stop_process(process)
            connection.close()
            return 252
        connected = True
        while process.poll() is None:
            has_command = False
            if connected:
                try:
                    has_command = connection.poll(0.1)
                except (EOFError, OSError):
                    connected = False
            if connected and has_command:
                try:
                    command = connection.recv()
                except (EOFError, OSError):
                    connected = False
                    continue
                action = command.get("command") if isinstance(command, dict) else None
                if action == "terminate":
                    with suppress(OSError):
                        process.terminate()
                elif action == "kill":
                    with suppress(OSError):
                        process.kill()
            else:
                Event().wait(0.05)
    finally:
        unregister_close()
        signal.signal(signal.SIGINT, previous_sigint_handler)
    exit_code = int(process.returncode or 0)
    if connected:
        with suppress(BrokenPipeError, EOFError, OSError):
            connection.send({"event": "exited", "exit_code": exit_code})
    connection.close()
    return exit_code


def _register_console_close(send: Callable[[dict[str, object]], None]) -> Callable[[], None]:
    """Report an explicit Windows console close; pipe EOF alone remains a failure."""
    if os.name != "nt":
        return lambda: None
    handler_type = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_ulong)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    register = kernel.SetConsoleCtrlHandler
    register.argtypes = [handler_type, ctypes.c_int]
    register.restype = ctypes.c_int

    def on_control(event: int) -> int:
        if event == 2:  # CTRL_CLOSE_EVENT, not an arbitrary plugin exit or lost pipe.
            with suppress(BrokenPipeError, EOFError, OSError):
                send({"event": "console_closed"})
        return 0  # Preserve the system's default console shutdown.

    callback = handler_type(on_control)
    if not register(callback, 1):
        raise ctypes.WinError(ctypes.get_last_error())

    def unregister() -> None:
        register(callback, 0)  # Closure retains the callback for its entire native lifetime.

    return unregister


def _stop_process(process: subprocess.Popen[bytes]) -> None:
    """Best-effort stop when ownership was not transferred to the parent process."""

    with suppress(OSError):
        process.terminate()
    try:
        process.wait(timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        with suppress(OSError):
            process.kill()
        with suppress(OSError, subprocess.TimeoutExpired):
            process.wait(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
