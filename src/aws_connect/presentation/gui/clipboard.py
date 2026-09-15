"""Central, conditional clipboard clearing for explicitly copied values."""

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

CLIPBOARD_CLEAR_MILLISECONDS = 30_000


def copy_temporarily(value: str) -> None:
    """Copy a value and clear it only if it is still the copied value after 30 seconds."""

    clipboard = QApplication.clipboard()
    clipboard.setText(value)
    QTimer.singleShot(
        CLIPBOARD_CLEAR_MILLISECONDS,
        lambda expected=value: clipboard.clear() if clipboard.text() == expected else None,
    )
