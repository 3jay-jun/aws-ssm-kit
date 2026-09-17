from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRect
from PySide6.QtGui import QColor, QPainter, QPixmap, QStandardItemModel
from PySide6.QtWidgets import QApplication, QStyle, QStyleOptionViewItem, QTableWidget

from aws_connect.presentation.gui.styles import APP_STYLE
from aws_connect.presentation.gui.table_selection import FirstColumnSelectionDelegate


def test_selected_row_background_has_only_one_first_column_bar() -> None:
    app = QApplication.instance() or QApplication([])
    table = QTableWidget()
    table.setStyleSheet(APP_STYLE)
    delegate = FirstColumnSelectionDelegate(table)
    model = QStandardItemModel(1, 2)
    canvas = QPixmap(80, 30)
    canvas.fill(QColor("white"))
    painter = QPainter(canvas)

    for column in range(2):
        option = QStyleOptionViewItem()
        option.widget = table
        option.rect = QRect(column * 40, 0, 40, 30)
        option.state = QStyle.StateFlag.State_Enabled | QStyle.StateFlag.State_Selected
        delegate.paint(painter, option, model.index(0, column))
    painter.end()
    app.processEvents()

    image = canvas.toImage()
    assert image.pixelColor(0, 15) == QColor("#2563eb")
    assert image.pixelColor(40, 15) == QColor("#eff6ff")
