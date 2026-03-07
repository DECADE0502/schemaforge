"""原理图设计页面

DesignPage — 核心设计标签页，集成输入面板、SVG 预览、AI 对话、结果展示。
支持两条后端链路：经典引擎 (core/engine) 和新主链 (workflows/design_session)。
"""

from __future__ import annotations

import logging
from functools import partial
from typing import Any

from PySide6.QtCore import Qt, Slot
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QTextEdit,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from schemaforge.gui.pages.library_page import PdfImportDialog
from schemaforge.gui.widgets.chat_panel import ChatPanel
from schemaforge.gui.widgets.progress_header import ProgressHeader
from schemaforge.gui.widgets.svg_viewer import SvgZoomView
from schemaforge.gui.workers.engine_worker import (
    ClassicEngineWorker,
    DesignSessionWorker,
    RetryDesignWorker,
    SchemaForgeOrchestratedWorker,
    SchemaForgeReviseWorker,
    SchemaForgeWorker,
)
from schemaforge.library.service import LibraryService
from schemaforge.workflows.design_session import MissingModule

logger = logging.getLogger(__name__)

# ============================================================
# 快捷模板预设
# ============================================================

PRESETS: dict[str, str] = {
    "voltage_divider": "12V到3.3V的分压采样电路",
    "ldo_regulator": "5V转3.3V稳压电路",
    "led_indicator": "3.3V绿色LED电源指示灯",
    "rc_lowpass": "1kHz低通滤波器",
}


