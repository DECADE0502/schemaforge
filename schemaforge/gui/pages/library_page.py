"""器件库管理页面

LibraryPage — 器件库浏览、手动录入、EasyEDA 搜索导入、PDF/图片导入、器件详情查看。

每个功能模块以独立 QDialog 弹窗形式打开，主页面为全宽器件列表。
"""

from __future__ import annotations

import logging
from pathlib import Path
import traceback

from PySide6.QtCore import QThread, Qt, Signal, Slot
from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTableView,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from schemaforge.gui.widgets.chat_panel import ChatPanel
from schemaforge.gui.widgets.symbol_editor import SymbolEditorWidget
from schemaforge.ingest.datasheet_extractor import (
    ExtractionResult,
    extract_from_image,
    extract_from_pdf,
)
from schemaforge.ingest.easyeda_provider import EasyEDAHit, fetch_easyeda_symbol, search_easyeda
from schemaforge.library.models import DeviceModel, PinSide, SymbolDef
from schemaforge.library.service import LibraryService
from schemaforge.library.validator import (
    VALID_CATEGORIES,
    DeviceDraft,
    PinDraft,
)

logger = logging.getLogger(__name__)

# 器件库存储路径
_STORE_DIR = Path("schemaforge/store")

# 主表列定义
_TABLE_COLUMNS = ["料号", "类别", "制造商", "封装", "来源"]


# ================================================================
# DeviceDetailDialog — 器件详情弹窗
# ================================================================


