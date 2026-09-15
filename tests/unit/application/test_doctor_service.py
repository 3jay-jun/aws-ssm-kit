from pathlib import Path

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


class Probe:
    def __init__(self, result: RuntimeDiagnostics) -> None:
        self.result = result

    def inspect(self) -> RuntimeDiagnostics:
        return self.result


def diagnostics(*, plugin_exists: bool = True) -> RuntimeDiagnostics:
    return RuntimeDiagnostics(
        DatabaseDiagnostic(Path("db"), True, True, 3, 3),
        DataProtectionDiagnostic(True),
        LogDiagnostic(Path("logs"), "INFO", True),
        PluginDiagnostic(Path("plugin.exe"), plugin_exists, "1.2.707.0", True, True),
        SessionHostDiagnostic(Path("helper.exe"), "sibling_executable", True, True),
        ProfileDiagnostic(0, None, "not_configured", None, "not_configured"),
    )


def test_doctor_reports_ready_with_all_required_local_runtime_dependencies() -> None:
    system, result = DoctorService(Probe(diagnostics())).get()

    assert system.status == "ready"
    assert result.to_payload()["database"]["ready"] is True  # type: ignore[index]


def test_doctor_reports_degraded_when_bundled_plugin_is_missing() -> None:
    system, _result = DoctorService(Probe(diagnostics(plugin_exists=False))).get()
    assert system.status == "degraded"
