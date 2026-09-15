import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from aws_connect.application.ports import PluginDiagnostic
from aws_connect.application.system_info import (
    DatabaseDiagnostic,
    DataProtectionDiagnostic,
    DoctorService,
    LogDiagnostic,
    ProfileDiagnostic,
    RuntimeDiagnostics,
    SessionHostDiagnostic,
)
from aws_connect.cli_main import main


def test_doctor_json_uses_composed_application_service(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    probe = Mock()
    probe.inspect.return_value = RuntimeDiagnostics(
        DatabaseDiagnostic(Path("aws_connect.db"), True, True, 3, 3),
        DataProtectionDiagnostic(True),
        LogDiagnostic(Path("logs"), "INFO", True),
        PluginDiagnostic(Path("session-manager-plugin.exe"), True, "1.2.707.0", True, True),
        SessionHostDiagnostic(
            Path("aws_connect_session_host.exe"), "sibling_executable", True, True
        ),
        ProfileDiagnostic(0, None, "not_configured", None, "not_configured"),
    )
    monkeypatch.setattr("aws_connect.cli_main.build_doctor_service", lambda: DoctorService(probe))
    assert main(["doctor", "--output", "json"]) == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["application_name"] == "AWS Connect"
    assert payload["status"] == "ready"
    assert payload["session_manager_plugin"]["version"] == "1.2.707.0"
    assert payload["database"]["migration_version"] == 3
    assert payload["data_protection"]["available"] is True
    assert payload["logging"]["writable"] is True
    assert payload["profile"]["session_state"] == "not_configured"
