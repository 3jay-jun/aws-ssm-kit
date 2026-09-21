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
    QPainter,
    QPixmap,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFileDialog,
    QFileSystemModel,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)

from aws_connect.domain.errors import ApplicationError
from aws_connect.presentation.gui.icons import gui_icon, set_button_icon
from aws_connect.presentation.gui.table_selection import add_check_all_header
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
        "폴더" if source.is_dir() else format_size(info.st_size),
        image,
    )


def format_size(value: int) -> str:
    size = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return "0 B"


@dataclass
class UploadQueueEntry:
    source: Path
    target: str = ""
    size: int = 0
    state: str = "대기"
    percent: int = 0
    started: str = "-"


class UploadSourcesList(QTableWidget):
    """Queue presentation; all transfer and retry work belongs to the page/service."""

    def __init__(
        self,
        on_drag: Callable[[QDragEnterEvent | QDragMoveEvent], None],
        on_drop: Callable[[QDropEvent], None],
        on_choose: Callable[[], None],
        runner: GuiTaskRunner,
    ) -> None:
        super().__init__(0, 9)
        self._on_drag, self._on_drop, self._on_choose = on_drag, on_drop, on_choose
        self._runner = runner
        self._generation = 0
        self._previews: list[TaskHandle] = []
        self.setObjectName("s3_upload_sources")
        self.setWordWrap(False)
        self.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.setHorizontalHeaderLabels(
            ["", "미리보기", "파일명", "크기", "로컬 경로", "수정 시간", "상태", "진행률", "작업"]
        )
        self.setAcceptDrops(True)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.verticalHeader().hide()
        self.verticalHeader().setDefaultSectionSize(42)
        self.horizontalHeader().setDefaultAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        for col, width in enumerate((32, 64, 180, 75, 175, 160, 70, 120, 76)):
            self.setColumnWidth(col, width)
        self.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.setMinimumHeight(214)
        add_check_all_header(self)
        self.setAccessibleName("업로드 큐: 폴더 추가 버튼을 누르거나 파일과 폴더를 끌어다 놓으세요")

    def clear(self) -> None:
        self._generation += 1
        for task in self._previews:
            task.cancel()
        self._previews.clear()
        self.setRowCount(0)

    def count(self) -> int:
        return self.rowCount()

    def _cell(self, row: int, column: int) -> QTableWidgetItem:
        item = self.item(row, column)
        if item is None:
            raise RuntimeError("업로드 큐 셀이 초기화되지 않았습니다.")
        return item

    def paintEvent(self, event: Any) -> None:  # noqa: N802
        super().paintEvent(event)
        if not self.rowCount():
            painter = QPainter(self.viewport())
            painter.setPen(Qt.GlobalColor.gray)
            painter.drawText(
                self.viewport().rect(),
                Qt.AlignmentFlag.AlignCenter,
                "우측 상단 폴더 추가 버튼을 누르거나 파일·폴더를 끌어다 놓으세요",
            )
            painter.end()

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        self._on_drag(event)

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:  # noqa: N802
        self._on_drag(event)

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        self._on_drop(event)

    def add_entry(
        self,
        entry: UploadQueueEntry,
        mime_type: str,
        remove: Callable[[Path], None],
        retry: Callable[[Path], None],
    ) -> None:
        row = self.rowCount()
        self.insertRow(row)
        check = QTableWidgetItem()
        check.setCheckState(Qt.CheckState.Unchecked)
        check.setData(Qt.ItemDataRole.UserRole, entry.source)
        self.setItem(row, 0, check)
        icon = QLabel()
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setPixmap(
            gui_icon("file-folder.svg" if mime_type == "폴더" else "file-default.svg").pixmap(
                30, 30
            )
        )
        self.setCellWidget(row, 1, icon)
        for col, text in (
            (2, entry.source.name),
            (3, "…"),
            (4, str(entry.source)),
            (5, "…"),
            (6, entry.state),
        ):
            item = QTableWidgetItem(text)
            item.setToolTip(str(entry.source) if col == 2 else text)
            self.setItem(row, col, item)
        progress = QProgressBar()
        progress.setRange(0, 100)
        progress.setValue(entry.percent)
        self.setCellWidget(row, 7, progress)
        actions = QWidget()
        layout = QHBoxLayout(actions)
        layout.setContentsMargins(2, 0, 2, 0)
        layout.setSpacing(4)
        again = QPushButton()
        again.setObjectName("upload_retry")
        again.setProperty("action_button", True)
        set_button_icon(
            again, "common-refresh.svg", color="#0055ff", tooltip="실패 항목 재시도", size=16
        )
        again.clicked.connect(lambda: retry(entry.source))
        again.setVisible(entry.state == "실패")
        layout.addWidget(again, alignment=Qt.AlignmentFlag.AlignVCenter)
        button = QPushButton()
        button.setObjectName("upload_source_remove")
        button.setProperty("action_button", True)
        button.setProperty("variant", "danger")
        set_button_icon(
            button,
            "common-delete.svg",
            color="#ffffff",
            tooltip="큐에서 제거 (S3 파일은 유지)",
            size=16,
        )
        button.clicked.connect(lambda: remove(entry.source))
        layout.addWidget(button, alignment=Qt.AlignmentFlag.AlignVCenter)
        self.setCellWidget(row, 8, actions)
        generation = self._generation

        def loaded(value: _Preview) -> None:
            if generation != self._generation:
                return
            self._cell(row, 3).setText(value.size)
            self._cell(row, 5).setText(value.modified)
            self._cell(row, 5).setToolTip(value.modified)
            if not value.image.isNull():
                icon.setPixmap(
                    QPixmap.fromImage(value.image).scaled(
                        44,
                        34,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )

        def failed(_error: ApplicationError) -> None:
            if generation == self._generation:
                self._cell(row, 3).setText("읽기 실패")

        self._previews.append(
            self._runner.submit(lambda: _read_preview(entry.source, mime_type), loaded, failed)
        )

    def update_entry(self, row: int, entry: UploadQueueEntry, busy: bool) -> None:
        self._cell(row, 4).setText(str(entry.source))
        self._cell(row, 4).setToolTip(str(entry.source))
        self._cell(row, 6).setText(entry.state)
        state_icons = {
            "성공": ("common-circle-check.svg", "#009e55"),
            "실패": ("common-circle-xmark.svg", "#ff2638"),
            "대기": ("clock-solid-full.svg", "#7182a5"),
        }
        icon, color = state_icons.get(entry.state, ("clock-solid-full.svg", "#0055ff"))
        self._cell(row, 6).setIcon(gui_icon(icon, color=color))
        bar = self.cellWidget(row, 7)
        if isinstance(bar, QProgressBar):
            bar.setValue(entry.percent)
        actions = self.cellWidget(row, 8)
        actions.setEnabled(not busy)
        retry = actions.findChild(QPushButton, "upload_retry")
        if retry is not None:
            retry.setVisible(entry.state == "실패")


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
