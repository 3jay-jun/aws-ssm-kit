"""Shared labeled form controls used by connection editors."""

from PySide6.QtWidgets import QHBoxLayout, QLabel, QToolButton, QToolTip, QVBoxLayout, QWidget

from aws_connect.presentation.gui.icons import set_button_icon


def form_field(label: str, control: QWidget, *, help_text: str | None = None) -> QWidget:
    container = QWidget()
    container.setObjectName("field")
    layout = QVBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(6)
    caption = QLabel(label)
    caption.setObjectName("field_label")
    if help_text is None:
        layout.addWidget(caption)
    else:
        caption_row = QHBoxLayout()
        caption_row.addWidget(caption)
        help_icon = QToolButton()
        help_icon.setObjectName("field_help")
        set_button_icon(help_icon, "common-info.svg", tooltip=help_text, color="#7b88a5", size=16)
        help_icon.clicked.connect(
            lambda: QToolTip.showText(
                help_icon.mapToGlobal(help_icon.rect().bottomLeft()), help_icon.toolTip(), help_icon
            )
        )
        caption_row.addWidget(help_icon)
        caption_row.addStretch()
        layout.addLayout(caption_row)
    layout.addWidget(control)
    return container
