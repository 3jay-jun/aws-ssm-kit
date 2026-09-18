"""Packaged GUI icon lookup; callers never depend on user download paths."""

from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter
from PySide6.QtWidgets import QAbstractButton, QHBoxLayout, QLabel, QWidget

from aws_connect.presentation.gui.view_models import LogBadgeViewModel

_ASSET_DIRECTORY = Path(__file__).with_name("assets")
ACTION_ICON_SIZE = 18
NAVIGATION_ICON_SIZE = 20
HEADER_ICON_SIZE = 21


def gui_icon(filename: str, *, color: str | None = None, size: int = 18) -> QIcon:
    """Load one repository-owned SVG icon from the packaged GUI asset directory."""

    icon = QIcon(str(_ASSET_DIRECTORY / filename))
    if color is not None:
        pixmap = icon.pixmap(QSize(size * 2, size * 2))
        painter = QPainter(pixmap)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
        painter.fillRect(pixmap.rect(), QColor(color))
        painter.end()
        pixmap.setDevicePixelRatio(2)
        icon = QIcon(pixmap)
    return icon


def gui_asset_path(filename: str) -> str:
    """Return a Qt stylesheet-safe path for one packaged GUI asset."""

    return (_ASSET_DIRECTORY / filename).as_posix()


def set_button_icon(
    button: QAbstractButton,
    filename: str,
    *,
    size: int = ACTION_ICON_SIZE,
    tooltip: str | None = None,
    accessible_name: str | None = None,
    color: str | None = None,
) -> None:
    """Apply one repository icon with the shared button sizing and accessibility policy."""

    icon = gui_icon(filename, color=color, size=size)
    button.setIcon(icon)
    button.setIconSize(QSize(size, size))
    if tooltip is not None:
        button.setToolTip(tooltip)
    if accessible_name is not None or tooltip is not None:
        button.setAccessibleName(accessible_name or tooltip or "")


def icon_text(icon_name: str, text: str, *, color: str | None = None) -> QWidget:
    """Shared SVG and plain-text row for status badges and feature labels."""
    widget = QWidget()
    widget.setObjectName("icon_text")
    layout = QHBoxLayout(widget)
    layout.setContentsMargins(10, 0, 10, 0)
    layout.setSpacing(8)
    icon = QLabel()
    icon.setPixmap(gui_icon(icon_name, color=color).pixmap(18, 18))
    label = QLabel(text)
    label.setObjectName("icon_text_label")
    label.setTextFormat(Qt.TextFormat.PlainText)
    layout.addWidget(icon)
    layout.addWidget(label)
    layout.addStretch()
    return widget


def status_badge(badge: "LogBadgeViewModel") -> QWidget:
    """One icon/text/color projection for execution history surfaces."""
    widget = icon_text(badge.icon or "", badge.text, color=badge.color)
    widget.setProperty("status", badge.key)
    label = widget.findChild(QLabel, "icon_text_label")
    if label is not None:
        label.setStyleSheet(f"color: {badge.color};")
    return widget
