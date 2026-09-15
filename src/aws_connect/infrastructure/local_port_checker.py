"""Loopback port availability adapter."""

from __future__ import annotations

import socket


class SocketLocalPortChecker:
    """Probe a TCP port without retaining ownership after the check."""

    def is_available(self, port: int) -> bool:
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
        finally:
            probe.close()
        return True
