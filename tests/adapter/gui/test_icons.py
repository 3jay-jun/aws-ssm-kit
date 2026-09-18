from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSize
from PySide6.QtWidgets import QApplication, QPushButton

from aws_connect.presentation.gui.icons import gui_asset_path, gui_icon, set_button_icon


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_supplied_gui_assets_exist_and_load_through_packaged_lookup() -> None:
    _app()
    filenames = (
        "common-clipboard.svg",
        "common-copy.svg",
        "common-delete.svg",
        "common-profile.svg",
        "common-refresh.svg",
        "common-save.svg",
        "common-search.svg",
        "common-start.svg",
        "common-stop.svg",
        "dashboard-move.svg",
        "ec2-terminal.svg",
        "file-folder.svg",
        "folder-plus-solid-full.svg",
        "clock-solid-full.svg",
        "profile-create.svg",
        "rds-create.svg",
        "s3-download.svg",
        "s3-upload.svg",
        "tab-dashboard.svg",
        "tab-ec2.svg",
        "tab-log.svg",
        "tab-rds.svg",
        "tab-s3.svg",
        "tab-secrets.svg",
        "logo.ico",
        "logo.png",
    )

    for filename in filenames:
        assert Path(gui_asset_path(filename)).is_file()
        assert not gui_icon(filename).isNull()


def test_button_icon_helper_applies_shared_size_tooltip_and_accessible_name() -> None:
    _app()
    button = QPushButton()

    set_button_icon(button, "common-refresh.svg", tooltip="새로고침")

    assert not button.icon().isNull()
    assert button.iconSize() == QSize(18, 18)
    assert button.toolTip() == "새로고침"
    assert button.accessibleName() == "새로고침"
