"""Shared WinFsp installation policy, independent of Windows and presentation."""

from dataclasses import dataclass
from enum import StrEnum
from threading import Lock
from typing import Protocol

from aws_connect.domain.errors import ConfigurationError


class RuntimeState(StrEnum):
    READY = "ready"
    MISSING = "missing"
    REPAIR_REQUIRED = "repair_required"
    UNSUPPORTED = "unsupported"
    REBOOT_REQUIRED = "reboot_required"


@dataclass(frozen=True, slots=True)
class WinFspStatus:
    state: RuntimeState
    version: str | None = None

    @property
    def ready(self) -> bool:
        return self.state is RuntimeState.READY


@dataclass(frozen=True, slots=True)
class InstallResult:
    outcome: str
    status: WinFspStatus
    installer_code: int | None = None


class WinFspInstaller(Protocol):
    def inspect(self) -> WinFspStatus: ...
    def install(self, *, repair: bool) -> int: ...
    def remember_reboot(self) -> None: ...


def install_outcome(code: int) -> str:
    if code == 0:
        return "installed"
    if code in (3010, 1641):
        return "reboot_required"
    if code in (1602, 1223):
        return "cancelled"
    if code == 1618:
        return "busy"
    if code == 258:
        return "pending"
    return "failed"


class WinFspRuntimeService:
    def __init__(self, installer: WinFspInstaller) -> None:
        self._installer = installer
        self._lock = Lock()

    def inspect(self) -> WinFspStatus:
        return self._installer.inspect()

    def install(self, *, confirm_replace: bool = False) -> InstallResult:
        if not self._lock.acquire(blocking=False):
            return InstallResult("busy", self.inspect(), 1618)
        try:
            before = self.inspect()
            if before.state in (RuntimeState.READY, RuntimeState.REBOOT_REQUIRED):
                return InstallResult(str(before.state), before)
            if before.state is RuntimeState.UNSUPPORTED:
                raise ConfigurationError("runtime.winfsp.unsupported", "Unsupported WinFsp version")
            repair = before.state is RuntimeState.REPAIR_REQUIRED
            if repair and not confirm_replace:
                raise ConfigurationError(
                    "runtime.winfsp.confirm_required", "Confirm replacement of shared WinFsp"
                )
            code = self._installer.install(repair=repair)
            outcome = install_outcome(code)
            if outcome == "reboot_required":
                self._installer.remember_reboot()
            after = self.inspect()
            if outcome == "installed" and not after.ready:
                outcome = "failed"
            return InstallResult(outcome, after, code)
        finally:
            self._lock.release()
