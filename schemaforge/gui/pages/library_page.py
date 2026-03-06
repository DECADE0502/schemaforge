"""器件库管理主页面

整合器件列表、搜索、手动录入表单、EasyEDA 搜索面板、PDF/图片导入。
左侧: 器件列表 + 搜索框
右侧: 标签页切换 手动录入 / EasyEDA搜索 / PDF图片导入

所有 UI 文案为中文。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from schemaforge.library.service import LibraryService
from schemaforge.library.validator import DeviceDraft
from schemaforge.gui.widgets.device_form import DeviceForm
from schemaforge.gui.widgets.device_search_panel import DeviceSearchPanel
from schemaforge.gui.widgets.import_wizard import ImportWizard


class LibraryPage(QWidget):
    """器件库管理页面

    信号:
        device_count_changed(int): 器件数量变化 (通知主窗口更新标题等)
    """

    device_count_changed = Signal(int)

    def __init__(
        self,
        store_dir: Path | str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)

        # 默认存储目录
        if store_dir is None:
            store_dir = Path(__file__).resolve().parents[3] / "schemaforge" / "store"
        self._service = LibraryService(store_dir)

        self._setup_ui()
        self._refresh_device_list()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        # 顶部标题栏
        header = QHBoxLayout()
        title = QLabel("📦 器件库管理")
        title.setFont(QFont("Microsoft YaHei", 14, QFont.Weight.Bold))
        header.addWidget(title)
        header.addStretch()

        self.stats_label = QLabel("")
        self.stats_label.setStyleSheet("color: #6c757d; font-size: 12px;")
        header.addWidget(self.stats_label)

        refresh_btn = QPushButton("🔄 刷新")
        refresh_btn.setStyleSheet(
            "QPushButton { padding: 4px 12px; border-radius: 4px; }"
        )
        refresh_btn.clicked.connect(self._refresh_device_list)
        header.addWidget(refresh_btn)
        layout.addLayout(header)

        # 主分割器: 左(列表) | 右(操作标签页)
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # ── 左侧: 器件列表 ──
        left_panel = self._build_device_list_panel()
        splitter.addWidget(left_panel)

        # ── 右侧: 操作标签页 ──
        self.operation_tabs = QTabWidget()

        # 手动录入标签
        self.device_form = DeviceForm()
        self.device_form.draft_ready.connect(self._on_manual_submit)
        self.operation_tabs.addTab(self.device_form, "✏️ 手动录入")

        # EasyEDA 搜索标签
        self.search_panel = DeviceSearchPanel()
        self.search_panel.hit_selected.connect(self._on_easyeda_import)
        self.operation_tabs.addTab(self.search_panel, "🔍 EasyEDA搜索")

        # PDF/图片导入标签
        self.import_wizard = ImportWizard(use_mock=True)
        self.import_wizard.draft_ready.connect(self._on_import_wizard_submit)
        self.operation_tabs.addTab(self.import_wizard, "📄 PDF/图片导入")

        splitter.addWidget(self.operation_tabs)

        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)
        splitter.setSizes([400, 600])

        layout.addWidget(splitter)

    def _build_device_list_panel(self) -> QWidget:
        """构建左侧器件列表面板"""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)

        # 搜索框
        search_row = QHBoxLayout()
        self.list_search = QLineEdit()
        self.list_search.setPlaceholderText("搜索器件库...")
        self.list_search.setFont(QFont("Microsoft YaHei", 10))
        self.list_search.textChanged.connect(self._on_search_changed)
        search_row.addWidget(self.list_search)
        layout.addLayout(search_row)

        # 器件表格
        self.device_table = QTableWidget(0, 5)
        self.device_table.setHorizontalHeaderLabels([
            "料号", "类别", "制造商", "封装", "来源",
        ])
        header = self.device_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        self.device_table.setColumnWidth(1, 80)
        self.device_table.setColumnWidth(3, 90)
        self.device_table.setColumnWidth(4, 70)
        self.device_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self.device_table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers
        )
        layout.addWidget(self.device_table)

        # 操作按钮
        btn_row = QHBoxLayout()

        delete_btn = QPushButton("删除选中")
        delete_btn.setStyleSheet(
            "QPushButton { background: #dc3545; color: white; "
            "border-radius: 4px; padding: 6px 12px; }"
            "QPushButton:hover { background: #bb2d3b; }"
        )
        delete_btn.clicked.connect(self._on_delete_selected)
        btn_row.addWidget(delete_btn)

        btn_row.addStretch()

        layout.addLayout(btn_row)
        return panel

    # ── 数据操作 ──

    def _refresh_device_list(self, search_query: str = "") -> None:
        """刷新器件列表"""
        if search_query:
            devices = self._service.search(query=search_query)
        else:
            # 列出全部
            part_numbers = self._service.list_all()
            devices = []
            for pn in part_numbers:
                dev = self._service.get(pn)
                if dev:
                    devices.append(dev)

        self.device_table.setRowCount(0)
        for dev in devices:
            row = self.device_table.rowCount()
            self.device_table.insertRow(row)
            self.device_table.setItem(row, 0, QTableWidgetItem(dev.part_number))
            self.device_table.setItem(row, 1, QTableWidgetItem(dev.category))
            self.device_table.setItem(row, 2, QTableWidgetItem(dev.manufacturer))
            self.device_table.setItem(row, 3, QTableWidgetItem(dev.package))
            self.device_table.setItem(row, 4, QTableWidgetItem(dev.source))

        # 更新统计
        stats = self._service.get_stats()
        total = stats["total_devices"]
        self.stats_label.setText(f"共 {total} 个器件")
        self.device_count_changed.emit(total)

    def _on_search_changed(self, text: str) -> None:
        """搜索框文字变化 → 实时过滤"""
        self._refresh_device_list(text.strip())

    def _on_delete_selected(self) -> None:
        """删除选中器件"""
        selected_rows = self.device_table.selectionModel().selectedRows()
        if not selected_rows:
            QMessageBox.information(self, "提示", "请先选中要删除的器件")
            return

        part_numbers = []
        for idx in selected_rows:
            item = self.device_table.item(idx.row(), 0)
            if item:
                part_numbers.append(item.text())

        if not part_numbers:
            return

        reply = QMessageBox.question(
            self,
            "确认删除",
            "确定要删除以下器件？\n\n" + "\n".join(part_numbers),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        deleted = 0
        for pn in part_numbers:
            if self._service.delete(pn):
                deleted += 1

        self._refresh_device_list(self.list_search.text().strip())
        QMessageBox.information(self, "删除完成", f"已删除 {deleted} 个器件")

    def _on_manual_submit(self, draft: DeviceDraft) -> None:
        """手动录入提交"""
        result = self._service.add_device_from_draft(draft)

        if result.success:
            QMessageBox.information(
                self,
                "入库成功",
                f"器件 {result.device.part_number} 已成功入库！",
            )
            self.device_form.clear()
            self._refresh_device_list(self.list_search.text().strip())
        else:
            # 构造详细错误信息
            msg = result.error_message
            if result.validation and not result.validation.is_valid:
                errors = result.validation.errors
                if errors:
                    msg += "\n\n校验错误:\n" + "\n".join(
                        f"  • [{e.field_path}] {e.message}" for e in errors
                    )
            if result.duplicate_check and result.duplicate_check.has_exact:
                msg += "\n\n如需强制覆盖，请先删除已有器件后重新入库。"

            QMessageBox.warning(self, "入库失败", msg)

    def _on_easyeda_import(self, hit: object) -> None:
        """EasyEDA 搜索结果 → 导入为 DeviceDraft → 加载到表单"""
        from schemaforge.ingest.easyeda_provider import EasyEDAHit as HitType
        if not isinstance(hit, HitType):
            return

        draft = self._service.easyeda_hit_to_draft(hit)

        # 切换到手动录入标签并加载数据
        self.operation_tabs.setCurrentIndex(0)
        self.device_form.load_draft(draft)

        QMessageBox.information(
            self,
            "已加载",
            f"器件 {draft.part_number} 已加载到表单。\n"
            "请检查并补充信息后点击「提交入库」。",
        )

    def _on_import_wizard_submit(self, draft: object) -> None:
        """PDF/图片导入向导提交 → 入库"""
        if not isinstance(draft, DeviceDraft):
            return

        result = self._service.add_device_from_draft(draft)

        if result.success:
            QMessageBox.information(
                self,
                "入库成功",
                f"器件 {result.device.part_number} 已从文件导入成功入库！",
            )
            self.import_wizard._reset()
            self._refresh_device_list(self.list_search.text().strip())
        else:
            msg = result.error_message
            if result.validation and not result.validation.is_valid:
                errors = result.validation.errors
                if errors:
                    msg += "\n\n校验错误:\n" + "\n".join(
                        f"  • [{e.field_path}] {e.message}" for e in errors
                    )
            QMessageBox.warning(self, "入库失败", msg)
