"""Upload cards and bounded previews, with shared page selection/drop handling."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import (
    QDragEnterEvent,
    QDragMoveEvent,
    QDropEvent,
    QImage,
    QImageReader,
    QMouseEvent,
    QPainter,
    QPixmap,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFileDialog,
    QFileSystemModel,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from aws_connect.domain.errors import ApplicationError
from aws_connect.presentation.gui.icons import gui_icon
from aws_connect.presentation.gui.tasks import GuiTaskRunner, TaskHandle


@dataclass(frozen=True)
class _Preview:
    modified: str
    size: str
    image: QImage


def _read_preview(source: Path, mime_type: str) -> _Preview:
    """Read metadata and downsample local images off the GUI thread."""
    try:
        info = source.stat()
    except OSError:
        return _Preview("파일 정보를 읽을 수 없습니다.", "", QImage())
    size = float(info.st_size)
    unit = "B"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            break
        size /= 1024
    image = QImage()
    if mime_type.startswith("image/") and info.st_size <= 20 * 1024 * 1024:
        reader = QImageReader(str(source))
        dimensions = reader.size()
        if dimensions.isValid() and dimensions.width() * dimensions.height() <= 16_000_000:
            reader.setAutoTransform(True)
            reader.setScaledSize(
                dimensions.scaled(QSize(104, 72), Qt.AspectRatioMode.KeepAspectRatio)
            )
            image = reader.read()
    return _Preview(
        datetime.fromtimestamp(info.st_mtime).strftime("%Y-%m-%d %H:%M"),
        "폴더"
        if source.is_dir()
        else f"{size:.0f} {unit}"
        if unit == "B"
        else f"{size:.1f} {unit}",
        image,
    )


class UploadSourcesList(QListWidget):
    """Square cards; blank viewport clicks select files, item clicks select cards."""

    def __init__(
        self,
        on_drag: Callable[[QDragEnterEvent | QDragMoveEvent], None],
        on_drop: Callable[[QDropEvent], None],
        on_choose: Callable[[], None],
        runner: GuiTaskRunner,
    ) -> None:
        super().__init__()
        self._on_drag = on_drag
        self._on_drop = on_drop
        self._on_choose = on_choose
        self._runner = runner
        self._generation = 0
        self._previews: list[TaskHandle] = []
        self.setObjectName("s3_upload_sources")
        self.setAcceptDrops(True)
        self.setFlow(QListWidget.Flow.LeftToRight)
        self.setWrapping(False)
        self.setSpacing(8)
        self.setFixedHeight(184)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.setAccessibleName("업로드 파일: 빈 배경을 클릭해 파일 또는 폴더 추가")

    def clear(self) -> None:
        self._generation += 1
        for task in self._previews:
            task.cancel()
        self._previews.clear()
        super().clear()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self.itemAt(event.position().toPoint()) is None
        ):
            self._on_choose()
            event.accept()
            return
        super().mousePressEvent(event)

    def paintEvent(self, event: Any) -> None:  # noqa: N802
        super().paintEvent(event)
        if self.count() == 0:
            painter = QPainter(self.viewport())
            painter.setPen(Qt.GlobalColor.gray)
            painter.drawText(
                self.viewport().rect(),
                Qt.AlignmentFlag.AlignCenter,
                "클릭하거나 파일·폴더를 끌어다 놓으세요",
            )
            painter.end()

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        self._on_drag(event)

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:  # noqa: N802
        self._on_drag(event)

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        self._on_drop(event)

    def add_source(self, source: Path, mime_type: str, remove: Callable[[Path], None]) -> None:
        icon_name = (
            "file-folder.svg"
            if mime_type == "폴더"
            else "file-image.svg"
            if mime_type.startswith("image/")
            else "file-video.svg"
            if mime_type.startswith("video/")
            else "file-default.svg"
        )
        card = QFrame()
        card.setObjectName("upload_source_card")
        card.setToolTip(str(source))
        layout = QVBoxLayout(card)
        layout.setContentsMargins(10, 6, 10, 8)
        layout.setSpacing(3)
        top = QHBoxLayout()
        top.addStretch()
        button = QPushButton("×")
        button.setObjectName("upload_source_remove")
        button.setToolTip(f"{source.name} 제거")
        button.setAccessibleName(f"{source.name} 업로드 대상에서 제거")
        button.clicked.connect(lambda: remove(source))
        top.addWidget(button)
        layout.addLayout(top)
        icon = QLabel()
        icon.setObjectName("upload_source_icon")
        icon.setFixedHeight(62)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setPixmap(gui_icon(icon_name).pixmap(QSize(48, 48)))
        layout.addWidget(icon)
        name = QLabel()
        name.setObjectName("upload_source_name")
        name.setTextFormat(Qt.TextFormat.PlainText)
        name.setText(
            name.fontMetrics().elidedText(
                source.name or str(source), Qt.TextElideMode.ElideRight, 128
            )
        )
        name.setFixedWidth(128)
        layout.addWidget(name)
        modified = QLabel("정보 확인 중…")
        size = QLabel("")
        for label in (modified, size):
            label.setObjectName("upload_source_metadata")
            label.setTextFormat(Qt.TextFormat.PlainText)
            layout.addWidget(label)
        for label in (icon, name, modified, size):
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        item = QListWidgetItem()
        item.setData(Qt.ItemDataRole.UserRole, source)
        item.setData(Qt.ItemDataRole.AccessibleTextRole, str(source))
        item.setToolTip(str(source))
        item.setSizeHint(QSize(152, 160))
        self.addItem(item)
        self.setItemWidget(item, card)
        generation = self._generation

        def loaded(value: _Preview) -> None:
            if self._generation != generation:
                return
            modified.setText(value.modified)
            size.setText(value.size)
            if not value.image.isNull():
                icon.setPixmap(
                    QPixmap.fromImage(value.image).scaled(
                        104,
                        62,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )

        def failed(_error: ApplicationError) -> None:
            if self._generation == generation:
                modified.setText("미리보기를 읽을 수 없습니다.")

        self._previews.append(
            self._runner.submit(lambda: _read_preview(source, mime_type), loaded, failed)
        )


class UploadSourceDialog(QFileDialog):
    """One Qt explorer permits mixed file/folder selection without a mode prompt."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent, "업로드 파일 및 폴더 선택")
        self.paths: tuple[Path, ...] = ()
        self.setOption(QFileDialog.Option.DontUseNativeDialog, True)
        self.setFileMode(QFileDialog.FileMode.ExistingFiles)
        self.setLabelText(QFileDialog.DialogLabel.Accept, "추가")
        for view in self.findChildren(QAbstractItemView):
            if isinstance(view.model(), QFileSystemModel):
                view.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)

    def accept(self) -> None:
        selected: list[Path] = []
        for view in self.findChildren(QAbstractItemView):
            model = view.model()
            if not view.isVisible() or not isinstance(model, QFileSystemModel):
                continue
            selected.extend(
                Path(model.filePath(index)) for index in view.selectionModel().selectedRows(0)
            )
        if not selected:
            selected = [Path(value) for value in self.selectedFiles()]
        paths = tuple(dict.fromkeys(path for path in selected if path.is_file() or path.is_dir()))
        if paths:
            self.paths = paths
            QDialog.accept(self)
