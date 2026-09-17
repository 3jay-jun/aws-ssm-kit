"""Packaged GUI icon lookup; callers never depend on user download paths."""

from pathlib import Path

from PySide6.QtCore import QSize
from PySide6.QtGui import QColor, QIcon, QPainter
from PySide6.QtWidgets import QAbstractButton

_ASSET_DIRECTORY = Path(__file__).with_name("assets")
ACTION_ICON_SIZE = 18
NAVIGATION_ICON_SIZE = 20
HEADER_ICON_SIZE = 21


def gui_icon(filename: str) -> QIcon:
    """Load one repository-owned SVG icon from the packaged GUI asset directory."""

    return QIcon(str(_ASSET_DIRECTORY / filename))


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

    icon = gui_icon(filename)
    if color is not None:
        pixmap = icon.pixmap(QSize(size * 2, size * 2))
        painter = QPainter(pixmap)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
        painter.fillRect(pixmap.rect(), QColor(color))
        painter.end()
        pixmap.setDevicePixelRatio(2)
        icon = QIcon(pixmap)
    button.setIcon(icon)
    button.setIconSize(QSize(size, size))
    if tooltip is not None:
        button.setToolTip(tooltip)
    if accessible_name is not None or tooltip is not None:
        button.setAccessibleName(accessible_name or tooltip or "")
