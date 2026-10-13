from unittest.mock import Mock

from aws_connect import cli_main
from aws_connect.application.winfsp_runtime import InstallResult, RuntimeState, WinFspStatus


def test_runtime_dispatch_never_builds_profile_or_doctor_services(monkeypatch, capsys):
    fail = Mock(side_effect=AssertionError("profile initialization forbidden"))
    monkeypatch.setattr(cli_main, "build_application_services", fail)
    monkeypatch.setattr(cli_main, "build_doctor_service", fail)
    service = Mock()
    service.inspect.return_value = WinFspStatus(RuntimeState.MISSING)
    service.install.return_value = InstallResult(
        "cancelled", WinFspStatus(RuntimeState.MISSING), 1223
    )
    monkeypatch.setattr(cli_main, "build_winfsp_runtime_service", lambda: service)
    assert cli_main.main(["runtime", "winfsp", "inspect"]) == 0
    assert '"ready": false' in capsys.readouterr().out
    assert cli_main.main(["runtime", "winfsp", "install"]) == 1602
    fail.assert_not_called()


def test_runtime_service_errors_have_structured_output(monkeypatch, capsys):
    from aws_connect.domain.errors import ConfigurationError

    service = Mock()
    service.install.side_effect = ConfigurationError("runtime.winfsp.payload_missing", "missing")
    monkeypatch.setattr(cli_main, "build_winfsp_runtime_service", lambda: service)
    assert cli_main.main(["runtime", "winfsp", "install"]) != 0
    assert "runtime.winfsp.payload_missing" in capsys.readouterr().err
