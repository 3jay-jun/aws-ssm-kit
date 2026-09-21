"""Windows Korean typography bootstrap for stable Qt text rendering."""

from pathlib import Path

from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication

BODY_FONT_SIZE = 13
PAGE_TITLE_FONT_SIZE = 25
SECTION_TITLE_FONT_SIZE = 17

_WINDOWS_FONT_DIRECTORY = Path("C:/Windows/Fonts")
_MALGUN_FONT_FILES = ("malgun.ttf", "malgunbd.ttf")


def configure_gui_typography(application: QApplication) -> None:
    """Register the installed Korean system font before applying application QSS."""

    families: list[str] = []
    for filename in _MALGUN_FONT_FILES:
        font_id = QFontDatabase.addApplicationFont(str(_WINDOWS_FONT_DIRECTORY / filename))
        if font_id >= 0:
            families.extend(QFontDatabase.applicationFontFamilies(font_id))
    family = families[0] if families else "Segoe UI"
    font = QFont(family)
    font.setPixelSize(BODY_FONT_SIZE)
    application.setFont(font)
