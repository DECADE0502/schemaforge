"""PDF/图片导入向导

分步导入流程:
1. 文件选择 (PDF 或图片)
2. 解析进度展示
3. AI 分析结果预览
4. 追问卡片 (用户补全缺失信息)
5. 最终 DeviceDraft 预览
6. 确认入库

所有 UI 文案为中文。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal, QThread
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import (
    QFileDialog,
    QFormLayout,
    QGraphicsScene,
    QGraphicsView,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from schemaforge.ingest.datasheet_extractor import (
    ExtractionResult,
    apply_user_answers,
    extract_from_image,
    extract_from_pdf,
)
from schemaforge.library.validator import DeviceDraft


# ============================================================
# 提取工作线程
# ============================================================


class ExtractionWorker(QThread):
    """后台执行提取流程"""

    finished = Signal(object)  # ExtractionResult
    error = Signal(str)
    progress = Signal(str, int)  # (message, percentage)

    def __init__(
        self,
        filepath: str,
        hint: str = "",
        use_mock: bool = True,
        extra_images: list[str] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.filepath = filepath
        self.hint = hint
        self.use_mock = use_mock
        self.extra_images: list[str] = extra_images or []

    def run(self) -> None:
        from schemaforge.common.progress import ProgressTracker

        tracker = ProgressTracker(
            on_event=self._on_event,
            source="import_wizard",
        )

        try:
            ext = Path(self.filepath).suffix.lower()
            if ext == ".pdf":
                result = extract_from_pdf(
                    self.filepath,
                    hint=self.hint,
                    use_mock=self.use_mock,
                    tracker=tracker,
                    extra_images=self.extra_images,
                )
            elif ext in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"):
                result = extract_from_image(
                    self.filepath,
                    hint=self.hint,
                    use_mock=self.use_mock,
                    tracker=tracker,
                )
            else:
                result = ExtractionResult(
                    error_message=f"不支持的文件格式: {ext}",
                )

            self.finished.emit(result)
        except Exception as exc:
            self.error.emit(f"提取异常: {exc}")

    def _on_event(self, event: object) -> None:
        """ProgressTracker 事件回调"""
        from schemaforge.common.events import ProgressEvent, LogEvent

        if isinstance(event, ProgressEvent):
            self.progress.emit(event.message, event.percentage)
        elif isinstance(event, LogEvent):
            self.progress.emit(event.message, -1)


# ============================================================
# 导入向导面板
# ============================================================


class ImportWizard(QWidget):
    """PDF/图片导入向导

    信号:
        draft_ready(DeviceDraft): 提取并补全后的草稿，可入库
    """

    draft_ready = Signal(object)  # DeviceDraft

    def __init__(self, use_mock: bool = True, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.use_mock = use_mock
        self._worker: ExtractionWorker | None = None
        self._current_result: ExtractionResult | None = None
        self._current_draft: DeviceDraft | None = None
        self._answer_widgets: dict[str, QLineEdit] = {}
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        # 标题
        title = QLabel("📄 PDF / 图片导入")
        title.setProperty("class", "title")
        layout.addWidget(title)

        desc = QLabel(
            "上传 PDF datasheet 或引脚图截图，AI 自动提取器件信息。\n"
            "不确定的信息会提示你手动补全。"
        )
        desc.setWordWrap(True)
        desc.setProperty("class", "muted")
        layout.addWidget(desc)

        # ── 文件选择区 ──
        file_group = QGroupBox("文件选择")
        file_layout = QHBoxLayout(file_group)

        self.file_label = QLabel("未选择文件")
        self.file_label.setProperty("class", "muted")
        file_layout.addWidget(self.file_label, 1)

        browse_btn = QPushButton("选择文件")
        browse_btn.clicked.connect(self._on_browse)
        file_layout.addWidget(browse_btn)

        layout.addWidget(file_group)

        # ── 引脚图片区（可选，多选） ──
        img_group = QGroupBox("引脚图 / 封装图（可选）")
        img_layout = QVBoxLayout(img_group)

        img_desc = QLabel("上传引脚图或封装图截图，AI 会结合 PDF 文本与图片一起分析。")
        img_desc.setWordWrap(True)
        img_desc.setProperty("class", "muted")
        img_layout.addWidget(img_desc)

        self._image_list = QListWidget()
        img_layout.addWidget(self._image_list)

        img_btn_row = QHBoxLayout()
        add_img_btn = QPushButton("添加引脚图")
        add_img_btn.clicked.connect(self._on_add_images)
        img_btn_row.addWidget(add_img_btn)

        remove_img_btn = QPushButton("移除选中")
        remove_img_btn.setProperty("class", "danger")
        remove_img_btn.clicked.connect(self._on_remove_image)
        img_btn_row.addWidget(remove_img_btn)
        img_btn_row.addStretch()
        img_layout.addLayout(img_btn_row)

        layout.addWidget(img_group)

        # 提示输入
        hint_row = QHBoxLayout()
        hint_row.addWidget(QLabel("器件型号提示 (可选):"))
        self.hint_edit = QLineEdit()
        self.hint_edit.setPlaceholderText("如果你知道器件型号，输入可提高识别准确率")
        hint_row.addWidget(self.hint_edit)
        layout.addLayout(hint_row)

        # 开始按钮
        self.start_btn = QPushButton("开始提取")
        self.start_btn.setProperty("class", "primary")
        self.start_btn.setMinimumHeight(40)
        self.start_btn.setEnabled(False)
        self.start_btn.clicked.connect(self._on_start)
        layout.addWidget(self.start_btn)

        # 进度区
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.hide()
        layout.addWidget(self.progress_bar)

        self.progress_label = QLabel("")
        self.progress_label.setProperty("class", "muted")
        self.progress_label.hide()
        layout.addWidget(self.progress_label)

        # ── 结果滚动区 ──
        self._result_scroll = QScrollArea()
        self._result_scroll.setWidgetResizable(True)
        self._result_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )

        self._result_container = QWidget()
        self._result_layout = QVBoxLayout(self._result_container)
        self._result_layout.setContentsMargins(0, 0, 0, 0)
        self._result_layout.addStretch()

        self._result_scroll.setWidget(self._result_container)
        layout.addWidget(self._result_scroll, 1)

        # ── 底部按钮 ──
        btn_row = QHBoxLayout()

        self.confirm_btn = QPushButton("确认入库")
        self.confirm_btn.setProperty("class", "success")
        self.confirm_btn.setMinimumHeight(40)
        self.confirm_btn.setEnabled(False)
        self.confirm_btn.clicked.connect(self._on_confirm)
        btn_row.addWidget(self.confirm_btn)

        reset_btn = QPushButton("重置")
        reset_btn.setMinimumHeight(40)
        reset_btn.clicked.connect(self._reset)
        btn_row.addWidget(reset_btn)

        layout.addLayout(btn_row)

    # ── 文件选择 ──

    def _on_browse(self) -> None:
        filepath, _ = QFileDialog.getOpenFileName(
            self,
            "选择 PDF 或图片文件",
            "",
            "支持的文件 (*.pdf *.png *.jpg *.jpeg *.webp *.gif *.bmp);;"
            "PDF 文件 (*.pdf);;"
            "图片文件 (*.png *.jpg *.jpeg *.webp *.gif *.bmp);;"
            "所有文件 (*)",
        )
        if filepath:
            self._selected_file = filepath
            name = Path(filepath).name
            size = Path(filepath).stat().st_size / 1024
            self.file_label.setText(f"{name} ({size:.0f}KB)")
            self.file_label.setProperty("class", "")
            self.file_label.style().unpolish(self.file_label)
            self.file_label.style().polish(self.file_label)
            self.start_btn.setEnabled(True)

    def _on_add_images(self) -> None:
        filepaths, _ = QFileDialog.getOpenFileNames(
            self,
            "选择引脚图 / 封装图",
            "",
            "图片文件 (*.png *.jpg *.jpeg *.webp *.gif *.bmp);;所有文件 (*)",
        )
        for fp in filepaths:
            existing = [
                self._image_list.item(i).data(Qt.ItemDataRole.UserRole)
                for i in range(self._image_list.count())
            ]
            if fp not in existing:
                item = QListWidgetItem(Path(fp).name)
                item.setData(Qt.ItemDataRole.UserRole, fp)
                self._image_list.addItem(item)

    def _on_remove_image(self) -> None:
        for item in self._image_list.selectedItems():
            row = self._image_list.row(item)
            self._image_list.takeItem(row)

    def _get_extra_image_paths(self) -> list[str]:
        return [
            self._image_list.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self._image_list.count())
        ]

    # ── 开始提取 ──

    def _on_start(self) -> None:
        if not hasattr(self, "_selected_file"):
            return

        self.start_btn.setEnabled(False)
        self.confirm_btn.setEnabled(False)
        self.progress_bar.show()
        self.progress_bar.setValue(0)
        self.progress_label.show()
        self.progress_label.setText("正在启动提取...")
        self._clear_results()

        self._worker = ExtractionWorker(
            filepath=self._selected_file,
            hint=self.hint_edit.text().strip(),
            use_mock=self.use_mock,
            extra_images=self._get_extra_image_paths(),
        )
        self._worker.finished.connect(self._on_extraction_done)
        self._worker.error.connect(self._on_extraction_error)
        self._worker.progress.connect(self._on_progress)
        self._worker.start()

    def _on_progress(self, message: str, percentage: int) -> None:
        self.progress_label.setText(message)
        if percentage >= 0:
            self.progress_bar.setValue(percentage)

    def _on_extraction_done(self, result: ExtractionResult) -> None:
        self.start_btn.setEnabled(True)
        self.progress_bar.hide()

        self._current_result = result

        if not result.success:
            self.progress_label.setText(f"❌ 提取失败: {result.error_message}")
            self.progress_label.setProperty("class", "error")
            self.progress_label.style().unpolish(self.progress_label)
            self.progress_label.style().polish(self.progress_label)
            return

        self.progress_label.setText("✅ 提取完成")
        self.progress_label.setProperty("class", "success")
        self.progress_label.style().unpolish(self.progress_label)
        self.progress_label.style().polish(self.progress_label)

        self._current_draft = result.draft
        self._show_results(result)

    def _on_extraction_error(self, msg: str) -> None:
        self.start_btn.setEnabled(True)
        self.progress_bar.hide()
        self.progress_label.setText(f"❌ {msg}")
        self.progress_label.setProperty("class", "error")
        self.progress_label.style().unpolish(self.progress_label)
        self.progress_label.style().polish(self.progress_label)

    # ── 结果展示 ──

    def _show_results(self, result: ExtractionResult) -> None:
        """展示提取结果和追问卡片"""
        self._clear_results()

        if result.draft is None:
            return

        draft = result.draft

        # 已提取信息概览
        info_group = QGroupBox("已提取信息")
        info_layout = QFormLayout(info_group)
        info_layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        fields = [
            ("料号", draft.part_number),
            ("制造商", draft.manufacturer),
            ("类别", draft.category),
            ("描述", draft.description),
            ("封装", draft.package),
            ("引脚数", str(draft.pin_count) if draft.pin_count else ""),
            ("来源", draft.source),
            ("置信度", f"{draft.confidence:.0%}"),
        ]
        for label, value in fields:
            val_label = QLabel(value or "(未识别)")
            if not value:
                val_label.setProperty("class", "error")
            info_layout.addRow(f"{label}:", val_label)

        self._result_layout.insertWidget(
            self._result_layout.count() - 1,
            info_group,
        )

        # ── Symbol 预览 ──
        if draft.pins:
            sym_preview = self._render_symbol_preview(draft)
            if sym_preview is not None:
                self._result_layout.insertWidget(
                    self._result_layout.count() - 1,
                    sym_preview,
                )

        # 追问卡片
        if result.needs_user_input and result.questions:
            q_group = QGroupBox(f"需要补全 ({len(result.questions)} 项)")
            q_group.setObjectName("questionGroup")
            q_layout = QVBoxLayout(q_group)

            self._answer_widgets.clear()

            for q in result.questions:
                q_row = QHBoxLayout()
                q_label = QLabel(q.get("text", ""))
                q_label.setWordWrap(True)
                q_row.addWidget(q_label, 2)

                q_input = QLineEdit()
                default = q.get("default", "")
                if default:
                    q_input.setText(str(default))
                q_input.setPlaceholderText("请输入...")
                q_row.addWidget(q_input, 1)

                field_path = q.get("field_path", q.get("question_id", ""))
                self._answer_widgets[field_path] = q_input

                q_layout.addLayout(q_row)

            self._result_layout.insertWidget(
                self._result_layout.count() - 1,
                q_group,
            )

        # 启用确认按钮
        self.confirm_btn.setEnabled(True)

    def _render_symbol_preview(self, draft: DeviceDraft) -> QGroupBox | None:
        from schemaforge.library.models import PinSide, SymbolDef, SymbolPin
        from schemaforge.core.models import PinType

        type_map = {
            "input": PinType.INPUT,
            "output": PinType.OUTPUT,
            "power": PinType.POWER,
            "passive": PinType.PASSIVE,
            "nc": PinType.NC,
            "bidirectional": PinType.BIDIRECTIONAL,
        }
        side_map = {
            "left": PinSide.LEFT,
            "right": PinSide.RIGHT,
            "top": PinSide.TOP,
            "bottom": PinSide.BOTTOM,
        }

        symbol_pins: list[SymbolPin] = []
        for pin in draft.pins:
            symbol_pins.append(
                SymbolPin(
                    name=pin.name or f"PIN{pin.number}",
                    pin_number=pin.number,
                    side=side_map.get(pin.side, PinSide.LEFT),
                    pin_type=type_map.get(pin.pin_type, PinType.PASSIVE),
                    description=pin.description,
                )
            )

        if not symbol_pins:
            return None

        symbol = SymbolDef(pins=symbol_pins)
        label = draft.part_number or "IC"

        try:
            from schemaforge.schematic.renderer import TopologyRenderer

            png_bytes = TopologyRenderer.render_symbol_preview(symbol, label)
        except Exception:
            return None

        pixmap = QPixmap()
        if not pixmap.loadFromData(png_bytes):
            return None

        group = QGroupBox("原理图 Symbol 预览")
        layout = QVBoxLayout(group)

        scene = QGraphicsScene()
        scene.addPixmap(pixmap)
        scene.setSceneRect(pixmap.rect().toRectF())

        view = QGraphicsView(scene)
        view.setRenderHints(
            QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform
        )
        view.setBackgroundBrush(QColor("#1a1a2e"))
        view.setMinimumHeight(200)
        view.setProperty("class", "symbol-preview")
        view.fitInView(scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

        layout.addWidget(view)
        return group

    # ── 确认入库 ──

    def _on_confirm(self) -> None:
        if self._current_draft is None:
            return

        # 收集用户回答
        answers: dict[str, str] = {}
        for field_path, widget in self._answer_widgets.items():
            val = widget.text().strip()
            if val:
                answers[field_path] = val

        # 应用回答
        if answers:
            self._current_draft = apply_user_answers(self._current_draft, answers)

        # 检查必填字段
        if not self._current_draft.part_number:
            QMessageBox.warning(self, "提示", "料号不能为空！请先填写器件型号。")
            return

        self.draft_ready.emit(self._current_draft)

    # ── 工具方法 ──

    def _clear_results(self) -> None:
        while self._result_layout.count() > 1:
            item = self._result_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self._answer_widgets.clear()

    def _reset(self) -> None:
        self._current_result = None
        self._current_draft = None
        self.file_label.setText("未选择文件")
        self.file_label.setProperty("class", "muted")
        self.file_label.style().unpolish(self.file_label)
        self.file_label.style().polish(self.file_label)
        self.hint_edit.clear()
        self._image_list.clear()
        self.start_btn.setEnabled(False)
        self.confirm_btn.setEnabled(False)
        self.progress_bar.hide()
        self.progress_label.hide()
        self._clear_results()
        if hasattr(self, "_selected_file"):
            del self._selected_file
