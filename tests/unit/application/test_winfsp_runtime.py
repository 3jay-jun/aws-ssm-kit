from unittest.mock import Mock

import pytest

from aws_connect.application.winfsp_runtime import RuntimeState, WinFspRuntimeService, WinFspStatus
from aws_connect.domain.errors import ConfigurationError


@pytest.mark.parametrize(
    "code,outcome",
    [
        (0, "installed"),
        (3010, "reboot_required"),
        (1641, "reboot_required"),
        (1223, "cancelled"),
        (1602, "cancelled"),
        (1618, "busy"),
        (258, "pending"),
        (1603, "failed"),
    ],
)
def test_install_results_reinspect_without_hiding_failure(code, outcome):
    adapter = Mock()
    adapter.inspect.side_effect = [
        WinFspStatus(RuntimeState.MISSING),
        WinFspStatus(RuntimeState.READY),
    ]
    adapter.install.return_value = code
    result = WinFspRuntimeService(adapter).install()
    assert result.outcome == outcome
    assert result.installer_code == code
    adapter.install.assert_called_once_with(repair=False)
    assert adapter.remember_reboot.called == (code in (3010, 1641))


def test_replacement_requires_confirmation_and_failure_releases_gate():
    adapter = Mock()
    adapter.inspect.return_value = WinFspStatus(RuntimeState.REPAIR_REQUIRED, "2.0.1")
    service = WinFspRuntimeService(adapter)
    with pytest.raises(ConfigurationError, match="confirm_required"):
        service.install()
    adapter.install.assert_not_called()
    adapter.install.side_effect = ConfigurationError(
        "runtime.winfsp.verification_failed", "bad payload"
    )
    with pytest.raises(ConfigurationError, match="verification_failed"):
        service.install(confirm_replace=True)
    adapter.install.side_effect = None
    adapter.install.return_value = 1602
    assert service.install(confirm_replace=True).outcome == "cancelled"


@pytest.mark.parametrize(
    "state", [RuntimeState.READY, RuntimeState.REBOOT_REQUIRED, RuntimeState.UNSUPPORTED]
)
def test_ready_reboot_and_unsupported_never_install(state):
    adapter = Mock()
    adapter.inspect.return_value = WinFspStatus(state)
    service = WinFspRuntimeService(adapter)
    if state is RuntimeState.UNSUPPORTED:
        with pytest.raises(ConfigurationError, match="unsupported"):
            service.install()
    else:
        assert service.install().outcome == state
    adapter.install.assert_not_called()


def test_installer_success_with_unready_runtime_is_failure():
    adapter = Mock()
    adapter.inspect.return_value = WinFspStatus(RuntimeState.MISSING)
    adapter.install.return_value = 0
    assert WinFspRuntimeService(adapter).install().outcome == "failed"


def test_concurrent_install_does_not_launch_another_msi():
    adapter = Mock()
    service = WinFspRuntimeService(adapter)
    service._lock.acquire()
    try:
        assert service.install().outcome == "busy"
        adapter.install.assert_not_called()
    finally:
        service._lock.release()
