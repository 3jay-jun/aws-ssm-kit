"""Dashboard presentation: service DTOs only, no feature-widget state."""

from dataclasses import replace
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from aws_connect.application.activity_log_service import ActivityLogService, execution_entry
from aws_connect.application.dashboard_service import (
    PERMISSIONS,
    CapabilityState,
    DashboardPermissions,
    DashboardService,
    FeatureCapability,
    PermissionState,
)
from aws_connect.domain.errors import ApplicationError
from aws_connect.presentation.gui.icons import gui_icon, set_button_icon, status_badge
from aws_connect.presentation.gui.tasks import GuiTaskRunner
from aws_connect.presentation.gui.view_models import (
    LogBadgeViewModel,
    build_activity_log_row_view_model,
    log_result_view_model,
)

_CAPABILITY_BADGES = {
    CapabilityState.AVAILABLE: replace(log_result_view_model("SUCCESS"), text="사용 가능"),
    CapabilityState.PARTIAL: replace(log_result_view_model("WARNING"), text="일부 가능"),
    CapabilityState.UNAVAILABLE: replace(log_result_view_model("FAILURE"), text="사용 불가"),
    CapabilityState.UNKNOWN: LogBadgeViewModel("unknown", "확인 필요", "common-info.svg", "#667085"),
}
_PERMISSION_BADGES = {
    PermissionState.ALLOWED: log_result_view_model("SUCCESS"),
    PermissionState.DENIED: log_result_view_model("FAILURE"),
    PermissionState.UNKNOWN: _CAPABILITY_BADGES[CapabilityState.UNKNOWN],
}


class FeatureCard(QFrame):
    activated = Signal(str)

    def __init__(self, route: str, title: str, description: str, action: str) -> None:
        super().__init__()
        self.setObjectName("feature_card")
        self.setProperty("feature", route)
        self.setMinimumWidth(235)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 16, 14, 14)
        layout.setSpacing(10)
        top = QHBoxLayout()
        icon = QLabel()
        icon.setPixmap(gui_icon(f"tab-{route}.svg").pixmap(30, 30))
        top.addWidget(icon)
        top.addStretch()
        self._badge_layout = top
        self._badge = status_badge(_CAPABILITY_BADGES[CapabilityState.UNKNOWN])
        top.addWidget(self._badge)
        layout.addLayout(top)
        heading = QLabel(title)
        heading.setObjectName("feature_heading")
        layout.addWidget(heading)
        copy = QLabel(description)
        copy.setObjectName("feature_description")
        copy.setWordWrap(True)
        copy.setMinimumHeight(52)
        layout.addWidget(copy)
        caption = QLabel("권한 상태")
        caption.setObjectName("field_label")
        layout.addWidget(caption)
        self._permissions: dict[str, tuple[QLabel, QLabel]] = {}
        permissions = QGridLayout()
        permissions.setVerticalSpacing(10)
        for index, definition in enumerate(PERMISSIONS[route]):
            state_icon = QLabel()
            state_icon.setFixedSize(18, 18)
            label = QLabel(definition.label)
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setWordWrap(True)
            permissions.addWidget(state_icon, index, 0, Qt.AlignmentFlag.AlignTop)
            permissions.addWidget(label, index, 1)
            self._permissions[definition.key] = (state_icon, label)
        permissions.setColumnStretch(1, 1)
        layout.addLayout(permissions)
        layout.addStretch()
        button = QPushButton(action)
        button.setObjectName(f"dashboard_{route}_button")
        set_button_icon(button, "dashboard-move.svg")
        button.clicked.connect(lambda: self.activated.emit(route))
        layout.addWidget(button)
        self.apply_capability(None)

    def apply_capability(self, capability: FeatureCapability | None) -> None:
        badge = status_badge(
            _CAPABILITY_BADGES[capability.state if capability else CapabilityState.UNKNOWN]
        )
        self._badge_layout.replaceWidget(self._badge, badge)
        self._badge.deleteLater()
        self._badge = badge
        observations = {row.key: row for row in capability.permissions} if capability else {}
        for key, (icon, label) in self._permissions.items():
            observation = observations.get(key)
            state = observation.state if observation else PermissionState.UNKNOWN
            badge = _PERMISSION_BADGES[state]
            icon.setPixmap(gui_icon(badge.icon or "", color=badge.color).pixmap(18, 18))
            icon.setAccessibleName(state.value)
            label.setToolTip(
                observation.explanation if observation else "현재 프로필의 권한 상태를 확인하세요."
            )


