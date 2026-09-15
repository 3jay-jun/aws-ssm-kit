import socket

from aws_connect.infrastructure.local_port_checker import SocketLocalPortChecker


def test_reports_bound_and_available_loopback_ports() -> None:
    checker = SocketLocalPortChecker()
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    port = int(listener.getsockname()[1])
    try:
        assert not checker.is_available(port)
    finally:
        listener.close()
    assert checker.is_available(port)
