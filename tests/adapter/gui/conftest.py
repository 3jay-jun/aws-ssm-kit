from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="session")
def qt_application() -> QApplication:
    """Keep one application alive while the GUI test suite owns Qt objects."""
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def isolate_qt_widgets(qt_application: QApplication) -> Iterator[None]:
    """Dispose test windows and their timers even when an assertion fails."""
    yield
    for widget in qt_application.topLevelWidgets():
        widget.deleteLater()
    # These tests do not run app.exec(), so process deferred deletion explicitly.
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
