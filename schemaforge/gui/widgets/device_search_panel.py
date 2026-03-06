"""EasyEDA 在线搜索面板

搜索 EasyEDA 器件库，展示搜索结果，支持选中后导入为 DeviceDraft。
所有 UI 文案为中文。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal, QThread
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from schemaforge.ingest.easyeda_provider import (
    EasyEDAHit,
    search_easyeda,
)


# ============================================================
# 搜索工作线程
# ============================================================

class EasyEDASearchWorker(QThread):
    """后台执行 EasyEDA 搜索"""

    finished = Signal(list)  # list[EasyEDAHit]
    error = Signal(str)

    def __init__(self, keyword: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.keyword = keyword

    def run(self) -> None:
        try:
            result = search_easyeda(self.keyword, limit=20)
            if result.success:
                self.finished.emit(result.data)
            else:
                err_msg = result.error.message if result.error else "搜索失败"
                self.error.emit(err_msg)
        except Exception as exc:
            self.error.emit(f"搜索异常: {exc}")


# ============================================================
# 搜索结果卡片
# ============================================================

class HitCard(QFrame):
    """单条搜索结果卡片"""

    import_clicked = Signal(object)  # EasyEDAHit

    def __init__(self, hit: EasyEDAHit, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.hit = hit
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet(
            "QFrame { background: #f8f9fa; border: 1px solid #dee2e6; "
            "border-radius: 6px; padding: 8px; margin: 2px 0; }"
            "QFrame:hover { background: #e9ecef; border-color: #adb5bd; }"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(4)

        # 第一行: 标题 + 导入按钮
        top_row = QHBoxLayout()
        title_label = QLabel(hit.title or "(无标题)")
        title_label.setFont(QFont("Microsoft YaHei", 11, QFont.Weight.Bold))
        top_row.addWidget(title_label)
        top_row.addStretch()

        import_btn = QPushButton("导入")
        import_btn.setStyleSheet(
            "QPushButton { background: #198754; color: white; "
            "border-radius: 4px; padding: 4px 12px; font-weight: bold; }"
            "QPushButton:hover { background: #157347; }"
        )
        import_btn.clicked.connect(lambda: self.import_clicked.emit(self.hit))
        top_row.addWidget(import_btn)
        layout.addLayout(top_row)

        # 第二行: 描述
        if hit.description:
            desc_label = QLabel(hit.description[:120])
            desc_label.setWordWrap(True)
            desc_label.setStyleSheet("color: #495057; font-size: 11px;")
            layout.addWidget(desc_label)

        # 第三行: 元数据
        meta_parts: list[str] = []
        if hit.manufacturer:
            meta_parts.append(f"厂商: {hit.manufacturer}")
        if hit.package:
            meta_parts.append(f"封装: {hit.package}")
        if hit.lcsc_part:
            meta_parts.append(f"LCSC: {hit.lcsc_part}")
        if hit.pin_count:
            meta_parts.append(f"引脚: {hit.pin_count}")

        if meta_parts:
            meta_label = QLabel(" | ".join(meta_parts))
            meta_label.setStyleSheet("color: #6c757d; font-size: 10px;")
            layout.addWidget(meta_label)


# ============================================================
# EasyEDA 搜索面板
# ============================================================

class DeviceSearchPanel(QWidget):
    """EasyEDA 器件搜索面板

    信号:
        hit_selected(EasyEDAHit): 用户选择导入某个搜索结果
    """

    hit_selected = Signal(object)  # EasyEDAHit

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._worker: EasyEDASearchWorker | None = None
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        # 标题
        title = QLabel("EasyEDA 在线搜索")
        title.setFont(QFont("Microsoft YaHei", 13, QFont.Weight.Bold))
        layout.addWidget(title)

        # 搜索栏
        search_row = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("输入器件型号搜索，如: TPS54202")
        self.search_input.setFont(QFont("Microsoft YaHei", 11))
        self.search_input.returnPressed.connect(self._on_search)
        search_row.addWidget(self.search_input)

        self.search_btn = QPushButton("搜索")
        self.search_btn.setMinimumHeight(36)
        self.search_btn.setStyleSheet(
            "QPushButton { background: #0d6efd; color: white; "
            "border-radius: 6px; padding: 6px 20px; font-weight: bold; }"
            "QPushButton:hover { background: #0b5ed7; }"
            "QPushButton:disabled { background: #6c757d; }"
        )
        self.search_btn.clicked.connect(self._on_search)
        search_row.addWidget(self.search_btn)
        layout.addLayout(search_row)

        # 进度条
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)  # indeterminate
        self.progress.hide()
        layout.addWidget(self.progress)

        # 状态标签
        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #6c757d; font-size: 11px;")
        layout.addWidget(self.status_label)

        # 结果滚动区
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )

        self._result_container = QWidget()
        self._result_layout = QVBoxLayout(self._result_container)
        self._result_layout.setContentsMargins(0, 0, 0, 0)
        self._result_layout.setSpacing(4)
        self._result_layout.addStretch()

        self._scroll.setWidget(self._result_container)
        layout.addWidget(self._scroll, 1)

    def _on_search(self) -> None:
        """执行搜索"""
        keyword = self.search_input.text().strip()
        if not keyword:
            QMessageBox.warning(self, "提示", "请输入搜索关键词！")
            return

        self.search_btn.setEnabled(False)
        self.progress.show()
        self.status_label.setText(f"正在搜索: {keyword}...")
        self._clear_results()

        self._worker = EasyEDASearchWorker(keyword)
        self._worker.finished.connect(self._on_results)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_results(self, hits: list[EasyEDAHit]) -> None:
        """处理搜索结果"""
        self.search_btn.setEnabled(True)
        self.progress.hide()

        if not hits:
            self.status_label.setText("未找到匹配器件")
            return

        self.status_label.setText(f"找到 {len(hits)} 个结果")

        for hit in hits:
            card = HitCard(hit)
            card.import_clicked.connect(self._on_import)
            self._result_layout.insertWidget(
                self._result_layout.count() - 1, card,
            )

    def _on_error(self, msg: str) -> None:
        """搜索出错"""
        self.search_btn.setEnabled(True)
        self.progress.hide()
        self.status_label.setText(f"搜索失败: {msg}")
        self.status_label.setStyleSheet("color: #dc3545; font-size: 11px;")

    def _on_import(self, hit: EasyEDAHit) -> None:
        """用户点击导入"""
        self.hit_selected.emit(hit)

    def _clear_results(self) -> None:
        """清空结果列表"""
        while self._result_layout.count() > 1:
            item = self._result_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self.status_label.setStyleSheet("color: #6c757d; font-size: 11px;")
