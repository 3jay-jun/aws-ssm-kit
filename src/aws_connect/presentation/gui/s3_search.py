"""S3 navigation/search input dialog; no network or transfer logic."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTabBar,
    QVBoxLayout,
    QWidget,
)

from aws_connect.presentation.gui.icons import set_button_icon


class S3SearchDialog(QDialog):
    def __init__(
        self,
        parent: QWidget,
        bucket: str,
        prefix: str,
        buckets: list[str],
        recent: list[tuple[str, str]],
    ) -> None:
        super().__init__(parent)
        self.setObjectName("s3_search_dialog")
        self.setWindowTitle("S3 검색 / 경로 이동")
        self.setMinimumWidth(560)
        self.resize(650, 610)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 24, 30, 24)
        layout.setSpacing(16)
        head = QHBoxLayout()
        title = QLabel("S3 검색 / 경로 이동")
        title.setObjectName("s3_dialog_title")
        head.addWidget(title, 1)
        close = QPushButton("×")
        close.setAccessibleName("닫기")
        close.clicked.connect(self.reject)
        head.addWidget(close)
        layout.addLayout(head)
        self.tabs = QTabBar()
        self.tabs.addTab("경로 이동")
        self.tabs.addTab("파일 검색")
        self.tabs.setExpanding(False)
        self.tabs.setDrawBase(False)
        layout.addWidget(self.tabs)
        layout.addWidget(QLabel("현재 위치"))
        current = QLabel(f"s3://{bucket}/{prefix}")
        current.setTextFormat(Qt.TextFormat.PlainText)
        current.setWordWrap(True)
        current.setObjectName("s3_current_location")
        layout.addWidget(current)
        layout.addWidget(QLabel("버킷 선택"))
        self.bucket = QComboBox()
        self.bucket.setEditable(True)
        self.bucket.addItems(buckets)
        self.bucket.setCurrentText(bucket)
        layout.addWidget(self.bucket)
        layout.addWidget(QLabel("경로 (Prefix)"))
        self.prefix = QLineEdit(prefix)
        self.prefix.setPlaceholderText("이동할 경로를 입력하세요. (예: images/banners)")
        path_row = QHBoxLayout()
        path_row.addWidget(self.prefix, 1)
        open_path = QPushButton()
        open_path.setProperty("action_button", True)
        set_button_icon(open_path, "file-folder.svg", tooltip="입력한 경로로 이동")
        open_path.clicked.connect(self._open_path)
        path_row.addWidget(open_path)
        layout.addLayout(path_row)
        self.query = QLineEdit()
        self.query.setPlaceholderText("현재 Prefix와 하위 폴더에서 찾을 파일명")
        self.query.hide()
        layout.addWidget(self.query)
        self.recent = QWidget()
        recent_layout = QVBoxLayout(self.recent)
        recent_layout.setContentsMargins(0, 0, 0, 0)
        recent_layout.addWidget(QLabel("최근 경로"))
        for saved_bucket, saved_prefix in recent[:3]:
            button = QPushButton(
                f"/{saved_prefix}"
                if saved_bucket == bucket
                else f"s3://{saved_bucket}/{saved_prefix}"
            )
            button.setObjectName("s3_recent_path")
            set_button_icon(button, "clock-solid-full.svg", color="#7182a5")
            button.clicked.connect(
                lambda _checked=False, b=saved_bucket, p=saved_prefix: self._select_recent(b, p)
            )
            recent_layout.addWidget(button)
        if not recent:
            recent_layout.addWidget(QLabel("최근 경로가 없습니다."))
        layout.addWidget(self.recent)
        layout.addStretch()
        actions = QHBoxLayout()
        actions.addStretch()
        cancel = QPushButton("취소")
        cancel.clicked.connect(self.reject)
        actions.addWidget(cancel)
        self.submit = QPushButton("이동하기")
        self.submit.setProperty("variant", "primary")
        self.submit.clicked.connect(self.accept)
        actions.addWidget(self.submit)
        layout.addLayout(actions)
        self.tabs.currentChanged.connect(self._tab_changed)

    def _select_recent(self, bucket: str, prefix: str) -> None:
        self.bucket.setCurrentText(bucket)
        self.prefix.setText(prefix)

    def _open_path(self) -> None:
        self.tabs.setCurrentIndex(0)
        self.accept()

    def _tab_changed(self, index: int) -> None:
        self.query.setVisible(index == 1)
        self.recent.setVisible(index == 0)
        self.submit.setText("검색" if index else "이동하기")

    def accept(self) -> None:
        if not self.bucket.currentText().strip():
            self.bucket.setFocus()
            self.bucket.setToolTip("버킷 이름을 입력하세요.")
            return
        if self.tabs.currentIndex() == 1 and not self.query.text().strip():
            self.query.setFocus()
            self.query.setPlaceholderText("검색할 파일명을 입력하세요.")
            return
        super().accept()