class DashboardPage(QScrollArea):
    activated = Signal(str)
    error_raised = Signal(object)

    def __init__(
        self,
        service: DashboardService | None,
        activity: ActivityLogService | None,
        runner: GuiTaskRunner,
    ) -> None:
        super().__init__()
        self._service = service
        self._activity = activity
        self._runner = runner
        self._profile_id: int | None = None
        self._generation = 0
        self._recent_generation = 0
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setWidgetResizable(True)
        content = QWidget()
        content.setObjectName("dashboard")
        self.setWidget(content)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(24, 22, 24, 24)
        layout.setSpacing(18)
        head = QHBoxLayout()
        copy = QVBoxLayout()
        title = QLabel("대시보드")
        title.setObjectName("page_title")
        copy.addWidget(title)
        subtitle = QLabel("현재 AWS 프로필로 사용할 수 있는 기능과 최근 작업 현황을 확인합니다.")
        subtitle.setObjectName("page_subtitle")
        copy.addWidget(subtitle)
        head.addLayout(copy, 1)
        actions = QVBoxLayout()
        self.refresh_button = QPushButton("권한 상태 새로 확인")
        self.refresh_button.setObjectName("dashboard_permissions_refresh")
        set_button_icon(self.refresh_button, "common-refresh.svg")
        self.refresh_button.setToolTip(
            "안전한 목록 조회와 최근 실행 결과를 확인합니다. 대상별 권한은 다를 수 있습니다."
        )
        self.refresh_button.clicked.connect(self.refresh_permissions)
        self.refresh_button.setEnabled(False)
        actions.addWidget(self.refresh_button)
        self.checked_at = QLabel("마지막 확인: -")
        self.checked_at.setAlignment(Qt.AlignmentFlag.AlignRight)
        actions.addWidget(self.checked_at)
        head.addLayout(actions)
        layout.addLayout(head)
        cards = QHBoxLayout()
        cards.setSpacing(14)
        self.cards: dict[str, FeatureCard] = {}
        for route, card_title, description, action in (
            ("ec2", "EC2 접속", "EC2 인스턴스에 SSM으로 접속하는 기능입니다.", "EC2 접속하기"),
            (
                "rds",
                "RDS 터널",
                "SSM 포트포워딩을 통해 RDS에 연결하는 기능입니다.",
                "RDS 터널 열기",
            ),
            (
                "secrets",
                "Secrets Manager",
                "Secrets를 조회하고 안전하게 관리하는 기능입니다.",
                "Secrets 열기",
            ),
            ("s3", "S3 파일", "S3 버킷을 탐색하고 파일을 업로드하는 기능입니다.", "S3 파일 열기"),
        ):
            card = FeatureCard(route, card_title, description, action)
            card.activated.connect(self.activated)
            cards.addWidget(card, 1)
            self.cards[route] = card
        layout.addLayout(cards, 1)
        recent_card = QFrame()
        recent_card.setProperty("role", "card")
        recent_layout = QVBoxLayout(recent_card)
        recent_layout.setContentsMargins(16, 14, 16, 14)
        recent_head = QHBoxLayout()
        recent_copy = QVBoxLayout()
        recent_title = QLabel("최근 작업")
        recent_title.setObjectName("section_title")
        recent_copy.addWidget(recent_title)
        recent_copy.addWidget(QLabel("최근 실행한 작업 이력을 표시합니다."))
        recent_head.addLayout(recent_copy)
        recent_head.addStretch()
        logs_button = QPushButton("실행 로그 전체 보기 →")
        logs_button.setObjectName("dashboard_logs_button")
        logs_button.clicked.connect(lambda: self.activated.emit("logs"))
        recent_head.addWidget(logs_button)
        recent_layout.addLayout(recent_head)
        self.recent = QTableWidget(0, 6)
        self.recent.setObjectName("dashboard_recent_logs")
        self.recent.setHorizontalHeaderLabels(("시간", "결과", "기능", "작업", "대상", "내용"))
        self.recent.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.recent.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.recent.verticalHeader().hide()
        self.recent.verticalHeader().setDefaultSectionSize(34)
        self.recent.setMinimumHeight(215)
        header = self.recent.horizontalHeader()
        for column, width in ((0, 170), (1, 100), (2, 100), (3, 125)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
            header.resizeSection(column, width)
        for column in (4, 5):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Stretch)
        recent_layout.addWidget(self.recent)
        layout.addWidget(recent_card)

    def set_profile(self, profile_id: int | None) -> None:
        self._generation += 1
        self._profile_id = profile_id
        self.checked_at.setText("마지막 확인: -")
        for card in self.cards.values():
            card.apply_capability(None)
        self.refresh_button.setEnabled(profile_id is not None and self._service is not None)

    def refresh_permissions(self) -> None:
        service, profile_id = self._service, self._profile_id
        if service is None or profile_id is None:
            return
        self._generation += 1
        generation = self._generation
        self.refresh_button.setEnabled(False)
        self._runner.submit(
            lambda: service.check_permissions(profile_id),
            lambda value: self._permissions_loaded(value, generation),
            lambda error: self._permissions_failed(error, generation),
        )

    def _permissions_loaded(self, result: DashboardPermissions, generation: int) -> None:
        if generation != self._generation or result.profile_id != self._profile_id:
            return
        for feature in result.features:
            self.cards[feature.feature].apply_capability(feature)
        self.checked_at.setText(
            "마지막 확인: " + result.checked_at.astimezone().strftime("%Y-%m-%d %H:%M:%S")
        )
        self.refresh_button.setEnabled(True)

    def _permissions_failed(self, error: ApplicationError, generation: int) -> None:
        if generation != self._generation:
            return
        self.set_profile(self._profile_id)
        self.error_raised.emit(error)

    def refresh_recent(self) -> None:
        activity = self._activity
        if activity is None:
            return
        self._recent_generation += 1
        generation = self._recent_generation
        self._runner.submit(
            lambda: activity.list_recent(5),
            lambda entries: self._recent_loaded(entries, generation),
            self.error_raised.emit,
        )

    def _recent_loaded(self, events: Any, generation: int) -> None:
        if generation != self._recent_generation:
            return
        self.recent.setRowCount(0)
        for index, event in enumerate(events[:5]):
            row = build_activity_log_row_view_model(execution_entry(event))
            self.recent.insertRow(index)
            for column, text in (
                (0, row.occurred_at),
                (2, row.feature.text),
                (3, row.operation),
                (4, row.target),
                (5, row.message),
            ):
                item = QTableWidgetItem(text)
                item.setToolTip(text)
                self.recent.setItem(index, column, item)
            self.recent.setCellWidget(index, 1, status_badge(row.result))
