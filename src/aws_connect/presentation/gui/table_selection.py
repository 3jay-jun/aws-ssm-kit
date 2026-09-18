"""Shared row-selection painting for data tables."""

from PySide6.QtCore import QModelIndex, QPersistentModelIndex, QRect, QSignalBlocker, Qt
from PySide6.QtGui import QColor, QPainter, QPalette
from PySide6.QtWidgets import (
    QCheckBox,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableWidget,
)


class FirstColumnSelectionDelegate(QStyledItemDelegate):
    """Draw one selection bar per row instead of one focus mark per cell."""

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> None:
        clean_option = QStyleOptionViewItem(option)
        selected = bool(clean_option.state & QStyle.StateFlag.State_Selected)
        clean_option.palette.setColor(QPalette.ColorRole.Highlight, QColor("#eff6ff"))
        clean_option.palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#172033"))
        # Let the stylesheet paint the full selected cell, including its padding.
        # The first-column bar supplies the row focus cue without a native inset.
        clean_option.state &= ~QStyle.StateFlag.State_HasFocus
        super().paint(painter, clean_option, index)
        if selected and index.column() == 0:
            painter.save()
            painter.fillRect(
                QRect(option.rect.left(), option.rect.top(), 3, option.rect.height()),
                QColor("#2563eb"),
            )
            painter.restore()


def use_first_column_selection_bar(table: QTableWidget) -> None:
    """Install the repository-wide row selection visual on one table."""

    table.setItemDelegate(FirstColumnSelectionDelegate(table))


def add_check_all_header(table: QTableWidget) -> QCheckBox:
    """Toggle only visible first-column checkboxes; keep filtered rows untouched."""
    header = table.horizontalHeader()
    check = QCheckBox(header.viewport())
    check.setAccessibleName("표시된 항목 전체 선택")

    def position() -> None:
        check.setGeometry(header.sectionViewportPosition(0) + 7, 8, 20, 22)

    def toggle(checked: bool) -> None:
        for row in range(table.rowCount()):
            item = table.item(row, 0)
            if item is not None and not table.isRowHidden(row):
                item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)

    def sync() -> None:
        items = [
            table.item(row, 0) for row in range(table.rowCount()) if not table.isRowHidden(row)
        ]
        with QSignalBlocker(check):
            check.setChecked(
                bool(items)
                and all(
                    item is not None and item.checkState() == Qt.CheckState.Checked
                    for item in items
                )
            )

    check.clicked.connect(toggle)
    table.itemChanged.connect(sync)
    table.model().rowsRemoved.connect(sync)
    header.sectionResized.connect(position)
    header.geometriesChanged.connect(position)
    table.horizontalScrollBar().valueChanged.connect(position)
    position()
    return check
