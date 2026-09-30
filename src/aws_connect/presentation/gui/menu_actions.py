"""Shared styled actions for feature row menus."""

from PySide6.QtWidgets import QMenu, QPushButton, QWidgetAction

from aws_connect.presentation.gui.icons import set_button_icon


def menu_action(menu: QMenu, text: str, icon: str, *, danger: bool = False) -> QWidgetAction:
    action = QWidgetAction(menu)
    action.setText(text)
    button = QPushButton(text)
    button.setObjectName("s3_menu_item")
    button.setProperty("danger", danger)
    set_button_icon(button, icon, color="#ff2638" if danger else None, size=18)
    action.setDefaultWidget(button)
    button.clicked.connect(action.trigger)
    button.clicked.connect(menu.close)

    def sync() -> None:
        button.setEnabled(action.isEnabled())
        button.setCheckable(action.isCheckable())
        button.setChecked(action.isChecked())

    action.changed.connect(sync)
    menu.addAction(action)
    return action
