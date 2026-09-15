"""Packaged GUI icon lookup; callers never depend on user download paths."""

from pathlib import Path

from PySide6.QtGui import QIcon

_ASSET_DIRECTORY = Path(__file__).with_name("assets")


def gui_icon(filename: str) -> QIcon:
    """Load one repository-owned SVG icon from the packaged GUI asset directory."""

    return QIcon(str(_ASSET_DIRECTORY / filename))


def gui_asset_path(filename: str) -> str:
    """Return a Qt stylesheet-safe path for one packaged GUI asset."""

    return (_ASSET_DIRECTORY / filename).as_posix()
