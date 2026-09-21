from pathlib import Path
from unittest.mock import Mock

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)
from tests.adapter.gui.test_logs import ImmediateTaskRunner, _app

from aws_connect.domain.app_settings import AppSettings, LogLevel
from aws_connect.presentation.gui.page_layout import PAGE_MARGINS, PAGE_SPACING
from aws_connect.presentation.gui.styles import APP_STYLE
from aws_connect.presentation.gui.typography import BODY_FONT_SIZE, PAGE_TITLE_FONT_SIZE
from aws_connect.presentation.gui.window import MainWindow


def test_all_six_tabs_share_page_margins_heading_size_and_position(tmp_path: Path) -> None:
    app = _app()
    activity = Mock()
    activity.list_recent.return_value = []
    activity.recent.return_value = []
    settings = Mock()
    settings.get.return_value = AppSettings(tmp_path, LogLevel.INFO)
    window = MainWindow(
        Mock(),
        Mock(),
        Mock(),
        ec2=Mock(),
        tunnel_sessions=Mock(),
        rds_tunnels=Mock(),
        secrets=Mock(),
        s3_locations=Mock(),
        s3=Mock(),
        settings=settings,
        activity_logs=activity,
        diagnostic_logs=Mock(),
        connection_lifecycle=Mock(),
        task_runner=ImmediateTaskRunner(),
        auto_start=False,
    )
    window.show()
    for width, height in ((1424, 894), (1024, 720)):
        window.resize(width, height)
        for index in range(6):
            window.pages.setCurrentIndex(index)
            app.processEvents()
            page = window.pages.widget(index)
            title = page.findChild(QLabel, "page_title")
            subtitle = page.findChild(QLabel, "page_subtitle")
            content = title.parentWidget()
            margins = content.layout().contentsMargins()
            assert (
                margins.left(),
                margins.top(),
                margins.right(),
                margins.bottom(),
            ) == PAGE_MARGINS
            assert content.layout().spacing() == PAGE_SPACING
            assert title.font().pixelSize() == PAGE_TITLE_FONT_SIZE
            assert subtitle.font().pixelSize() == BODY_FONT_SIZE
            assert title.x() == PAGE_MARGINS[0]
            assert title.y() == PAGE_MARGINS[1]
    window._allow_close = True
    window.close()


def test_checkbox_and_table_indicators_use_same_blue_and_white_check() -> None:
    app = _app()
    parent = QWidget()
    parent.setStyleSheet(APP_STYLE)
    for object_name in (
        "profile_mfa_enabled",
        "ec2_favorites_only",
        "secret_consent",
        "logs_errors_only",
    ):
        check = QCheckBox(parent)
        check.setObjectName(object_name)
        check.setChecked(True)
        check.resize(24, 24)
        check.show()
        image = check.grab().toImage()
        colors = {
            image.pixelColor(x, y).name()
            for x in range(image.width())
            for y in range(image.height())
        }
        assert "#2563eb" in colors
        assert "#ffffff" in colors
        check.click()
        assert not check.isChecked()
        check.close()
    table = QTableWidget(1, 1, parent)
    item = QTableWidgetItem()
    item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
    item.setCheckState(Qt.CheckState.Checked)
    table.setItem(0, 0, item)
    table.resize(180, 120)
    table.show()
    app.processEvents()
    image = table.viewport().grab().toImage()
    colors = {
        image.pixelColor(x, y).name() for x in range(image.width()) for y in range(image.height())
    }
    assert "#2563eb" in colors
    assert item.checkState() == Qt.CheckState.Checked
    parent.close()
