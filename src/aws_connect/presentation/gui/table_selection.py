"""Shared row-selection painting for data tables."""

from PySide6.QtCore import QModelIndex, QPersistentModelIndex, QRect
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QStyle, QStyledItemDelegate, QStyleOptionViewItem, QTableWidget


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
        if selected:
            painter.fillRect(option.rect, QColor("#eff6ff"))
        clean_option.state &= ~(QStyle.StateFlag.State_HasFocus | QStyle.StateFlag.State_Selected)
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
