"""Private Windows Terminal host for one Session Manager Plugin process."""

from __future__ import annotations

import argparse
import signal
import subprocess  # nosec B404
from base64 import urlsafe_b64decode
from collections.abc import Sequence
from contextlib import suppress
from multiprocessing.connection import Client
from threading import Event
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
    try:
        try:
            connection.send({"event": "started", "process_id": process.pid})
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
        signal.signal(signal.SIGINT, previous_sigint_handler)
    exit_code = int(process.returncode or 0)
    if connected:
        with suppress(BrokenPipeError, EOFError, OSError):
            connection.send({"event": "exited", "exit_code": exit_code})
    connection.close()
    return exit_code


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
