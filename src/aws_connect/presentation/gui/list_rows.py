"""Compact list rows with shared metadata, accessibility, and separators."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter, QPaintEvent
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


class _ElidedLabel(QLabel):
    """Preserve the complete accessible text while painting within the row width."""

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setFont(self.font())
        painter.setPen(self.palette().windowText().color())
        painter.drawText(
            self.contentsRect(),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            self.fontMetrics().elidedText(self.text(), Qt.TextElideMode.ElideRight, self.width()),
        )


def set_compact_list_row(
    list_widget: QListWidget,
    item: QListWidgetItem,
    title: str,
    metadata: tuple[str, ...],
    *,
    title_suffix: str | None = None,
    elide: bool = False,
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
        label = _ElidedLabel(text) if elide else QLabel(text)
        label.setToolTip(text)
        label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setObjectName("compact_row_title" if index == 0 else "compact_row_metadata")
        label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        if index == 0 and title_suffix is not None:
            heading = QHBoxLayout()
            heading.setSpacing(6)
            heading.addWidget(label, 1)
            suffix = QLabel(title_suffix)
            suffix.setObjectName("compact_row_metadata")
            suffix.ensurePolished()
            suffix.setFixedWidth(suffix.fontMetrics().horizontalAdvance("0000-00-00 00:00") + 8)
            suffix.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            heading.addWidget(suffix)
            if trailing is not None:
                heading.addWidget(trailing, alignment=Qt.AlignmentFlag.AlignVCenter)
            layout.addLayout(heading)
        else:
            layout.addWidget(label)
    if trailing is not None and title_suffix is None:
        content.addWidget(trailing, alignment=Qt.AlignmentFlag.AlignVCenter)
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