class DesignPage(QWidget):
    """原理图设计标签页。

    Layout::

        ┌──────────────────────────────────────────────────┐
        │ ProgressHeader                                    │
        ├──────────┬──────────────────┬─────────────────────┤
        │ 输入面板  │  SVG 预览         │  AI 对话            │
        ├──────────┴──────────────────┴─────────────────────┤
        │ [BOM] [SPICE] [ERC] [设计概要] [运行日志] QTabWidget│
        └──────────────────────────────────────────────────┘
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._worker: ClassicEngineWorker | DesignSessionWorker | SchemaForgeWorker | SchemaForgeReviseWorker | SchemaForgeOrchestratedWorker | None = None
        self._last_result: Any = None  # EngineResult | DesignSessionResult

        # --- 缺失器件待办状态 ---
        self._missing_panel: QWidget | None = None
        self._pending_missing: dict[int, MissingModule] = {}
        self._missing_cards: dict[int, QFrame] = {}
        self._missing_labels: dict[int, QLabel] = {}
        self._missing_buttons: dict[int, QPushButton] = {}
        self._original_input: str = ""
        self._use_mock: bool = True
        self._library_service: LibraryService | None = None

        # --- 统一工作台会话（多轮复用） ---
        self._sf_session: object | None = None  # SchemaForgeSession 实例
        self._has_design: bool = False  # 是否已有设计结果（用于判断 chat 走 revise 还是 start）

        self._init_ui()
        self._connect_signals()

    # ==========================================================
    # UI 构建
    # ==========================================================

    def _init_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # --- 进度条顶栏 ---
        self._progress_header = ProgressHeader()
        root.addWidget(self._progress_header)

        # --- 主区域 (3 列 Splitter) ---
        self._main_splitter = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(self._main_splitter, stretch=3)

        self._build_input_panel()
        self._build_preview_panel()
        self._build_chat_panel()

        # 设置 25% / 50% / 25% 比例
        self._main_splitter.setStretchFactor(0, 1)
        self._main_splitter.setStretchFactor(1, 2)
        self._main_splitter.setStretchFactor(2, 1)

        # --- 底部结果标签页 ---
        self._build_result_tabs()
        root.addWidget(self._result_tabs, stretch=1)

    # ----------------------------------------------------------
    # 左侧：输入面板
    # ----------------------------------------------------------

    def _build_input_panel(self) -> None:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 8, 4, 8)

        # 标题
        title = QLabel("电路需求输入")
        title.setStyleSheet("font-weight: bold; font-size: 14px;")
        layout.addWidget(title)

        # 需求文本框
        self._input_edit = QTextEdit()
        self._input_edit.setPlaceholderText(
            "请输入电路需求描述，例如：\n5V转3.3V稳压电路，带绿色LED电源指示灯"
        )
        layout.addWidget(self._input_edit, stretch=1)

        # 快捷模板
        tmpl_label = QLabel("快捷模板")
        layout.addWidget(tmpl_label)
        self._template_combo = QComboBox()
        self._template_combo.addItem("— 选择预设模板 —", "")
        for key, desc in PRESETS.items():
            self._template_combo.addItem(desc, key)
        layout.addWidget(self._template_combo)

        # 运行模式
        mode_label = QLabel("运行模式")
        layout.addWidget(mode_label)
        self._mode_combo = QComboBox()
        self._mode_combo.addItems(["离线Mock", "在线LLM"])
        layout.addWidget(self._mode_combo)

        # 后端链路
        chain_label = QLabel("后端链路")
        layout.addWidget(chain_label)
        self._chain_combo = QComboBox()
        self._chain_combo.addItems([
            "经典链（模板驱动）",
            "新主链（库驱动+IR+审查）",
            "统一工作台（推荐）",
        ])
        self._chain_combo.setCurrentIndex(2)  # 默认选统一工作台
        layout.addWidget(self._chain_combo)

        # 按钮行
        btn_row = QHBoxLayout()
        self._btn_generate = QPushButton("⚡ 生成")
        self._btn_generate.setMinimumHeight(36)
        self._btn_demo = QPushButton("🎯 Demo")
        self._btn_demo.setMinimumHeight(36)
        btn_row.addWidget(self._btn_generate)
        btn_row.addWidget(self._btn_demo)
        layout.addLayout(btn_row)

        # 状态标签
        self._status_label = QLabel("就绪")
        self._status_label.setWordWrap(True)
        layout.addWidget(self._status_label)

        self._main_splitter.addWidget(panel)

    # ----------------------------------------------------------
    # 中部：SVG 预览
    # ----------------------------------------------------------

    def _build_preview_panel(self) -> None:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(4, 8, 4, 8)

        # 标题
        self._preview_title = QLabel("原理图预览")
        self._preview_title.setStyleSheet("font-weight: bold; font-size: 14px;")
        layout.addWidget(self._preview_title)

        # 缩放工具栏
        self._preview_toolbar = QToolBar()
        self._preview_toolbar.setMovable(False)

        self._btn_fit = QPushButton("适应")
        self._btn_zoom_in = QPushButton("+")
        self._btn_zoom_out = QPushButton("−")
        self._zoom_label = QLabel("100%")
        self._zoom_label.setMinimumWidth(50)
        self._zoom_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._preview_toolbar.addWidget(self._btn_fit)
        self._preview_toolbar.addWidget(self._btn_zoom_out)
        self._preview_toolbar.addWidget(self._zoom_label)
        self._preview_toolbar.addWidget(self._btn_zoom_in)
        layout.addWidget(self._preview_toolbar)

        # Stacked widget: index 0 = SVG viewer, index 1 = missing panel
        self._preview_stack = QStackedWidget()

        # SVG 查看器 (index 0)
        self._svg_viewer = SvgZoomView()
        self._preview_stack.addWidget(self._svg_viewer)

        # 缺失器件面板占位 (index 1) — 延迟构建
        self._missing_placeholder = QWidget()
        self._preview_stack.addWidget(self._missing_placeholder)

        layout.addWidget(self._preview_stack, stretch=1)

        self._main_splitter.addWidget(panel)

    # ----------------------------------------------------------
    # 右侧：AI 对话面板
    # ----------------------------------------------------------

    def _build_chat_panel(self) -> None:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(4, 8, 8, 8)

        title = QLabel("AI 对话")
        title.setStyleSheet("font-weight: bold; font-size: 14px;")
        layout.addWidget(title)

        self._chat_panel = ChatPanel()
        layout.addWidget(self._chat_panel, stretch=1)

        self._main_splitter.addWidget(panel)

    # ----------------------------------------------------------
    # 底部：结果标签页
    # ----------------------------------------------------------

    def _build_result_tabs(self) -> None:
        self._result_tabs = QTabWidget()

        self._tab_bom = QTextEdit()
        self._tab_bom.setReadOnly(True)
        self._tab_bom.setPlaceholderText("生成后显示 BOM 清单…")
        self._result_tabs.addTab(self._tab_bom, "BOM清单")

        self._tab_spice = QTextEdit()
        self._tab_spice.setReadOnly(True)
        self._tab_spice.setPlaceholderText("生成后显示 SPICE 网表…")
        self._result_tabs.addTab(self._tab_spice, "SPICE网表")

        self._tab_erc = QTextEdit()
        self._tab_erc.setReadOnly(True)
        self._tab_erc.setPlaceholderText("生成后显示 ERC 检查结果…")
        self._result_tabs.addTab(self._tab_erc, "ERC检查")

        self._tab_summary = QTextEdit()
        self._tab_summary.setReadOnly(True)
        self._tab_summary.setPlaceholderText("生成后显示设计概要…")
        self._result_tabs.addTab(self._tab_summary, "设计概要")

        self._tab_log = QTextEdit()
        self._tab_log.setReadOnly(True)
        self._tab_log.setPlaceholderText("运行日志…")
        self._result_tabs.addTab(self._tab_log, "运行日志")

    # ==========================================================
    # 信号连接
    # ==========================================================

    def _connect_signals(self) -> None:
        self._btn_generate.clicked.connect(self._on_generate)
        self._btn_demo.clicked.connect(self._on_demo)
        self._btn_fit.clicked.connect(self._svg_viewer.fit_to_view)
        self._btn_zoom_in.clicked.connect(self._svg_viewer.zoom_in)
        self._btn_zoom_out.clicked.connect(self._svg_viewer.zoom_out)
        self._svg_viewer.zoom_changed.connect(self._on_zoom_changed)
        self._template_combo.currentIndexChanged.connect(self._on_template_selected)
        self._chat_panel.message_sent.connect(self._on_chat_send)

    # ==========================================================
    # 事件处理
    # ==========================================================

    @Slot(float)
    def _on_zoom_changed(self, factor: float) -> None:
        self._zoom_label.setText(f"{factor * 100:.0f}%")

    @Slot(int)
    def _on_template_selected(self, index: int) -> None:
        key = self._template_combo.itemData(index)
        if key and key in PRESETS:
            self._input_edit.setPlainText(PRESETS[key])

    @Slot()
    def _on_demo(self) -> None:
        """Demo 按钮：填充预设需求并触发生成。"""
        self._input_edit.setPlainText("5V转3.3V稳压电路，带绿色LED电源指示灯")
        self._on_generate()

    @Slot(str)
    def _on_chat_send(self, message: str) -> None:
        """对话面板发送消息时触发修改或新建设计。

        如果已有设计会话（_sf_session 且 _has_design），则走多轮修改路径；
        否则将消息填入输入框，走全新设计路径。
        """
        text = message.strip()
        if not text:
            return

        if self._sf_session is not None and self._has_design:
            # --- 多轮修改路径 ---
            self._start_revise(text)
        else:
            # --- 新设计路径 ---
            self._input_edit.setPlainText(text)
            self._on_generate()

    def _start_revise(self, message: str) -> None:
        """启动多轮修改流程（对话面板 → SchemaForgeReviseWorker）。"""
        if self._worker is not None:
            self._status_label.setText("⚠ 正在运行中，请等待完成")
            return

        self._status_label.setText("正在修改设计…")
        self._btn_generate.setEnabled(False)
        self._progress_header.reset()
        self._tab_log.append(f"[修改] 输入: {message[:80]}…")

        worker = SchemaForgeReviseWorker(
            session=self._sf_session,
            user_input=message,
        )
        worker.progress.connect(self._on_worker_progress)
        worker.finished.connect(self._on_sf_revise_finished)
        worker.error.connect(self._on_worker_error)

        self._worker = worker
        worker.start()

    @Slot()
    def _on_generate(self) -> None:
        """启动生成流程。"""
        user_input = self._input_edit.toPlainText().strip()
        if not user_input:
            self._status_label.setText("⚠ 请输入电路需求描述")
            return

        if self._worker is not None:
            self._status_label.setText("⚠ 正在运行中，请等待完成")
            return

        # 隐藏可能残留的缺失面板
        self._hide_missing_panel()

        # 清空旧结果
        self._clear_results()
        self._status_label.setText("正在生成…")
        self._btn_generate.setEnabled(False)
        self._progress_header.reset()
        self._tab_log.append(f"[启动] 输入: {user_input[:80]}…")

        use_mock = self._mode_combo.currentIndex() == 0
        chain_index = self._chain_combo.currentIndex()

        # 保存输入信息，供缺失器件重试时复用
        self._original_input = user_input
        self._use_mock = use_mock

        # 切换链路时清空会话状态
        if chain_index != 2:
            self._sf_session = None
            self._has_design = False

        if chain_index == 2:
            # 统一工作台（推荐）— 复用已有会话
            worker = SchemaForgeWorker(
                user_input=user_input,
                use_mock=use_mock,
                session=self._sf_session,
            )
            worker.session_ready.connect(self._on_session_ready)
            worker.progress.connect(self._on_worker_progress)
            worker.finished.connect(self._on_sf_worker_finished)
            worker.error.connect(self._on_worker_error)
        elif chain_index == 1:
            worker = DesignSessionWorker(
                user_input=user_input,
                use_mock=use_mock,
            )
            worker.progress.connect(self._on_worker_progress)
            worker.finished.connect(self._on_worker_finished)
            worker.error.connect(self._on_worker_error)
        else:
            worker = ClassicEngineWorker(
                user_input=user_input,
                use_mock=use_mock,
            )
            worker.progress.connect(self._on_worker_progress)
            worker.finished.connect(self._on_worker_finished)
            worker.error.connect(self._on_worker_error)

        self._worker = worker
        worker.start()

    @Slot(str, int)
    def _on_worker_progress(self, message: str, percentage: int) -> None:
        """工作线程进度回调。"""
        self._progress_header.update_progress(message, percentage)
        self._status_label.setText(message)
        self._tab_log.append(f"[{percentage}%] {message}")

    @Slot(object)
    def _on_worker_finished(self, result: Any) -> None:
        """工作线程完成回调。"""
        self._worker = None
        self._btn_generate.setEnabled(True)
        self._last_result = result
        self._progress_header.update_progress("完成", 100)

        # --- 缺失器件检测（优先于 success/error 判断）---
        has_missing = getattr(result, "has_missing", False)
        if has_missing:
            missing_modules: list[MissingModule] = getattr(
                result, "missing_modules", []
            )
            count = len(missing_modules)
            self._status_label.setText(f"⚠ 发现 {count} 个器件缺失，请补录")
            self._progress_header.update_progress("等待补录缺失器件", 50)
            self._tab_log.append(f"[缺失] 发现 {count} 个缺失器件，等待用户补录")
            self._chat_panel.add_message(
                "assistant",
                f"发现 {count} 个器件在库中缺失，请在中间面板逐一补录后继续设计。",
            )
            self._show_missing_panel(missing_modules)
            return

        # 判断结果类型并填充
        success = getattr(result, "success", False)
        svg_paths: list[str] = getattr(result, "svg_paths", [])
        bom_text: str = getattr(result, "bom_text", "")
        spice_text: str = getattr(result, "spice_text", "")
        error: str = getattr(result, "error", "")

        if success:
            self._status_label.setText("✅ 生成完成")

            # 加载第一个 SVG
            if svg_paths:
                loaded = self._svg_viewer.load_file(svg_paths[0])
                if loaded:
                    self._svg_viewer.fit_to_view()
                    self._tab_log.append(f"[SVG] 已加载: {svg_paths[0]}")
                else:
                    self._tab_log.append(f"[SVG] 加载失败: {svg_paths[0]}")

            # 填充结果标签页
            self._tab_bom.setPlainText(bom_text or "（无 BOM 数据）")
            self._tab_spice.setPlainText(spice_text or "（无 SPICE 数据）")

            # ERC 检查
            erc_errors = getattr(result, "erc_errors", [])
            if erc_errors:
                erc_lines = [
                    f"[{e.severity}] {e.message}" if hasattr(e, "severity") else str(e)
                    for e in erc_errors
                ]
                self._tab_erc.setPlainText("\n".join(erc_lines))
            else:
                self._tab_erc.setPlainText("✅ ERC 检查通过，无错误")

            # 设计概要
            summary_parts: list[str] = []
            design_name = getattr(result, "design_name", "")
            description = getattr(result, "description", "")
            notes = getattr(result, "notes", "")
            if design_name:
                summary_parts.append(f"设计名称: {design_name}")
            if description:
                summary_parts.append(f"描述: {description}")
            if notes:
                summary_parts.append(f"备注: {notes}")
            summary_parts.append(f"SVG 数量: {len(svg_paths)}")

            # DesignSessionResult 额外信息
            modules = getattr(result, "modules", [])
            if modules:
                summary_parts.append(f"模块数量: {len(modules)}")
                for i, m in enumerate(modules):
                    req = getattr(m, "requirement", None)
                    role = getattr(req, "role", "?") if req else "?"
                    dev = getattr(m, "device", None)
                    pn = dev.part_number if dev else "无"
                    summary_parts.append(f"  模块{i + 1}: {role} → {pn}")

            design_review = getattr(result, "design_review", None)
            if design_review is not None:
                passed = getattr(design_review, "overall_passed", None)
                summary_parts.append(
                    f"设计审查: {'✅ 通过' if passed else '❌ 未通过'}"
                )

            self._tab_summary.setPlainText("\n".join(summary_parts) or "（无概要）")

            # Chat 反馈
            self._chat_panel.add_message(
                "assistant",
                f"生成完成！共产出 {len(svg_paths)} 张原理图。",
            )
            self._tab_log.append("[完成] 生成成功")
        else:
            self._status_label.setText(f"❌ 生成失败: {error}")
            self._tab_log.append(f"[失败] {error}")
            self._chat_panel.add_message("assistant", f"生成失败: {error}")

    @Slot(object)
    def _on_sf_worker_finished(self, result: Any) -> None:
        """统一工作台 SchemaForgeWorker 完成回调。

        处理 SchemaForgeTurnResult: status = generated / needs_asset / error
        """
        self._worker = None
        self._btn_generate.setEnabled(True)
        self._last_result = result
        self._progress_header.update_progress("完成", 100)

        status = getattr(result, "status", "error")
        message = getattr(result, "message", "")
        bundle = getattr(result, "bundle", None)

        if status == "generated" and bundle is not None:
            self._status_label.setText("✅ 生成完成")
            self._display_bundle(bundle)
            self._has_design = True
            self._chat_panel.add_message("assistant", f"✅ {message}")
            self._chat_panel.add_message(
                "system",
                "💬 你可以在对话框中输入修改指令，例如「把输出电压改成3.3V」",
            )
            self._tab_log.append(f"[完成] {message}")

        elif status == "needs_asset":
            # 缺失器件 → 显示导入提示
            missing_pn = getattr(result, "missing_part_number", "")
            self._status_label.setText(f"⚠ 器件缺失: {missing_pn or '未知型号'}")
            self._progress_header.update_progress("等待补录器件", 50)
            self._tab_log.append(f"[缺失] 需要器件: {missing_pn}")
            self._chat_panel.add_message(
                "assistant",
                f"⚠ {message}\n\n请点击「去补录」按钮上传 datasheet 导入器件。",
            )

            # 构造兼容 MissingModule 的结构给现有面板使用
            from schemaforge.workflows.design_session import MissingModule as MM

            fake_missing = [
                MM(
                    role="main",
                    part_number=missing_pn,
                    category=getattr(result, "request", None)
                    and getattr(result.request, "category", "") or "",
                    description=f"需要导入 {missing_pn} 的 datasheet",
                    search_error="器件库中不存在",
                ),
            ]
            self._show_missing_panel(fake_missing)

        else:
            # error
            self._status_label.setText(f"❌ {message}")
            self._tab_log.append(f"[失败] {message}")
            self._chat_panel.add_message("assistant", f"❌ {message}")

    @Slot(str)
    def _on_worker_error(self, error_message: str) -> None:
        """工作线程异常回调。"""
        self._worker = None
        self._btn_generate.setEnabled(True)
        self._progress_header.update_progress("错误", 0)
        self._status_label.setText(f"❌ 异常: {error_message}")
        self._tab_log.append(f"[异常] {error_message}")
        self._chat_panel.add_message("assistant", f"运行异常: {error_message}")
        logger.exception("Worker error: %s", error_message)

    @Slot(object)
    def _on_session_ready(self, session: Any) -> None:
        """SchemaForgeWorker 创建会话后保存引用，供后续多轮修改复用。"""
        self._sf_session = session
        self._tab_log.append("[会话] 已创建统一工作台会话（支持多轮修改）")

    @Slot(object)
    def _on_sf_revise_finished(self, result: Any) -> None:
        """多轮修改 SchemaForgeReviseWorker 完成回调。

        处理逻辑与 _on_sf_worker_finished 相同，但不重置会话。
        """
        self._worker = None
        self._btn_generate.setEnabled(True)
        self._last_result = result
        self._progress_header.update_progress("修改完成", 100)

        status = getattr(result, "status", "error")
        message = getattr(result, "message", "")
        bundle = getattr(result, "bundle", None)

        if status == "generated" and bundle is not None:
            self._status_label.setText("✅ 修改完成")
            self._display_bundle(bundle)
            self._chat_panel.add_message("assistant", f"✅ {message}")
            self._tab_log.append(f"[修改完成] {message}")

        elif status == "needs_asset":
            missing_pn = getattr(result, "missing_part_number", "")
            self._status_label.setText(f"⚠ 器件缺失: {missing_pn or '未知型号'}")
            self._progress_header.update_progress("等待补录器件", 50)
            self._tab_log.append(f"[缺失] 需要器件: {missing_pn}")
            self._chat_panel.add_message(
                "assistant",
                f"⚠ {message}\n\n请点击「去补录」按钮上传 datasheet 导入器件。",
            )
            from schemaforge.workflows.design_session import MissingModule as MM

            fake_missing = [
                MM(
                    role="main",
                    part_number=missing_pn,
                    category=getattr(result, "request", None)
                    and getattr(result.request, "category", "")
                    or "",
                    description=f"需要导入 {missing_pn} 的 datasheet",
                    search_error="器件库中不存在",
                ),
            ]
            self._show_missing_panel(fake_missing)

        else:
            self._status_label.setText(f"❌ 修改失败: {message}")
            self._tab_log.append(f"[修改失败] {message}")
            self._chat_panel.add_message("assistant", f"❌ {message}")

    @Slot(object)
    def _on_orchestrated_finished(self, agent_step: Any) -> None:
        """AI 编排 SchemaForgeOrchestratedWorker 完成回调。

        处理 AgentStep: ASK_USER / PRESENT_DRAFT / FINALIZE / FAIL
        """
        self._worker = None
        self._btn_generate.setEnabled(True)
        self._progress_header.update_progress("AI 编排完成", 100)

        action = getattr(agent_step, "action", None)
        message = getattr(agent_step, "message", "")
        action_name = action.value if action is not None else "unknown"

        if action_name == "ask_user":
            # AI 有问题需要用户回答
            questions = getattr(agent_step, "questions", [])
            q_texts = [getattr(q, "text", str(q)) for q in questions]
            q_display = "\n".join(f"  • {t}" for t in q_texts) if q_texts else ""
            self._status_label.setText("🤔 AI 需要更多信息")
            self._chat_panel.add_message(
                "assistant",
                f"{message}\n\n{q_display}" if q_display else message,
            )
            self._tab_log.append(f"[AI 提问] {message}")

        elif action_name in ("present_draft", "finalize"):
            # AI 完成设计
            self._status_label.setText("✅ AI 编排完成")
            # 尝试从会话中获取最新 bundle
            if self._sf_session is not None:
                bundle = getattr(self._sf_session, "bundle", None)
                if bundle is not None:
                    self._display_bundle(bundle)
                    self._has_design = True
            self._chat_panel.add_message("assistant", f"✅ {message}")
            self._chat_panel.add_message(
                "system",
                "💬 你可以继续在对话框中输入修改指令",
            )
            self._tab_log.append(f"[AI 完成] {message}")

        elif action_name == "fail":
            self._status_label.setText(f"❌ AI 编排失败: {message}")
            self._chat_panel.add_message("assistant", f"❌ {message}")
            self._tab_log.append(f"[AI 失败] {message}")

        else:
            # call_tools 等中间态（理论上不应该到 GUI）
            self._status_label.setText(f"AI: {action_name}")
            self._chat_panel.add_message("assistant", message or f"AI 动作: {action_name}")
            self._tab_log.append(f"[AI {action_name}] {message}")

    def _display_bundle(self, bundle: Any) -> None:
        """提取 DesignBundle 的内容并展示到各标签页。"""
        # SVG
        svg_path = getattr(bundle, "svg_path", "")
        if svg_path:
            loaded = self._svg_viewer.load_file(svg_path)
            if loaded:
                self._svg_viewer.fit_to_view()
                self._tab_log.append(f"[SVG] 已加载: {svg_path}")
            else:
                self._tab_log.append(f"[SVG] 加载失败: {svg_path}")

        # BOM / SPICE
        bom_text = getattr(bundle, "bom_text", "")
        spice_text = getattr(bundle, "spice_text", "")
        self._tab_bom.setPlainText(bom_text or "（无 BOM 数据）")
        self._tab_spice.setPlainText(spice_text or "（无 SPICE 数据）")
        self._tab_erc.setPlainText("—")

        # 设计概要
        device = getattr(bundle, "device", None)
        params = getattr(bundle, "parameters", {})
        rationale = getattr(bundle, "rationale", [])
        summary_parts: list[str] = []
        if device:
            summary_parts.append(f"器件: {device.part_number}")
            summary_parts.append(f"分类: {device.category or '—'}")
        summary_parts.append(f"参数数量: {len(params)}")
        for key, val in params.items():
            summary_parts.append(f"  {key}: {val}")
        if rationale:
            summary_parts.append("")
            summary_parts.append("设计依据:")
            for r in rationale:
                summary_parts.append(f"  • {r}")
        self._tab_summary.setPlainText("\n".join(summary_parts) or "（无概要）")

    # ==========================================================
    # 缺失器件待办面板
    # ==========================================================

    def _show_missing_panel(self, modules: list[MissingModule]) -> None:
        """构建并显示缺失器件待办面板，替换 SVG 预览区。"""
        self._pending_missing = {i: m for i, m in enumerate(modules)}
        self._missing_cards.clear()
        self._missing_labels.clear()
        self._missing_buttons.clear()

        # 替换 stacked widget 中 index 1 的占位
        old = self._preview_stack.widget(1)
        if old is not None:
            self._preview_stack.removeWidget(old)
            old.deleteLater()

        container = QWidget()
        outer = QVBoxLayout(container)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # --- 顶部警告横幅 ---
        banner = QLabel(
            f"⚠ 以下 {len(modules)} 个器件在库中缺失，请逐一补录后自动继续设计"
        )
        banner.setWordWrap(True)
        banner.setStyleSheet(
            "background: #3a2a00; color: #ffb74d; padding: 10px 14px;"
            "font-size: 13px; font-weight: bold; border-bottom: 1px solid #555;"
        )
        outer.addWidget(banner)

        # --- 可滚动卡片区 ---
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(
            "QScrollArea { border: none; background: transparent; }"
        )

        cards_container = QWidget()
        cards_layout = QVBoxLayout(cards_container)
        cards_layout.setContentsMargins(12, 12, 12, 8)
        cards_layout.setSpacing(10)

        for idx, missing in enumerate(modules):
            card = self._build_missing_card(idx, missing)
            cards_layout.addWidget(card)

        cards_layout.addStretch()
        scroll.setWidget(cards_container)
        outer.addWidget(scroll, stretch=1)

        # --- 底部按钮行 ---
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(12, 8, 12, 12)
        self._btn_retry = QPushButton("🔄 全部补录完成，继续设计")
        self._btn_retry.setMinimumHeight(38)
        self._btn_retry.setEnabled(False)
        self._btn_retry.setStyleSheet(
            "QPushButton { background: #1565c0; color: #fff; border: none;"
            "border-radius: 6px; font-size: 13px; font-weight: bold; padding: 8px 20px; }"
            "QPushButton:disabled { background: #333; color: #777; }"
            "QPushButton:hover:!disabled { background: #1976d2; }"
        )
        self._btn_retry.clicked.connect(self._on_retry_design)
        btn_row.addStretch()
        btn_row.addWidget(self._btn_retry)
        btn_row.addStretch()
        outer.addLayout(btn_row)

        self._missing_panel = container
        self._preview_stack.addWidget(container)

        # 切换到缺失面板
        self._preview_title.setText("⚠ 缺失器件待办")
        self._preview_toolbar.setVisible(False)
        self._preview_stack.setCurrentWidget(container)

    def _build_missing_card(self, idx: int, missing: MissingModule) -> QFrame:
        """构建单个缺失器件卡片。"""
        card = QFrame()
        card.setStyleSheet(
            "QFrame { border: 1px solid #555; border-radius: 6px;"
            "background: #2d2d2d; padding: 10px; }"
        )

        layout = QVBoxLayout(card)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(4)

        # 角色描述（标题行）
        role_text = missing.description or missing.role or f"器件 {idx + 1}"
        title = QLabel(role_text)
        title.setStyleSheet("font-weight: bold; font-size: 13px; color: #e0e0e0;")
        title.setWordWrap(True)
        layout.addWidget(title)

        # 料号（橙色高亮）
        if missing.part_number:
            pn_label = QLabel(f"料号: {missing.part_number}")
            pn_label.setStyleSheet("color: #ff9800; font-size: 12px;")
            layout.addWidget(pn_label)

        # 分类
        if missing.category:
            cat_label = QLabel(f"分类: {missing.category}")
            cat_label.setStyleSheet("color: #9e9e9e; font-size: 12px;")
            layout.addWidget(cat_label)

        # 检索失败原因
        if missing.search_error:
            err_label = QLabel(f"原因: {missing.search_error}")
            err_label.setStyleSheet("color: #ef5350; font-size: 11px;")
            err_label.setWordWrap(True)
            layout.addWidget(err_label)

        # 状态标签 + 按钮行
        bottom_row = QHBoxLayout()

        status_label = QLabel("")
        status_label.setStyleSheet("color: #4caf50; font-size: 12px;")
        status_label.setVisible(False)
        bottom_row.addWidget(status_label)

        bottom_row.addStretch()

        btn = QPushButton("去补录")
        btn.setStyleSheet(
            "QPushButton { background: #1565c0; color: #fff; border: none;"
            "border-radius: 4px; padding: 5px 16px; font-size: 12px; }"
            "QPushButton:hover { background: #1976d2; }"
        )
        btn.clicked.connect(partial(self._on_resolve_device, idx))
        bottom_row.addWidget(btn)

        layout.addLayout(bottom_row)

        # 存储引用
        self._missing_cards[idx] = card
        self._missing_labels[idx] = status_label
        self._missing_buttons[idx] = btn

        return card

    def _on_resolve_device(self, index: int) -> None:
        """打开 PdfImportDialog 补录指定缺失器件。"""
        missing = self._pending_missing.get(index)
        if missing is None:
            return

        # 延迟初始化 LibraryService
        if self._library_service is None:
            self._library_service = LibraryService(store_dir="schemaforge/store")

        dlg = PdfImportDialog(self._library_service, parent=self)
        dlg._result_status.setText(
            f"待补录: {missing.part_number} — {missing.description}"
            if missing.part_number
            else f"待补录: {missing.description or missing.role}"
        )
        dlg.device_imported.connect(partial(self._on_device_resolved, index))
        dlg.exec()

    def _on_device_resolved(self, index: int) -> None:
        """器件补录完成回调 — 更新卡片状态。"""
        if index not in self._pending_missing:
            return

        # 从待办中移除
        del self._pending_missing[index]

        # 更新卡片样式 → 绿色已完成
        card = self._missing_cards.get(index)
        if card is not None:
            card.setStyleSheet(
                "QFrame { border: 1px solid #4caf50; border-radius: 6px;"
                "background: #1a3a1a; padding: 10px; }"
            )

        # 隐藏按钮，显示已补录标签
        btn = self._missing_buttons.get(index)
        if btn is not None:
            btn.setVisible(False)

        label = self._missing_labels.get(index)
        if label is not None:
            label.setText("✅ 已补录")
            label.setVisible(True)

        self._tab_log.append(f"[补录] 器件 {index + 1} 已补录完成")

        # 检查是否全部完成
        if not self._pending_missing:
            self._btn_retry.setEnabled(True)
            self._chat_panel.add_message(
                "assistant",
                "所有缺失器件已补录完成！点击「继续设计」按钮重新运行设计。",
            )
            # 自动触发重试
            self._on_retry_design()

    def _on_retry_design(self) -> None:
        """隐藏缺失面板，恢复 SVG 预览，启动 RetryDesignWorker。"""
        if self._worker is not None:
            self._status_label.setText("⚠ 正在运行中，请等待完成")
            return

        # 恢复 SVG 预览视图
        self._preview_title.setText("原理图预览")
        self._preview_toolbar.setVisible(True)
        self._preview_stack.setCurrentIndex(0)

        # 清空旧结果
        self._clear_results()
        self._status_label.setText("正在重新设计…")
        self._btn_generate.setEnabled(False)
        self._progress_header.reset()
        self._tab_log.append(f"[重试] 补录完成，重新运行: {self._original_input[:80]}…")

        worker = RetryDesignWorker(
            user_input=self._original_input,
            use_mock=self._use_mock,
        )
        worker.progress.connect(self._on_worker_progress)
        worker.finished.connect(self._on_worker_finished)
        worker.error.connect(self._on_worker_error)

        self._worker = worker
        worker.start()

    def _hide_missing_panel(self) -> None:
        """恢复 SVG 预览视图（不启动重试）。"""
        self._preview_title.setText("原理图预览")
        self._preview_toolbar.setVisible(True)
        self._preview_stack.setCurrentIndex(0)
        self._pending_missing.clear()
        self._missing_cards.clear()
        self._missing_labels.clear()
        self._missing_buttons.clear()

    # ==========================================================
    # 辅助方法
    # ==========================================================

    def _clear_results(self) -> None:
        """清空所有结果区域。"""
        self._tab_bom.clear()
        self._tab_spice.clear()
        self._tab_erc.clear()
        self._tab_summary.clear()
        self._tab_log.clear()

    # ----------------------------------------------------------
    # 公开 API（供 MainWindow 菜单调用）
    # ----------------------------------------------------------

    def get_current_svg_path(self) -> str | None:
        """返回当前结果中的第一个 SVG 路径。"""
        if self._last_result is None:
            return None
        paths: list[str] = getattr(self._last_result, "svg_paths", [])
        return paths[0] if paths else None

    def get_bom_text(self) -> str:
        """返回当前 BOM 文本。"""
        return self._tab_bom.toPlainText()

    def get_spice_text(self) -> str:
        """返回当前 SPICE 文本。"""
        return self._tab_spice.toPlainText()
