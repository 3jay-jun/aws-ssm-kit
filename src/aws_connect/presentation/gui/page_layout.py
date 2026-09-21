"""Shared tab content geometry and heading composition."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout

PAGE_MARGINS = (24, 20, 24, 20)
PAGE_SPACING = 16
HEADING_SPACING = 4


def apply_page_layout(layout: QVBoxLayout) -> None:
    layout.setContentsMargins(*PAGE_MARGINS)
    layout.setSpacing(PAGE_SPACING)


def page_heading(title: str, description: str) -> QVBoxLayout:
    layout = QVBoxLayout()
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(HEADING_SPACING)
    layout.setAlignment(Qt.AlignmentFlag.AlignTop)
    for name, text in (("page_title", title), ("page_subtitle", description)):
        label = QLabel(text)
        label.setObjectName(name)
        label.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(label)
    return layout
