"""Compact list rows with shared metadata, accessibility, and separators."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


def set_compact_list_row(
    list_widget: QListWidget,
    item: QListWidgetItem,
    title: str,
    metadata: tuple[str, ...],
    *,
    connected: bool = False,
    show_status: bool = False,
    trailing: QWidget | None = None,
    status_alignment: Qt.AlignmentFlag = Qt.AlignmentFlag.AlignVCenter,
) -> None:
    """Attach a compact title and metadata without changing item identity."""
    row = QFrame()
    row.setObjectName("compact_list_row")
    row.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, trailing is None)
    if show_status:
        row.setProperty("connected", connected)
    content = QHBoxLayout(row)
    content.setContentsMargins(10, 6, 10, 6)
    content.setSpacing(12)
    if connected or show_status:
        dot = QFrame()
        dot.setObjectName("status_dot")
        dot.setProperty("active", connected)
        dot.setAccessibleName("연결됨" if connected else "중지됨")
        content.addWidget(dot, alignment=status_alignment)
    layout = QVBoxLayout()
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(1)
    content.addLayout(layout, 1)
    for index, text in enumerate((title, *metadata)):
        if not text:
            continue
        label = QLabel(text)
        label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setObjectName("compact_row_title" if index == 0 else "compact_row_metadata")
        label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout.addWidget(label)
    if trailing is not None:
        content.addWidget(trailing)
    description = "\n".join(text for text in (title, *metadata) if text)
    item.setToolTip(description)
    item.setData(Qt.ItemDataRole.AccessibleTextRole, description)
    list_widget.setItemWidget(item, row)
    item.setSizeHint(row.sizeHint())


def update_list_row_separators(list_widget: QListWidget) -> None:
    """Keep the final row borderless after creation, reload, or deletion."""
    for index in range(list_widget.count()):
        row = list_widget.itemWidget(list_widget.item(index))
        if row is not None:
            row.setProperty("last_row", index == list_widget.count() - 1)
            row.style().unpolish(row)
            row.style().polish(row)