class DeviceDetailDialog(QDialog):
    """器件详情弹窗，包含所有字段 + 符号编辑器。"""

    symbol_saved = Signal(str, object)

    def __init__(
        self,
        device: DeviceModel,
        service: LibraryService,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._device = device
        self._service = service
        self.setWindowTitle(f"器件详情 — {device.part_number}")
        self.resize(900, 700)
        self.setWindowFlags(
            self.windowFlags() | Qt.WindowType.WindowMaximizeButtonHint
        )
        self._init_ui()

    def _init_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        container = QWidget()
        layout = QVBoxLayout(container)

        dev = self._device

        # --- 基础信息 ---
        form = QFormLayout()

        lbl_pn = QLabel(dev.part_number)
        lbl_pn.setStyleSheet("font-weight: bold; font-size: 14px;")
        form.addRow("料号", lbl_pn)

        form.addRow("类别", QLabel(dev.category or "—"))
        form.addRow("制造商", QLabel(dev.manufacturer or "—"))

        desc_label = QLabel(dev.description or "—")
        desc_label.setWordWrap(True)
        form.addRow("描述", desc_label)

        form.addRow("封装", QLabel(dev.package or "—"))
        form.addRow("来源", QLabel(dev.source or "—"))
        form.addRow("LCSC编号", QLabel(dev.lcsc_part or "—"))

        ds_label = QLabel(dev.datasheet_url or "—")
        ds_label.setWordWrap(True)
        form.addRow("Datasheet", ds_label)

        form.addRow("置信度", QLabel(f"{dev.confidence:.0%}"))

        notes_label = QLabel(dev.notes or "—")
        notes_label.setWordWrap(True)
        form.addRow("备注", notes_label)

        # --- 设计知识字段 ---
        sep = QLabel("— 设计知识 —")
        sep.setStyleSheet("font-weight: bold; margin-top: 12px;")
        form.addRow(sep)

        def _wrap_label(text: str) -> QLabel:
            lbl = QLabel(text)
            lbl.setWordWrap(True)
            return lbl

        form.addRow(
            "设计角色",
            _wrap_label(", ".join(dev.design_roles) if dev.design_roles else "—"),
        )
        form.addRow(
            "适用场景",
            _wrap_label(
                "\n".join(dev.selection_hints) if dev.selection_hints else "—"
            ),
        )
        form.addRow(
            "不适用场景",
            _wrap_label(
                "\n".join(dev.anti_patterns) if dev.anti_patterns else "—"
            ),
        )
        form.addRow(
            "必需外围件",
            _wrap_label(
                "\n".join(dev.required_companions)
                if dev.required_companions
                else "—"
            ),
        )
        form.addRow(
            "工作约束",
            _wrap_label(
                "\n".join(
                    f"{k}: {v}" for k, v in dev.operating_constraints.items()
                )
                if dev.operating_constraints
                else "—"
            ),
        )
        form.addRow(
            "布局提示",
            _wrap_label(
                "\n".join(dev.layout_hints) if dev.layout_hints else "—"
            ),
        )
        form.addRow(
            "常见误用",
            _wrap_label(
                "\n".join(dev.failure_modes) if dev.failure_modes else "—"
            ),
        )
        form.addRow(
            "审查规则",
            _wrap_label(
                "\n".join(dev.review_rules) if dev.review_rules else "—"
            ),
        )

        layout.addLayout(form)

        # --- 符号编辑器 ---
        sym_sep = QLabel("— 器件符号 —")
        sym_sep.setStyleSheet("font-weight: bold; margin-top: 12px;")
        layout.addWidget(sym_sep)

        self._symbol_editor = SymbolEditorWidget()
        self._symbol_editor.setMinimumHeight(400)
        self._symbol_editor.symbol_saved.connect(self._on_symbol_saved)
        self._symbol_editor.load_device(dev.part_number, dev.symbol)
        layout.addWidget(self._symbol_editor, 1)

        scroll.setWidget(container)
        root.addWidget(scroll, 1)

    def _on_symbol_saved(self, part_number: str, symbol: object) -> None:
        """保存符号到库并向外发射信号。"""
        if not isinstance(symbol, SymbolDef):
            return
        ok = self._service.update_device_symbol(part_number, symbol)
        if ok:
            QMessageBox.information(
                self, "保存成功", f"器件 {part_number} 的符号已更新。"
            )
            self.symbol_saved.emit(part_number, symbol)
        else:
            QMessageBox.warning(
                self, "保存失败", f"无法更新器件 {part_number} 的符号。"
            )


# ================================================================
# ManualEntryDialog — 手动录入弹窗
# ================================================================


class ManualEntryDialog(QDialog):
    """手动录入器件弹窗。"""

    device_added = Signal()

    def __init__(
        self,
        service: LibraryService,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._service = service
        self.setWindowTitle("手动录入器件")
        self.resize(600, 700)
        self.setWindowFlags(
            self.windowFlags() | Qt.WindowType.WindowMaximizeButtonHint
        )
        self._init_ui()
        self._connect_signals()

    def _init_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        container = QWidget()
        layout = QVBoxLayout(container)

        form = QFormLayout()

        self._f_part_number = QLineEdit()
        self._f_part_number.setPlaceholderText("如 TPS54202")
        form.addRow("料号 *", self._f_part_number)

        self._f_category = QComboBox()
        self._f_category.addItem("— 选择类别 —", "")
        for cat in sorted(VALID_CATEGORIES):
            self._f_category.addItem(cat, cat)
        form.addRow("类别 *", self._f_category)

        self._f_manufacturer = QLineEdit()
        form.addRow("制造商", self._f_manufacturer)

        self._f_package = QLineEdit()
        form.addRow("封装", self._f_package)

        self._f_source = QLineEdit()
        self._f_source.setText("manual")
        form.addRow("来源", self._f_source)

        self._f_description = QLineEdit()
        form.addRow("描述", self._f_description)

        self._f_lcsc_part = QLineEdit()
        form.addRow("LCSC编号", self._f_lcsc_part)

        self._f_datasheet_url = QLineEdit()
        form.addRow("Datasheet URL", self._f_datasheet_url)

        self._f_notes = QTextEdit()
        self._f_notes.setMaximumHeight(60)
        form.addRow("备注", self._f_notes)

        layout.addLayout(form)

        # 引脚表
        pin_label = QLabel("引脚定义")
        pin_label.setStyleSheet("font-weight: bold; margin-top: 8px;")
        layout.addWidget(pin_label)

        self._pin_table = QTableView()
        self._pin_model = QStandardItemModel()
        self._pin_model.setHorizontalHeaderLabels(
            ["引脚名", "编号", "类型", "方位", "描述"]
        )
        self._pin_table.setModel(self._pin_model)
        self._pin_table.horizontalHeader().setStretchLastSection(True)
        self._pin_table.setMaximumHeight(180)
        layout.addWidget(self._pin_table)

        pin_btn_row = QHBoxLayout()
        self._btn_add_pin = QPushButton("+ 添加引脚")
        self._btn_remove_pin = QPushButton("− 移除选中")
        pin_btn_row.addWidget(self._btn_add_pin)
        pin_btn_row.addWidget(self._btn_remove_pin)
        pin_btn_row.addStretch()
        layout.addLayout(pin_btn_row)

        # 操作按钮
        action_row = QHBoxLayout()
        self._btn_submit = QPushButton("📥 入库")
        self._btn_submit.setMinimumHeight(36)
        self._btn_clear_form = QPushButton("🧹 清空表单")
        action_row.addWidget(self._btn_submit)
        action_row.addWidget(self._btn_clear_form)
        layout.addLayout(action_row)

        self._form_status = QLabel("")
        layout.addWidget(self._form_status)
        layout.addStretch()

        scroll.setWidget(container)
        root.addWidget(scroll, 1)

    def _connect_signals(self) -> None:
        self._btn_add_pin.clicked.connect(self._on_add_pin)
        self._btn_remove_pin.clicked.connect(self._on_remove_pin)
        self._btn_submit.clicked.connect(self._on_submit_draft)
        self._btn_clear_form.clicked.connect(self._clear_form)

    # --- 引脚管理 ---

    @Slot()
    def _on_add_pin(self) -> None:
        row_items = [QStandardItem("") for _ in range(5)]
        self._pin_model.appendRow(row_items)

    @Slot()
    def _on_remove_pin(self) -> None:
        indexes = self._pin_table.selectedIndexes()
        if indexes:
            rows = sorted({idx.row() for idx in indexes}, reverse=True)
            for r in rows:
                self._pin_model.removeRow(r)

    # --- 入库 ---

    @Slot()
    def _on_submit_draft(self) -> None:
        part_number = self._f_part_number.text().strip()
        if not part_number:
            self._form_status.setText("⚠ 料号不能为空")
            return

        category = self._f_category.currentData() or ""
        if not category:
            self._form_status.setText("⚠ 请选择器件类别")
            return

        # 收集引脚
        pins: list[PinDraft] = []
        for row_idx in range(self._pin_model.rowCount()):
            pin_name = (
                self._pin_model.item(row_idx, 0) or QStandardItem()
            ).text()
            pin_number = (
                self._pin_model.item(row_idx, 1) or QStandardItem()
            ).text()
            pin_type = (
                self._pin_model.item(row_idx, 2) or QStandardItem()
            ).text()
            pin_side = (
                self._pin_model.item(row_idx, 3) or QStandardItem()
            ).text()
            pin_desc = (
                self._pin_model.item(row_idx, 4) or QStandardItem()
            ).text()
            pins.append(
                PinDraft(
                    name=pin_name,
                    number=pin_number,
                    pin_type=pin_type,
                    side=pin_side,
                    description=pin_desc,
                )
            )

        draft = DeviceDraft(
            part_number=part_number,
            category=category,
            manufacturer=self._f_manufacturer.text().strip(),
            package=self._f_package.text().strip(),
            source=self._f_source.text().strip() or "manual",
            description=self._f_description.text().strip(),
            lcsc_part=self._f_lcsc_part.text().strip(),
            datasheet_url=self._f_datasheet_url.text().strip(),
            notes=self._f_notes.toPlainText().strip(),
            pins=pins,
            pin_count=len(pins),
        )

        result = self._service.add_device_from_draft(draft)

        if result.success:
            self._form_status.setText(
                f"✅ 入库成功: {result.device.part_number}"
                if result.device
                else "✅ 入库成功"
            )
            self._clear_form()
            self.device_added.emit()
        else:
            self._form_status.setText(f"❌ {result.error_message}")

    @Slot()
    def _clear_form(self) -> None:
        self._f_part_number.clear()
        self._f_category.setCurrentIndex(0)
        self._f_manufacturer.clear()
        self._f_package.clear()
        self._f_source.setText("manual")
        self._f_description.clear()
        self._f_lcsc_part.clear()
        self._f_datasheet_url.clear()
        self._f_notes.clear()
        self._pin_model.setRowCount(0)
        self._form_status.clear()

    # --- 预填充（从 EasyEDA 加载） ---

    def prefill(self, draft: DeviceDraft) -> None:
        """用已有草稿预填充表单。"""
        self._f_part_number.setText(draft.part_number)
        self._f_manufacturer.setText(draft.manufacturer)
        self._f_description.setText(draft.description)
        self._f_package.setText(draft.package)
        self._f_lcsc_part.setText(draft.lcsc_part)
        self._f_datasheet_url.setText(draft.datasheet_url)
        self._f_source.setText(draft.source)
        self._f_notes.setPlainText(draft.notes)
        self._form_status.setText("已加载数据，请检查并补充后入库")


# ================================================================
# EasyEDADialog — EasyEDA 搜索导入弹窗
# ================================================================


class EasyEDADialog(QDialog):
    """EasyEDA / JLCPCB 搜索导入弹窗。

    使用 JLCPCB API 搜索器件（含价格/库存），
    支持通过 EasyEDA 产品 API 获取完整引脚符号。
    """

    device_imported = Signal()

    def __init__(
        self,
        service: LibraryService,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._service = service
        self._hits: list[EasyEDAHit] = []
        self.setWindowTitle("EasyEDA / JLCPCB 搜索导入")
        self.resize(750, 650)
        self.setWindowFlags(
            self.windowFlags() | Qt.WindowType.WindowMaximizeButtonHint
        )
        self._init_ui()
        self._connect_signals()

    def _init_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)

        # 搜索行
        search_row = QHBoxLayout()
        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("输入器件型号搜索 JLCPCB…")
        self._btn_search = QPushButton("🔍 搜索")
        search_row.addWidget(self._search_input, stretch=1)
        search_row.addWidget(self._btn_search)
        root.addLayout(search_row)

        self._status_label = QLabel("")
        root.addWidget(self._status_label)

        # 结果列表
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._results_container = QWidget()
        self._results_layout = QVBoxLayout(self._results_container)
        self._results_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._scroll.setWidget(self._results_container)
        root.addWidget(self._scroll, stretch=1)

    def _connect_signals(self) -> None:
        self._btn_search.clicked.connect(self._on_search)
        self._search_input.returnPressed.connect(self._on_search)

    @Slot()
    def _on_search(self) -> None:
        keyword = self._search_input.text().strip()
        if not keyword:
            self._status_label.setText("⚠ 请输入搜索关键词")
            return

        self._status_label.setText("正在搜索…")
        self._clear_results()

        tool_result = search_easyeda(keyword)

        if not tool_result.success:
            error_msg = (
                tool_result.error.message if tool_result.error else "未知错误"
            )
            self._status_label.setText(f"❌ 搜索失败: {error_msg}")
            return

        hits: list[EasyEDAHit] = tool_result.data if tool_result.data else []
        self._hits = hits

        if not hits:
            self._status_label.setText("未找到匹配结果")
            return

        self._status_label.setText(f"找到 {len(hits)} 个结果")

        for i, hit in enumerate(hits):
            card = self._create_hit_card(hit, i)
            self._results_layout.addWidget(card)

    def _clear_results(self) -> None:
        while self._results_layout.count():
            child = self._results_layout.takeAt(0)
            if child is not None:
                w = child.widget()
                if w is not None:
                    w.deleteLater()
        self._hits = []

    def _create_hit_card(self, hit: EasyEDAHit, index: int) -> QFrame:
        card = QFrame()
        card.setFrameShape(QFrame.Shape.StyledPanel)
        card.setStyleSheet(
            "QFrame { border: 1px solid #555; border-radius: 4px; "
            "padding: 8px; margin: 2px 0; }"
        )
        layout = QVBoxLayout(card)
        layout.setContentsMargins(8, 6, 8, 6)

        # 标题行
        title = QLabel(f"<b>{hit.title}</b>")
        layout.addWidget(title)

        # 信息行: 制造商 | 封装 | LCSC | 分类
        info_parts: list[str] = []
        if hit.manufacturer:
            info_parts.append(f"制造商: {hit.manufacturer}")
        if hit.package:
            info_parts.append(f"封装: {hit.package}")
        if hit.lcsc_part:
            info_parts.append(f"LCSC: {hit.lcsc_part}")
        if hit.category_name:
            info_parts.append(f"分类: {hit.category_name}")
        if info_parts:
            info_label = QLabel(" | ".join(info_parts))
            info_label.setStyleSheet("color: #aaa;")
            layout.addWidget(info_label)

        # 库存/价格行
        stock_price_parts: list[str] = []
        if hit.stock > 0:
            stock_text = f"<span style='color: #4caf50;'>● 库存: {hit.stock}</span>"
        else:
            stock_text = "<span style='color: #f44336;'>● 无库存</span>"
        stock_price_parts.append(stock_text)
        if hit.price_range:
            stock_price_parts.append(f"价格: {hit.price_range}")
        if hit.library_type:
            lib_label = "基础库" if hit.library_type == "base" else "扩展库"
            stock_price_parts.append(f"[{lib_label}]")
        if hit.pin_count > 0:
            stock_price_parts.append(f"引脚: {hit.pin_count}")

        stock_label = QLabel(" | ".join(stock_price_parts))
        stock_label.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(stock_label)

        # 描述
        if hit.description:
            desc = QLabel(hit.description[:150])
            desc.setWordWrap(True)
            layout.addWidget(desc)

        # 按钮行
        btn_row = QHBoxLayout()
        btn_load = QPushButton("📋 加载到表单")
        btn_direct = QPushButton("📥 直接入库")
        btn_symbol = QPushButton("🔍 获取符号")
        btn_row.addWidget(btn_load)
        btn_row.addWidget(btn_direct)
        btn_row.addWidget(btn_symbol)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        # 仅在有 LCSC 编号时启用获取符号按钮
        btn_symbol.setEnabled(bool(hit.lcsc_part))
        if not hit.lcsc_part:
            btn_symbol.setToolTip("需要 LCSC 编号才能获取符号")

        btn_load.clicked.connect(
            lambda _checked, idx=index: self._load_hit_to_form(idx)
        )
        btn_direct.clicked.connect(
            lambda _checked, idx=index: self._direct_import_hit(idx)
        )
        btn_symbol.clicked.connect(
            lambda _checked, idx=index: self._fetch_symbol(idx)
        )

        return card

    def _load_hit_to_form(self, index: int) -> None:
        if index < 0 or index >= len(self._hits):
            return
        hit = self._hits[index]
        draft = self._service.easyeda_hit_to_draft(hit)

        dlg = ManualEntryDialog(self._service, parent=self)
        dlg.prefill(draft)
        dlg.device_added.connect(self._on_sub_device_added)
        dlg.show()

    def _direct_import_hit(self, index: int) -> None:
        if index < 0 or index >= len(self._hits):
            return
        hit = self._hits[index]
        draft = self._service.easyeda_hit_to_draft(hit)
        draft.source = "easyeda"

        result = self._service.add_device_from_draft(draft)

        if result.success:
            pn = result.device.part_number if result.device else hit.title
            self._status_label.setText(f"✅ 已入库: {pn}")
            self.device_imported.emit()
        else:
            self._status_label.setText(f"❌ 入库失败: {result.error_message}")

    def _fetch_symbol(self, index: int) -> None:
        """通过 EasyEDA 产品 API 获取完整符号并打开表单。"""
        if index < 0 or index >= len(self._hits):
            return
        hit = self._hits[index]
        if not hit.lcsc_part:
            self._status_label.setText("⚠ 该器件无 LCSC 编号，无法获取符号")
            return

        self._status_label.setText(f"正在获取 {hit.lcsc_part} 的符号数据…")

        result = fetch_easyeda_symbol(hit.lcsc_part)

        if not result.success:
            error_msg = (
                result.error.message if result.error else "未知错误"
            )
            self._status_label.setText(f"❌ 获取符号失败: {error_msg}")
            return

        symbol = result.data
        pin_count = len(symbol.pins) if symbol and symbol.pins else 0
        self._status_label.setText(
            f"✅ 已获取符号: {symbol.title} ({pin_count} 引脚)"
        )

        # 转换为 draft 并打开手动录入表单
        draft = self._service.easyeda_symbol_to_draft(symbol)
        # 补充搜索阶段的信息
        if hit.manufacturer and not draft.manufacturer:
            draft.manufacturer = hit.manufacturer
        if hit.datasheet_url and not draft.datasheet_url:
            draft.datasheet_url = hit.datasheet_url

        dlg = ManualEntryDialog(self._service, parent=self)
        dlg.prefill(draft)
        dlg.device_added.connect(self._on_sub_device_added)
        dlg.show()

    def _on_sub_device_added(self) -> None:
        """ManualEntryDialog 入库成功后通知父级。"""
        self.device_imported.emit()


# ================================================================
# _ExtractWorker — 后台提取线程
# ================================================================


class _ExtractWorker(QThread):
    """后台执行 PDF/图片提取，通过信号汇报进度。"""

    finished = Signal(object)    # ExtractionResult
    error = Signal(str)          # 异常描述
    progress = Signal(str, int)  # (阶段名, 百分比)

    def __init__(
        self,
        pdf_path: str,
        image_paths: list[str],
        hint: str,
        parent: QThread | None = None,
    ) -> None:
        super().__init__(parent)  # type: ignore[arg-type]
        self._pdf_path = pdf_path
        self._image_paths = image_paths
        self._hint = hint

    def run(self) -> None:
        try:
            from schemaforge.common.progress import ProgressTracker

            tracker = ProgressTracker(
                on_event=self._on_event,
                source="pdf_import",
            )

            if self._pdf_path:
                result = extract_from_pdf(
                    filepath=self._pdf_path,
                    hint=self._hint,
                    extra_images=self._image_paths or None,
                    tracker=tracker,
                )
            elif self._image_paths:
                result = extract_from_image(
                    image_source=self._image_paths[0],
                    hint=self._hint,
                    tracker=tracker,
                )
            else:
                from schemaforge.ingest.datasheet_extractor import ExtractionResult
                result = ExtractionResult(error_message="未提供 PDF 或图片")
                self.finished.emit(result)
                return

            self.progress.emit("提取完成", 100)
            self.finished.emit(result)
        except Exception as exc:
            tb = traceback.format_exc()
            self.error.emit(f"{exc}\n\n{tb}")

    def _on_event(self, event: object) -> None:
        """ProgressTracker 回调 → 转为 Qt Signal。"""
        from schemaforge.common.events import ProgressEvent

        if isinstance(event, ProgressEvent):
            self.progress.emit(event.message, event.percentage)


# ================================================================
# _SymbolChatWorker — AI 符号调整对话线程
# ================================================================


_SYMBOL_CHAT_SYSTEM = """\
你是器件符号布局助手。用户正在调整 {part_number} ({category}) 的原理图符号。

当前引脚布局:
{pin_layout}

用户可能会要求:
- 移动引脚到不同的边 (左/右/上/下)
- 调整引脚顺序
- 修改引脚名称
- 其他符号相关调整

请先用中文解释你的理解和建议，然后输出修改指令 JSON:
{{"modifications": [{{"pin_name": "引脚名", "action": "move", "new_side": "left/right/top/bottom"}}]}}

如果用户只是在聊天没有要修改，正常回复不需要输出 JSON。
"""


class _SymbolChatWorker(QThread):
    """后台执行 AI 符号调整对话。"""

    response_ready = Signal(str)  # AI response text
    error = Signal(str)

    def __init__(
        self,
        system_prompt: str,
        user_message: str,
        parent: QThread | None = None,
    ) -> None:
        super().__init__(parent)  # type: ignore[arg-type]
        self._system_prompt = system_prompt
        self._user_message = user_message

    def run(self) -> None:
        try:
            from schemaforge.ai.client import call_llm

            result = call_llm(
                self._system_prompt,
                self._user_message,
                temperature=0.3,
            )
            self.response_ready.emit(result)
        except Exception as exc:
            self.error.emit(str(exc))


# ================================================================
# PdfImportDialog — PDF/图片导入弹窗（提取 + 符号预览 + AI 对话）
# ================================================================


class PdfImportDialog(QDialog):
    """PDF / 图片导入弹窗。

    Phase 1: 选择文件 → 提取
    Phase 2: 符号预览 + AI 对话微调 → 确认入库
    """

    device_imported = Signal()

    def __init__(
        self,
        service: LibraryService,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._service = service

        # --- 状态 ---
        self._phase: int = 1
        self._pdf_path: str = ""
        self._image_paths: list[str] = []
        self._last_result: ExtractionResult | None = None
        self._current_symbol: SymbolDef | None = None
        self._current_draft: DeviceDraft | None = None
        self._chat_worker: _SymbolChatWorker | None = None
        self._extract_worker: _ExtractWorker | None = None

        self.setWindowTitle("PDF / 图片导入")
        self.resize(800, 700)
        self.setWindowFlags(
            self.windowFlags() | Qt.WindowType.WindowMaximizeButtonHint
        )
        self._init_ui()
        self._connect_signals()

    # ==============================================================
    # UI 构建
    # ==============================================================

    def _init_ui(self) -> None:
        self._root = QVBoxLayout(self)
        self._root.setContentsMargins(12, 12, 12, 12)

        # ---- Phase 1 容器 ----
        self._phase1_container = QWidget()
        p1 = QVBoxLayout(self._phase1_container)
        p1.setContentsMargins(0, 0, 0, 0)

        # PDF 选择
        pdf_section = QLabel("📄 PDF Datasheet")
        pdf_section.setStyleSheet("font-weight: bold; font-size: 14px;")
        p1.addWidget(pdf_section)

        pdf_row = QHBoxLayout()
        self._pdf_path_label = QLineEdit()
        self._pdf_path_label.setPlaceholderText("选择 PDF 文件…")
        self._pdf_path_label.setReadOnly(True)
        self._btn_choose_pdf = QPushButton("浏览…")
        pdf_row.addWidget(self._pdf_path_label, stretch=1)
        pdf_row.addWidget(self._btn_choose_pdf)
        p1.addLayout(pdf_row)

        # 图片上传
        img_section = QLabel("🖼 引脚图/封装图（可多张）")
        img_section.setStyleSheet(
            "font-weight: bold; font-size: 14px; margin-top: 8px;"
        )
        p1.addWidget(img_section)

        img_row = QHBoxLayout()
        self._btn_add_images = QPushButton("添加图片…")
        self._btn_clear_images = QPushButton("清除全部")
        img_row.addWidget(self._btn_add_images)
        img_row.addWidget(self._btn_clear_images)
        img_row.addStretch()
        p1.addLayout(img_row)

        self._image_list_label = QLabel("未选择图片")
        self._image_list_label.setStyleSheet("color: #888;")
        self._image_list_label.setWordWrap(True)
        p1.addWidget(self._image_list_label)

        # 器件型号提示
        hint_row = QHBoxLayout()
        hint_label = QLabel("器件型号提示:")
        self._hint_input = QLineEdit()
        self._hint_input.setPlaceholderText(
            "如 TPS54202（可选，提高识别准确率）"
        )
        hint_row.addWidget(hint_label)
        hint_row.addWidget(self._hint_input, stretch=1)
        p1.addLayout(hint_row)

        # 提取按钮
        self._btn_extract = QPushButton("🚀 开始提取")
        self._btn_extract.setMinimumHeight(36)
        p1.addWidget(self._btn_extract)

        # 进度条
        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setVisible(False)
        p1.addWidget(self._progress_bar)

        self._progress_stage = QLabel("")
        self._progress_stage.setStyleSheet("color: #888; font-size: 12px;")
        self._progress_stage.setVisible(False)
        p1.addWidget(self._progress_stage)

        # 结果展示
        result_section = QLabel("— 提取结果 —")
        result_section.setStyleSheet(
            "font-weight: bold; font-size: 14px; margin-top: 12px;"
        )
        p1.addWidget(result_section)

        self._result_scroll = QScrollArea()
        self._result_scroll.setWidgetResizable(True)
        self._result_container = QWidget()
        self._result_layout = QVBoxLayout(self._result_container)
        self._result_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._result_scroll.setWidget(self._result_container)
        p1.addWidget(self._result_scroll, stretch=1)

        self._root.addWidget(self._phase1_container)

        # ---- Phase 2 容器 ----
        self._phase2_container = QWidget()
        p2 = QVBoxLayout(self._phase2_container)
        p2.setContentsMargins(0, 0, 0, 0)

        # 标题行
        self._phase2_title = QLabel("📄 — 符号预览与调整")
        self._phase2_title.setStyleSheet(
            "font-weight: bold; font-size: 15px;"
        )
        p2.addWidget(self._phase2_title)

        # 摘要行
        self._phase2_summary = QLabel("")
        self._phase2_summary.setStyleSheet(
            "color: #aaa; font-size: 12px; margin-bottom: 4px;"
        )
        p2.addWidget(self._phase2_summary)

        # 分栏: 左=符号编辑器, 右=AI 聊天
        self._splitter = QSplitter(Qt.Orientation.Horizontal)

        self._symbol_editor = SymbolEditorWidget()
        self._symbol_editor.setMinimumWidth(400)

        self._chat_panel = ChatPanel()
        self._chat_panel.setMinimumWidth(300)

        self._splitter.addWidget(self._symbol_editor)
        self._splitter.addWidget(self._chat_panel)
        self._splitter.setStretchFactor(0, 3)
        self._splitter.setStretchFactor(1, 2)

        p2.addWidget(self._splitter, stretch=1)

        self._phase2_container.setVisible(False)
        self._root.addWidget(self._phase2_container, stretch=1)

        # ---- 状态行 ----
        self._result_status = QLabel("")
        self._root.addWidget(self._result_status)

        # ---- 底部操作 ----
        bottom_row = QHBoxLayout()
        self._btn_confirm = QPushButton("📥 确认入库")
        self._btn_confirm.setMinimumHeight(36)
        self._btn_confirm.setEnabled(False)
        self._btn_reextract = QPushButton("🔄 重新提取")
        self._btn_reextract.setMinimumHeight(36)
        self._btn_reextract.setVisible(False)
        self._btn_reset = QPushButton("🔄 重置")
        bottom_row.addWidget(self._btn_confirm)
        bottom_row.addWidget(self._btn_reextract)
        bottom_row.addWidget(self._btn_reset)
        bottom_row.addStretch()
        self._root.addLayout(bottom_row)

    # ==============================================================
    # 信号连接
    # ==============================================================

    def _connect_signals(self) -> None:
        self._btn_choose_pdf.clicked.connect(self._on_choose_pdf)
        self._btn_add_images.clicked.connect(self._on_add_images)
        self._btn_clear_images.clicked.connect(self._on_clear_images)
        self._btn_extract.clicked.connect(self._on_extract)
        self._btn_confirm.clicked.connect(self._on_confirm)
        self._btn_reextract.clicked.connect(self._on_reextract)
        self._btn_reset.clicked.connect(self._on_reset)
        self._chat_panel.message_sent.connect(self._on_chat_send)

    # ==============================================================
    # Phase 切换
    # ==============================================================

    def _switch_to_phase2(self) -> None:
        """从 Phase 1 切换到 Phase 2 (符号预览 + AI 对话)。"""
        self._phase = 2
        self._phase1_container.setVisible(False)
        self._phase2_container.setVisible(True)
        self._btn_reextract.setVisible(True)
        self.resize(1200, 800)

        draft = self._current_draft
        if not draft:
            return

        pn = draft.part_number or "未知器件"
        self._phase2_title.setText(f"📄 {pn} — 符号预览与调整")

        # 构建摘要
        parts = []
        if draft.pin_count:
            parts.append(f"{draft.pin_count}引脚")
        if draft.confidence:
            parts.append(f"置信度{draft.confidence:.0%}")
        if draft.manufacturer:
            parts.append(draft.manufacturer)
        if draft.package:
            parts.append(draft.package)
        summary = ", ".join(parts) if parts else ""
        self._phase2_summary.setText(f"— 提取摘要: {summary} —")

        # 加载符号到编辑器
        if self._current_symbol:
            self._symbol_editor.load_device(pn, self._current_symbol)

        # AI 初始消息
        self._chat_panel.clear()
        pin_summary = self._format_pin_layout(self._current_symbol)
        self._chat_panel.add_message(
            "ai",
            f"已为 {pn} 生成符号。{pin_summary}\n\n"
            "你可以要求我调整引脚位置、重命名引脚等。"
            "例如：「把CLK移到左边」「把所有电源引脚放到上面」",
        )

    def _switch_to_phase1(self) -> None:
        """从 Phase 2 回到 Phase 1。"""
        self._phase = 1
        self._phase2_container.setVisible(False)
        self._phase1_container.setVisible(True)
        self._btn_reextract.setVisible(False)
        self._btn_confirm.setEnabled(False)
        self._current_symbol = None
        self._current_draft = None
        self._symbol_editor.clear()
        self._chat_panel.clear()
        self.resize(800, 700)

    # ==============================================================
    # 文件选择
    # ==============================================================

    @Slot()
    def _on_choose_pdf(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 PDF 文件", "", "PDF 文件 (*.pdf)"
        )
        if path:
            self._pdf_path = path
            self._pdf_path_label.setText(path)

    @Slot()
    def _on_add_images(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "选择引脚图/封装图",
            "",
            "图片文件 (*.png *.jpg *.jpeg *.bmp *.webp)",
        )
        if paths:
            self._image_paths.extend(paths)
            names = [Path(p).name for p in self._image_paths]
            self._image_list_label.setText(
                f"已选 {len(self._image_paths)} 张: {', '.join(names)}"
            )

    @Slot()
    def _on_clear_images(self) -> None:
        self._image_paths.clear()
        self._image_list_label.setText("未选择图片")

    # ==============================================================
    # Phase 1: 提取
    # ==============================================================

    @Slot()
    def _on_extract(self) -> None:
        hint = self._hint_input.text().strip()

        # 清空旧结果
        self._clear_result_display()
        self._btn_confirm.setEnabled(False)
        self._last_result = None

        has_pdf = bool(self._pdf_path)
        has_images = bool(self._image_paths)

        if not has_pdf and not has_images:
            self._result_status.setText("⚠ 请先选择 PDF 文件或添加图片")
            return

        self._result_status.setText("正在提取…")
        self._btn_extract.setEnabled(False)
        self._progress_bar.setValue(0)
        self._progress_bar.setVisible(True)
        self._progress_stage.setVisible(True)

        self._extract_worker = _ExtractWorker(
            self._pdf_path, self._image_paths, hint,
        )
        self._extract_worker.progress.connect(self._on_progress)
        self._extract_worker.finished.connect(self._on_extract_done)
        self._extract_worker.error.connect(self._on_extract_error)
        self._extract_worker.start()

    @Slot(str, int)
    def _on_progress(self, stage: str, pct: int) -> None:
        self._progress_bar.setValue(pct)
        self._progress_stage.setText(stage)

    @Slot(object)
    def _on_extract_done(self, extraction: ExtractionResult) -> None:
        self._btn_extract.setEnabled(True)
        self._progress_bar.setVisible(False)
        self._progress_stage.setVisible(False)
        self._last_result = extraction

        if not extraction.success:
            self._result_status.setText(
                f"❌ 提取失败: {extraction.error_message}"
            )
            return

        # 显示提取摘要
        self._show_extraction_result(extraction)

        draft = extraction.draft
        if draft and draft.pins:
            self._current_draft = draft
            # 自动构建符号 → Phase 2
            try:
                self._build_and_enter_phase2(draft)
            except Exception as exc:
                logger.exception("符号构建失败")
                self._result_status.setText(
                    f"⚠ 符号构建失败: {exc}，可直接入库"
                )
                self._btn_confirm.setEnabled(True)
        elif draft:
            # 有草稿但无引脚 → 不进入 Phase 2
            self._current_draft = draft
            self._btn_confirm.setEnabled(True)
            self._result_status.setText(
                "✅ 提取完成（无引脚数据，跳过符号预览）"
            )
        else:
            self._result_status.setText("❌ 提取未生成有效草稿")

    @Slot(str)
    def _on_extract_error(self, msg: str) -> None:
        self._btn_extract.setEnabled(True)
        self._progress_bar.setVisible(False)
        self._progress_stage.setVisible(False)
        logger.error("提取异常: %s", msg)
        self._result_status.setText(f"❌ 提取异常: {msg.split(chr(10))[0]}")

    def _build_and_enter_phase2(self, draft: DeviceDraft) -> None:
        """构建符号并切换到 Phase 2。"""
        from schemaforge.library.symbol_builder import build_symbol

        pins_data = [
            {
                "name": p.name,
                "number": p.number,
                "type": p.pin_type,
                "description": p.description,
            }
            for p in draft.pins
        ]

        symbol = build_symbol(
            part_number=draft.part_number,
            pins_data=pins_data,
            category=draft.category,
            package=draft.package,
        )
        self._current_symbol = symbol
        self._btn_confirm.setEnabled(True)
        self._result_status.setText("✅ 符号已生成，可在右侧调整后入库")
        self._switch_to_phase2()

    # ==============================================================
    # 结果展示 (Phase 1)
    # ==============================================================

    def _show_extraction_result(self, result: ExtractionResult) -> None:
        """在结果区域展示提取摘要。"""
        self._clear_result_display()

        if result.pdf_result:
            src = QLabel(
                f"📄 来源: PDF ({result.pdf_result.total_pages} 页)"
            )
            self._result_layout.addWidget(src)

        if result.text_analysis:
            ta = result.text_analysis
            conf = QLabel(f"🎯 置信度: {ta.confidence:.0%}")
            self._result_layout.addWidget(conf)

        if result.image_analysis:
            ia = result.image_analysis
            img_info = QLabel(
                f"🖼 图片识别: {ia.pin_count} 个引脚, "
                f"置信度: {ia.confidence:.0%}"
            )
            self._result_layout.addWidget(img_info)

        draft = result.draft
        if draft:
            sep = QLabel("— 提取数据 —")
            sep.setStyleSheet("font-weight: bold; margin-top: 8px;")
            self._result_layout.addWidget(sep)

            fields = [
                ("料号", draft.part_number),
                ("类别", draft.category),
                ("制造商", draft.manufacturer),
                ("封装", draft.package),
                ("描述", draft.description),
                ("引脚数", str(draft.pin_count)),
            ]
            for label_text, value in fields:
                if value:
                    row_lbl = QLabel(f"  {label_text}: {value}")
                    self._result_layout.addWidget(row_lbl)

        if result.needs_user_input and result.questions:
            q_sep = QLabel("⚠ 以下信息需要确认:")
            q_sep.setStyleSheet(
                "font-weight: bold; color: #e8a838; margin-top: 8px;"
            )
            self._result_layout.addWidget(q_sep)
            for q in result.questions:
                q_text = q.get("question", str(q))
                self._result_layout.addWidget(QLabel(f"  • {q_text}"))

    def _clear_result_display(self) -> None:
        while self._result_layout.count():
            child = self._result_layout.takeAt(0)
            if child is not None:
                w = child.widget()
                if w is not None:
                    w.deleteLater()

    # ==============================================================
    # Phase 2: AI 对话
    # ==============================================================

    @Slot(str)
    def _on_chat_send(self, message: str) -> None:
        """用户在聊天面板发送消息。"""
        if not message.strip():
            return

        self._chat_panel.add_message("user", message)

        draft = self._current_draft
        symbol = self._current_symbol
        if not draft or not symbol:
            self._chat_panel.add_message("system", "⚠ 无活跃符号，请先提取")
            return

        # 构建 system prompt
        pin_layout = self._format_pin_layout(symbol)
        system_prompt = _SYMBOL_CHAT_SYSTEM.format(
            part_number=draft.part_number,
            category=draft.category or "通用",
            pin_layout=pin_layout,
        )

        self._chat_worker = _SymbolChatWorker(system_prompt, message)
        self._chat_worker.response_ready.connect(self._on_chat_response)
        self._chat_worker.error.connect(self._on_chat_error)
        self._chat_worker.start()

    @Slot(str)
    def _on_chat_response(self, ai_text: str) -> None:
        """AI 回复到达。"""
        # 尝试解析修改指令
        applied = self._try_apply_modifications(ai_text)

        # 显示 AI 回复
        self._chat_panel.add_message("ai", ai_text)

        if applied:
            self._chat_panel.add_message("system", "✅ 修改已应用到符号预览")

    @Slot(str)
    def _on_chat_error(self, msg: str) -> None:
        self._chat_panel.add_message("system", f"❌ AI 调用失败: {msg}")

    def _try_apply_modifications(self, ai_text: str) -> bool:
        """尝试从 AI 回复中解析并应用引脚修改。"""
        from schemaforge.ai.client import _extract_json

        parsed = _extract_json(ai_text)
        if not parsed or "modifications" not in parsed:
            return False

        symbol = self._current_symbol
        if not symbol:
            return False

        side_map = {
            "left": PinSide.LEFT,
            "right": PinSide.RIGHT,
            "top": PinSide.TOP,
            "bottom": PinSide.BOTTOM,
        }

        changed = False
        for mod in parsed["modifications"]:
            pin_name = mod.get("pin_name", "")
            new_side = mod.get("new_side", "")

            if not pin_name or new_side not in side_map:
                continue

            for pin in symbol.pins:
                if pin.name.upper() == pin_name.upper():
                    pin.side = side_map[new_side]
                    changed = True
                    break

        if changed:
            # 重新构建符号以更新槽位和尺寸
            self._rebuild_symbol_layout()
            # 刷新预览
            pn = (
                self._current_draft.part_number
                if self._current_draft
                else ""
            )
            self._symbol_editor.load_device(pn, self._current_symbol)

        return changed

    def _rebuild_symbol_layout(self) -> None:
        """根据当前引脚方位重新计算符号尺寸和槽位。"""
        if not self._current_symbol:
            return

        from collections import Counter

        from schemaforge.library.symbol_builder import (
            assign_slots,
            auto_body_size,
        )

        symbol = self._current_symbol
        symbol.pins = assign_slots(symbol.pins)

        # auto_body_size 需要 pins_per_side dict
        counts: dict[PinSide, int] = Counter(p.side for p in symbol.pins)
        w, h = auto_body_size(counts)
        symbol.size = (w, h)

    # ==============================================================
    # 辅助方法
    # ==============================================================

    @staticmethod
    def _format_pin_layout(symbol: SymbolDef | None) -> str:
        """将符号引脚布局格式化为可读文本。"""
        if not symbol or not symbol.pins:
            return "无引脚"

        sides: dict[str, list[str]] = {
            "左": [], "右": [], "上": [], "下": [],
        }
        side_labels = {
            PinSide.LEFT: "左",
            PinSide.RIGHT: "右",
            PinSide.TOP: "上",
            PinSide.BOTTOM: "下",
        }
        for pin in symbol.pins:
            label = side_labels.get(pin.side, "左")
            sides[label].append(pin.name)

        parts = []
        for side_name, pins in sides.items():
            if pins:
                parts.append(f"{side_name}: {', '.join(pins)}")
        return "; ".join(parts)

    # ==============================================================
    # 入库
    # ==============================================================

    @Slot()
    def _on_confirm(self) -> None:
        draft = self._current_draft
        if not draft:
            # 回退: 尝试旧 result
            if self._last_result and self._last_result.draft:
                draft = self._last_result.draft
            else:
                self._result_status.setText("⚠ 无可用草稿")
                return

        result = self._service.add_device_from_draft(draft)

        if result.success:
            pn = (
                result.device.part_number
                if result.device
                else draft.part_number
            )
            # 保存符号
            if self._current_symbol:
                self._service.update_device_symbol(pn, self._current_symbol)

            self._result_status.setText(f"✅ 入库成功: {pn}")
            self._btn_confirm.setEnabled(False)
            self.device_imported.emit()
        else:
            self._result_status.setText(
                f"❌ 入库失败: {result.error_message}"
            )

    @Slot()
    def _on_reextract(self) -> None:
        """重新提取 — 回到 Phase 1。"""
        self._switch_to_phase1()
        self._result_status.setText("已回到提取阶段，可重新提取")

    @Slot()
    def _on_reset(self) -> None:
        if self._phase == 2:
            self._switch_to_phase1()
        self._pdf_path = ""
        self._pdf_path_label.clear()
        self._image_paths.clear()
        self._image_list_label.setText("未选择图片")
        self._hint_input.clear()
        self._clear_result_display()
        self._result_status.clear()
        self._btn_confirm.setEnabled(False)
        self._last_result = None
        self._current_symbol = None
        self._current_draft = None


# ================================================================
# LibraryPage — 主页面（全宽器件列表 + 操作按钮）
# ================================================================


class LibraryPage(QWidget):
    """器件库管理标签页。

    Layout::

        ┌──────────────────────────────────────────┐
        │ 📦 器件库管理                  共N个器件  │
        ├──────────────────────────────────────────┤
        │ [✏ 手动录入] [🔍 EasyEDA搜索] [📄 PDF导入] [🔄 刷新] │
        ├──────────────────────────────────────────┤
        │ 搜索器件（料号/类别/制造商）…              │
        ├──────────────────────────────────────────┤
        │ 全宽器件列表                              │
        ├──────────────────────────────────────────┤
        │ [🗑 删除选中]                             │
        └──────────────────────────────────────────┘
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._service = LibraryService(store_dir=_STORE_DIR)
        self._devices: list[DeviceModel] = []

        self._init_ui()
        self._connect_signals()
        self._refresh_table()

    # ==========================================================
    # UI 构建
    # ==========================================================

    def _init_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)

        # --- 标题栏 ---
        header = QHBoxLayout()
        icon_label = QLabel("📦 器件库管理")
        icon_label.setStyleSheet("font-weight: bold; font-size: 16px;")
        header.addWidget(icon_label)
        header.addStretch()
        self._count_label = QLabel("共 0 个器件")
        header.addWidget(self._count_label)
        root.addLayout(header)

        # --- 操作按钮行 ---
        action_bar = QHBoxLayout()
        self._btn_manual = QPushButton("✏ 手动录入")
        self._btn_easyeda = QPushButton("🔍 EasyEDA搜索")
        self._btn_pdf_import = QPushButton("📄 PDF导入")
        self._btn_refresh = QPushButton("🔄 刷新")

        for btn in (
            self._btn_manual,
            self._btn_easyeda,
            self._btn_pdf_import,
            self._btn_refresh,
        ):
            btn.setMinimumHeight(32)
            action_bar.addWidget(btn)

        action_bar.addStretch()
        root.addLayout(action_bar)

        # --- 搜索框 ---
        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("搜索器件（料号/类别/制造商）…")
        self._search_input.setClearButtonEnabled(True)
        root.addWidget(self._search_input)

        # --- 全宽器件列表 ---
        self._table_view = QTableView()
        self._table_model = QStandardItemModel()
        self._table_model.setHorizontalHeaderLabels(_TABLE_COLUMNS)
        self._table_view.setModel(self._table_model)
        self._table_view.setSelectionBehavior(
            QTableView.SelectionBehavior.SelectRows
        )
        self._table_view.setSelectionMode(
            QTableView.SelectionMode.SingleSelection
        )
        self._table_view.setEditTriggers(
            QTableView.EditTrigger.NoEditTriggers
        )
        self._table_view.horizontalHeader().setStretchLastSection(True)
        self._table_view.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Interactive
        )
        self._table_view.verticalHeader().setVisible(False)
        root.addWidget(self._table_view, stretch=1)

        # --- 底部删除按钮 ---
        bottom_bar = QHBoxLayout()
        self._btn_delete = QPushButton("🗑 删除选中")
        bottom_bar.addWidget(self._btn_delete)
        bottom_bar.addStretch()
        root.addLayout(bottom_bar)

    # ==========================================================
    # 信号连接
    # ==========================================================

    def _connect_signals(self) -> None:
        self._btn_refresh.clicked.connect(self._refresh_table)
        self._search_input.textChanged.connect(self._on_search_changed)
        self._btn_delete.clicked.connect(self._on_delete)

        # 操作按钮 → 弹窗
        self._btn_manual.clicked.connect(self._on_manual_entry)
        self._btn_easyeda.clicked.connect(self._on_easyeda_search)
        self._btn_pdf_import.clicked.connect(self._on_pdf_import)

        # 双击打开详情
        self._table_view.doubleClicked.connect(self._on_device_double_clicked)

    # ==========================================================
    # 数据刷新
    # ==========================================================

    @Slot()
    def _refresh_table(self) -> None:
        """刷新器件列表。"""
        query = self._search_input.text().strip()
        if query:
            self._devices = self._service.search(query=query)
        else:
            part_numbers = self._service.list_all()
            self._devices = []
            for pn in part_numbers:
                dev = self._service.get(pn)
                if dev:
                    self._devices.append(dev)

        self._populate_table()
        self._count_label.setText(f"共 {len(self._devices)} 个器件")

    def _populate_table(self) -> None:
        """将 self._devices 填充到表格模型。"""
        self._table_model.setRowCount(0)
        for dev in self._devices:
            row = [
                QStandardItem(dev.part_number),
                QStandardItem(dev.category),
                QStandardItem(dev.manufacturer),
                QStandardItem(dev.package),
                QStandardItem(dev.source),
            ]
            for item in row:
                item.setEditable(False)
            self._table_model.appendRow(row)

    # ==========================================================
    # 搜索
    # ==========================================================

    @Slot(str)
    def _on_search_changed(self, text: str) -> None:
        self._refresh_table()

    # ==========================================================
    # 双击 → 器件详情弹窗
    # ==========================================================

    @Slot()
    def _on_device_double_clicked(self, index: object) -> None:
        """双击表格行，打开器件详情弹窗。"""
        from PySide6.QtCore import QModelIndex

        if not isinstance(index, QModelIndex):
            return
        row = index.row()
        if 0 <= row < len(self._devices):
            dev = self._devices[row]
            dlg = DeviceDetailDialog(dev, self._service, parent=self)
            dlg.symbol_saved.connect(self._on_symbol_saved)
            dlg.show()

    # ==========================================================
    # 操作按钮 → 弹窗
    # ==========================================================

    @Slot()
    def _on_manual_entry(self) -> None:
        dlg = ManualEntryDialog(self._service, parent=self)
        dlg.device_added.connect(self._refresh_table)
        dlg.show()

    @Slot()
    def _on_easyeda_search(self) -> None:
        dlg = EasyEDADialog(self._service, parent=self)
        dlg.device_imported.connect(self._refresh_table)
        dlg.show()

    @Slot()
    def _on_pdf_import(self) -> None:
        dlg = PdfImportDialog(self._service, parent=self)
        dlg.device_imported.connect(self._refresh_table)
        dlg.show()

    # ==========================================================
    # 删除
    # ==========================================================

    @Slot()
    def _on_delete(self) -> None:
        indexes = self._table_view.selectedIndexes()
        if not indexes:
            return
        row = indexes[0].row()
        if 0 <= row < len(self._devices):
            dev = self._devices[row]
            reply = QMessageBox.question(
                self,
                "确认删除",
                f"确定要删除器件 {dev.part_number} 吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                ok = self._service.delete(dev.part_number)
                if ok:
                    self._refresh_table()
                else:
                    QMessageBox.warning(self, "删除失败", "无法删除该器件。")

    # ==========================================================
    # 符号保存回调
    # ==========================================================

    def _on_symbol_saved(self, part_number: str, symbol: object) -> None:
        """将编辑后的 SymbolDef 保存回库（已在 Dialog 中完成，这里刷新列表）。"""
        self._refresh_table()

    # ==========================================================
    # 公开 API
    # ==========================================================

    def refresh(self) -> None:
        """外部调用刷新。"""
        self._refresh_table()
