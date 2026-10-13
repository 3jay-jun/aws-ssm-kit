"""Shared offline runtime controls for mount and diagnostic pages."""

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMessageBox, QPushButton, QWidget

from aws_connect.application.winfsp_runtime import (
    InstallResult,
    RuntimeState,
    WinFspRuntimeService,
    WinFspStatus,
)
from aws_connect.domain.errors import ApplicationError
from aws_connect.presentation.gui.tasks import GuiTaskRunner

_LABELS = {
    RuntimeState.READY: "WinFsp 사용 가능",
    RuntimeState.MISSING: "S3 마운트를 사용하려면 WinFsp를 설치하세요.",
    RuntimeState.REPAIR_REQUIRED: "WinFsp 업데이트 또는 복구가 필요합니다.",
    RuntimeState.UNSUPPORTED: "지원하지 않는 WinFsp 버전입니다. 공식 설치 상태를 확인하세요.",
    RuntimeState.REBOOT_REQUIRED: "WinFsp 설치를 완료하려면 Windows를 재부팅하세요.",
}
_RESULTS = {
    "failed": "설치에 실패했습니다. 환경 진단 후 재시도하세요.",
    "cancelled": "설치를 취소했습니다. 다른 기능은 계속 사용할 수 있습니다.",
    "busy": "다른 설치 또는 마운트가 진행 중입니다. 완료 후 재시도하세요.",
    "pending": "설치가 진행 중입니다. 중복 설치하지 말고 완료를 기다려 주세요.",
}


class WinFspSetupWidget(QWidget):
    error_raised = Signal(object)
    readiness_changed = Signal(bool)

    def __init__(self, service: WinFspRuntimeService, runner: GuiTaskRunner) -> None:
        super().__init__()
        self._service, self._runner = service, runner
        self._busy = False
        self._status = WinFspStatus(RuntimeState.MISSING)
        self.label = QLabel("WinFsp 설치 상태를 확인하세요.")
        self.label.setObjectName("helper_text")
        self.label.setWordWrap(True)
        self.install_button = QPushButton("WinFsp 설치")
        self.check_button = QPushButton("환경 진단")
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.label, 1)
        row.addWidget(self.check_button)
        row.addWidget(self.install_button)
        self.check_button.clicked.connect(self.refresh)
        self.install_button.clicked.connect(self.install)
        QTimer.singleShot(0, self.refresh)

    def refresh(self) -> None:
        if not self._busy:
            self._runner.submit(self._service.inspect, self._loaded, self._failed)

    def _loaded(self, status: WinFspStatus) -> None:
        self._status = status
        self.label.setText(_LABELS[status.state])
        self.install_button.setText(
            "업데이트 / 복구" if status.state is RuntimeState.REPAIR_REQUIRED else "WinFsp 설치"
        )
        self.install_button.setEnabled(
            status.state in (RuntimeState.MISSING, RuntimeState.REPAIR_REQUIRED)
        )
        self.readiness_changed.emit(status.ready)

    def install(self) -> None:
        if self._busy:
            return
        replace = self._status.state is RuntimeState.REPAIR_REQUIRED
        message = (
            "다른 프로그램과 공유하는 WinFsp 드라이버를 교체/복구합니다. 사용 중인 마운트와 다른 "
            "프로그램을 닫아 주세요. 재부팅이 필요할 수 있습니다. 계속할까요?"
            if replace
            else "WinFsp를 설치합니다. Windows 관리자 승인이 필요합니다. 계속할까요?"
        )
        if QMessageBox.question(self, "WinFsp 설치", message) != QMessageBox.StandardButton.Yes:
            return
        self._busy = True
        self.check_button.setEnabled(False)
        self.install_button.setEnabled(False)
        self.label.setText("WinFsp 설치 중… 관리자 승인 창을 확인하세요.")
        self.readiness_changed.emit(False)
        QTimer.singleShot(900000, self._pending)
        self._runner.submit(
            lambda: self._service.install(confirm_replace=replace), self._installed, self._failed
        )

    def _pending(self) -> None:
        if self._busy:
            self.label.setText(_RESULTS["pending"])

    def _installed(self, result: InstallResult) -> None:
        self._busy = False
        self.check_button.setEnabled(True)
        self._loaded(result.status)
        if result.outcome in _RESULTS:
            self.label.setText(_RESULTS[result.outcome])

    def _failed(self, error: ApplicationError) -> None:
        self._busy = False
        self.check_button.setEnabled(True)
        self.label.setText("WinFsp 상태 확인 또는 설치에 실패했습니다. 환경 진단으로 재시도하세요.")
        self.readiness_changed.emit(False)
        self.error_raised.emit(error)
