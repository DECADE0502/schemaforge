#!/usr/bin/env python3
"""SchemaForge — PySide6 桌面 GUI

约束驱动的AI原理图生成器，桌面图形界面版本。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# 确保项目根目录在 Python 路径中
sys.path.insert(0, str(Path(__file__).parent))

from typing import Any

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QFont, QAction, QColor
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QGraphicsScene,
    QGraphicsView,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSplitter,
    QStatusBar,
    QTabWidget,
    QTextEdit,
    QToolBar,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtSvgWidgets import QGraphicsSvgItem

from schemaforge.core.engine import EngineResult, SchemaForgeEngine
from schemaforge.core.templates import get_template, list_templates
from schemaforge.gui.widgets.chat_panel import ChatPanel
from schemaforge.gui.widgets.progress_header import ProgressHeader
from schemaforge.gui.pages.library_page import LibraryPage
from schemaforge.common.events import ProgressEvent
from schemaforge.workflows.design_session import DesignSession, DesignSessionResult


# ============================================================
# 工作线程 — 防止 LLM 调用阻塞 UI
# ============================================================


class EngineWorker(QThread):
    """在后台线程运行引擎处理"""

    finished = Signal(object)  # EngineResult
    error = Signal(str)
    progress = Signal(str, int)  # (message, percentage)

    def __init__(self, engine: SchemaForgeEngine, user_input: str) -> None:
        super().__init__()
        self.engine = engine
        self.user_input = user_input

    def run(self) -> None:
        try:
            result = self.engine.process(
                self.user_input,
                progress_callback=self.progress.emit,
            )
            self.finished.emit(result)
        except Exception as e:
            self.error.emit(str(e))


class DesignSessionWorker(QThread):
    """在后台线程运行新主链 DesignSession"""

    finished = Signal(object)  # DesignSessionResult
    error = Signal(str)
    progress = Signal(str, int)  # (message, percentage)

    def __init__(self, session: DesignSession, user_input: str) -> None:
        super().__init__()
        self.session = session
        self.user_input = user_input

    def run(self) -> None:
        try:
            result = self.session.run(self.user_input)
            self.finished.emit(result)
        except Exception as e:
            self.error.emit(str(e))


# ============================================================
# SVG 预览组件
# ============================================================


class SvgView(QGraphicsView):
    """自适应缩放的 SVG 视图

    使用 QGraphicsView + QGraphicsSvgItem 实现 SVG 自动填充可用空间。
    支持白色背景和抗锯齿。
    """

    def __init__(self, svg_path: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        from PySide6.QtGui import QPainter

        self.setRenderHints(
            QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform
        )
        self.setBackgroundBrush(QColor("#ffffff"))
        self.setFrameShape(QFrame.Shape.NoFrame)

        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)

        # 加载 SVG
        self._svg_item = QGraphicsSvgItem(svg_path)
        self._scene.addItem(self._svg_item)
        self._scene.setSceneRect(self._svg_item.boundingRect())

    def resizeEvent(self, event: Any) -> None:  # noqa: N802
        """窗口大小变化时，自动缩放 SVG 以适应视图"""
        super().resizeEvent(event)
        if self._svg_item:
            self.fitInView(self._svg_item, Qt.AspectRatioMode.KeepAspectRatio)


class SvgPreviewWidget(QWidget):
    """SVG 预览面板，支持多个 SVG 标签页，自适应缩放"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.tab_widget = QTabWidget()
        self.tab_widget.setTabsClosable(False)
        layout.addWidget(self.tab_widget)

        # 空状态提示
        self.empty_label = QLabel("原理图预览区\n\n输入电路需求后点击「生成」")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setStyleSheet("color: #888; font-size: 16px;")
        layout.addWidget(self.empty_label)

        self.tab_widget.hide()

    def clear(self) -> None:
        self.tab_widget.clear()
        self.tab_widget.hide()
        self.empty_label.show()

    def load_svgs(self, svg_paths: list[str]) -> None:
        """加载多个 SVG 文件到标签页，自动缩放填充"""
        self.tab_widget.clear()

        for path in svg_paths:
            if not os.path.exists(path):
                continue

            svg_view = SvgView(path)

            name = Path(path).stem
            if len(name) > 25:
                name = name[:22] + "..."
            self.tab_widget.addTab(svg_view, name)

        if self.tab_widget.count() > 0:
            self.empty_label.hide()
            self.tab_widget.show()
        else:
            self.empty_label.show()
            self.tab_widget.hide()


# ============================================================
# 运行日志面板
# ============================================================


class LogPanel(QWidget):
    """实时运行日志面板"""

    _STEP_NAMES = ["AI调用", "验证", "实例化", "ERC检查", "渲染", "导出"]
    _STEP_THRESHOLDS = [5, 25, 40, 55, 70, 85]

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        # 步骤指示栏
        self.step_bar = QWidget()
        step_layout = QHBoxLayout(self.step_bar)
        step_layout.setContentsMargins(0, 0, 0, 0)
        self.steps: list[QLabel] = []
        for name in self._STEP_NAMES:
            label = QLabel(f"○ {name}")
            label.setStyleSheet("color: #999; font-size: 11px;")
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            step_layout.addWidget(label)
            self.steps.append(label)
        layout.addWidget(self.step_bar)

        # 日志文本区
        self.log_text = QPlainTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setFont(QFont("Consolas", 9))
        self.log_text.setStyleSheet(
            "QPlainTextEdit { background: #1e1e2e; color: #cdd6f4; "
            "border: 1px solid #45475a; border-radius: 4px; }"
        )
        self.log_text.setMaximumBlockCount(500)
        layout.addWidget(self.log_text)

    def clear(self) -> None:
        """清空日志和步骤指示器"""
        self.log_text.clear()
        for i, label in enumerate(self.steps):
            label.setText(f"○ {self._STEP_NAMES[i]}")
            label.setStyleSheet("color: #999; font-size: 11px;")

    def append_log(self, message: str) -> None:
        """追加一条带时间戳的日志"""
        import datetime

        ts = datetime.datetime.now().strftime("%H:%M:%S")
        self.log_text.appendPlainText(f"[{ts}] {message}")
        scrollbar = self.log_text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def update_step(self, percentage: int) -> None:
        """根据进度百分比更新步骤指示器"""
        for i, threshold in enumerate(self._STEP_THRESHOLDS):
            if percentage >= threshold:
                if percentage > threshold + 10 or percentage >= 100:
                    self.steps[i].setText(f"✓ {self._STEP_NAMES[i]}")
                    self.steps[i].setStyleSheet(
                        "color: #a6e3a1; font-size: 11px; font-weight: bold;"
                    )
                else:
                    self.steps[i].setText(f"▸ {self._STEP_NAMES[i]}")
                    self.steps[i].setStyleSheet(
                        "color: #89b4fa; font-size: 11px; font-weight: bold;"
                    )
            else:
                self.steps[i].setText(f"○ {self._STEP_NAMES[i]}")
                self.steps[i].setStyleSheet("color: #999; font-size: 11px;")


# ============================================================
# 主窗口
# ============================================================


class MainWindow(QMainWindow):
    """SchemaForge 主窗口"""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("SchemaForge — 约束驱动的AI原理图生成器")
        self.resize(1280, 800)

        # 引擎（默认离线模式）
        self.engine = SchemaForgeEngine(use_mock=True)
        self.worker: EngineWorker | None = None
        self.session_worker: DesignSessionWorker | None = None
        self.last_result: EngineResult | None = None
        self.last_session_result: DesignSessionResult | None = None
        self._design_session: DesignSession | None = None

        self._setup_ui()
        self._setup_toolbar()
        self._setup_statusbar()

    # ── UI 构建 ──────────────────────────────────────────────

    def _setup_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(8, 4, 8, 4)

        # ── 顶级标签页：原理图设计 / 器件库管理 ──
        self.main_tabs = QTabWidget()
        self.main_tabs.setFont(QFont("Microsoft YaHei", 11))
        self.main_tabs.setStyleSheet(
            "QTabBar::tab { min-width: 140px; min-height: 32px; "
            "padding: 6px 16px; font-weight: bold; }"
        )

        # ── 标签1: 原理图设计 ──
        design_page = QWidget()
        design_layout = QVBoxLayout(design_page)
        design_layout.setContentsMargins(0, 4, 0, 0)

        # 顶部：进度头
        self.progress_header = ProgressHeader()
        design_layout.addWidget(self.progress_header)

        # 主分割器：上(输入+预览)  下(BOM/SPICE)
        splitter_v = QSplitter(Qt.Orientation.Vertical)

        # ── 上半部分：左输入 + 中预览 + 右对话 ──
        splitter_h = QSplitter(Qt.Orientation.Horizontal)

        # 左侧：输入面板
        left_panel = self._build_input_panel()
        splitter_h.addWidget(left_panel)

        # 中间：SVG 预览
        self.svg_preview = SvgPreviewWidget()
        splitter_h.addWidget(self.svg_preview)

        # 右侧：AI 对话面板
        self.chat_panel = ChatPanel()
        self.chat_panel.message_sent.connect(self._on_chat_message)
        splitter_h.addWidget(self.chat_panel)

        splitter_h.setStretchFactor(0, 1)  # 输入面板
        splitter_h.setStretchFactor(1, 2)  # SVG预览
        splitter_h.setStretchFactor(2, 1)  # 对话面板
        splitter_h.setSizes([300, 600, 340])

        splitter_v.addWidget(splitter_h)

        # ── 下半部分：结果标签页 ──
        self.result_tabs = QTabWidget()

        # BOM 标签
        self.bom_text = QTextEdit()
        self.bom_text.setReadOnly(True)
        self.bom_text.setFont(QFont("Consolas", 10))
        self.result_tabs.addTab(self.bom_text, "BOM 清单")

        # SPICE 标签
        self.spice_text = QTextEdit()
        self.spice_text.setReadOnly(True)
        self.spice_text.setFont(QFont("Consolas", 10))
        self.result_tabs.addTab(self.spice_text, "SPICE 网表")

        # ERC 标签
        self.erc_text = QTextEdit()
        self.erc_text.setReadOnly(True)
        self.erc_text.setFont(QFont("Consolas", 10))
        self.result_tabs.addTab(self.erc_text, "ERC 检查")

        # 设计概要标签
        self.summary_text = QTextEdit()
        self.summary_text.setReadOnly(True)
        self.summary_text.setFont(QFont("Microsoft YaHei", 10))
        self.result_tabs.addTab(self.summary_text, "设计概要")

        # 运行日志标签
        self.log_panel = LogPanel()
        self.result_tabs.addTab(self.log_panel, "运行日志")

        splitter_v.addWidget(self.result_tabs)
        splitter_v.setStretchFactor(0, 3)
        splitter_v.setStretchFactor(1, 1)
        splitter_v.setSizes([560, 220])

        design_layout.addWidget(splitter_v)

        self.main_tabs.addTab(design_page, "⚡ 原理图设计")

        # ── 标签2: 器件库管理 ──
        self.library_page = LibraryPage()
        self.main_tabs.addTab(self.library_page, "📦 器件库管理")

        main_layout.addWidget(self.main_tabs)

    def _build_input_panel(self) -> QWidget:
        """构建左侧输入面板"""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(4, 4, 4, 4)

        # 标题
        title = QLabel("电路需求输入")
        title.setFont(QFont("Microsoft YaHei", 12, QFont.Weight.Bold))
        layout.addWidget(title)

        # 输入框
        self.input_edit = QPlainTextEdit()
        self.input_edit.setPlaceholderText(
            "请输入电路需求，例如：\n"
            "• 5V转3.3V稳压电路，带LED指示灯\n"
            "• 12V到3.3V的分压采样电路\n"
            "• 1kHz低通滤波器\n"
            "• LED电源指示灯"
        )
        self.input_edit.setFont(QFont("Microsoft YaHei", 11))
        self.input_edit.setMaximumHeight(120)
        layout.addWidget(self.input_edit)

        # 快捷模板选择
        template_group = QGroupBox("快捷模板")
        tpl_layout = QVBoxLayout(template_group)

        self.template_combo = QComboBox()
        self.template_combo.addItem("-- 自定义输入 --", "")
        for name in list_templates():
            t = get_template(name)
            if t:
                self.template_combo.addItem(f"{t.display_name} ({t.name})", name)
        self.template_combo.currentIndexChanged.connect(self._on_template_changed)
        tpl_layout.addWidget(self.template_combo)

        layout.addWidget(template_group)

        # 模式选择
        mode_group = QGroupBox("运行模式")
        mode_layout = QVBoxLayout(mode_group)

        self.mode_combo = QComboBox()
        self.mode_combo.addItem("离线Mock（无需网络）", "mock")
        self.mode_combo.addItem("在线LLM（kimi-k2.5）", "online")
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        mode_layout.addWidget(self.mode_combo)

        # 后端链路选择
        self.chain_combo = QComboBox()
        self.chain_combo.addItem("经典链（模板驱动）", "classic")
        self.chain_combo.addItem("新主链（库驱动+IR+审查）", "new")
        self.chain_combo.currentIndexChanged.connect(self._on_chain_changed)
        mode_layout.addWidget(self.chain_combo)

        layout.addWidget(mode_group)

        # 按钮区
        btn_layout = QHBoxLayout()

        self.generate_btn = QPushButton("生成原理图")
        self.generate_btn.setFont(QFont("Microsoft YaHei", 12, QFont.Weight.Bold))
        self.generate_btn.setMinimumHeight(44)
        self.generate_btn.setStyleSheet(
            "QPushButton {"
            "  background-color: #2563EB; color: white; border-radius: 6px;"
            "  padding: 8px 16px;"
            "}"
            "QPushButton:hover { background-color: #1D4ED8; }"
            "QPushButton:pressed { background-color: #1E40AF; }"
            "QPushButton:disabled { background-color: #94A3B8; }"
        )
        self.generate_btn.clicked.connect(self._on_generate)
        btn_layout.addWidget(self.generate_btn)

        self.demo_btn = QPushButton("运行Demo")
        self.demo_btn.setMinimumHeight(44)
        self.demo_btn.setStyleSheet(
            "QPushButton {"
            "  background-color: #7C3AED; color: white; border-radius: 6px;"
            "  padding: 8px 16px;"
            "}"
            "QPushButton:hover { background-color: #6D28D9; }"
            "QPushButton:pressed { background-color: #5B21B6; }"
            "QPushButton:disabled { background-color: #94A3B8; }"
        )
        self.demo_btn.clicked.connect(self._on_demo)
        btn_layout.addWidget(self.demo_btn)

        layout.addLayout(btn_layout)

        # 进度条
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)  # 不确定进度
        self.progress.hide()
        layout.addWidget(self.progress)

        # ERC 快速摘要
        self.quick_status = QLabel("")
        self.quick_status.setWordWrap(True)
        self.quick_status.setStyleSheet("color: #666; font-size: 11px; padding: 4px;")
        layout.addWidget(self.quick_status)

        layout.addStretch()

        return panel

    def _setup_toolbar(self) -> None:
        """构建工具栏"""
        toolbar = QToolBar("工具栏")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        # 导出 SVG
        export_svg_action = QAction("导出SVG", self)
        export_svg_action.triggered.connect(self._on_export_svg)
        toolbar.addAction(export_svg_action)

        # 导出 BOM
        export_bom_action = QAction("导出BOM", self)
        export_bom_action.triggered.connect(self._on_export_bom)
        toolbar.addAction(export_bom_action)

        # 导出 SPICE
        export_spice_action = QAction("导出SPICE", self)
        export_spice_action.triggered.connect(self._on_export_spice)
        toolbar.addAction(export_spice_action)

        toolbar.addSeparator()

        # 清空
        clear_action = QAction("清空", self)
        clear_action.triggered.connect(self._on_clear)
        toolbar.addAction(clear_action)

    def _setup_statusbar(self) -> None:
        self.statusbar = QStatusBar()
        self.setStatusBar(self.statusbar)
        self.statusbar.showMessage("就绪 — 输入电路需求后点击「生成原理图」")

    # ── 事件处理 ──────────────────────────────────────────────

    def _on_template_changed(self, index: int) -> None:
        """快捷模板下拉框变化"""
        template_name = self.template_combo.currentData()
        if not template_name:
            return

        # 预填输入
        preset_inputs = {
            "voltage_divider": "12V到3.3V的分压采样电路",
            "ldo_regulator": "5V转3.3V稳压电路",
            "led_indicator": "3.3V绿色LED电源指示灯",
            "rc_lowpass": "1kHz低通滤波器",
        }
        text = preset_inputs.get(template_name, "")
        if text:
            self.input_edit.setPlainText(text)

    def _on_mode_changed(self, index: int) -> None:
        """切换在线/离线模式"""
        mode = self.mode_combo.currentData()
        use_mock = mode == "mock"
        self.engine = SchemaForgeEngine(use_mock=use_mock)
        self._design_session = None  # 重建 session
        mode_text = "离线Mock" if use_mock else "在线LLM (kimi-k2.5)"
        self.statusbar.showMessage(f"已切换至: {mode_text}")

    def _on_chain_changed(self, index: int) -> None:
        """切换后端链路"""
        chain = self.chain_combo.currentData()
        if chain == "new":
            self._ensure_design_session()
            self.statusbar.showMessage("已切换至: 新主链（库驱动+IR+审查）")
        else:
            self.statusbar.showMessage("已切换至: 经典链（模板驱动）")

    def _ensure_design_session(self) -> None:
        """确保 DesignSession 已初始化"""
        if self._design_session is None:
            use_mock = self.mode_combo.currentData() == "mock"
            self._design_session = DesignSession(
                store_dir=Path("schemaforge/store"),
                use_mock=use_mock,
            )

    def _on_generate(self) -> None:
        """点击生成"""
        user_input = self.input_edit.toPlainText().strip()
        if not user_input:
            QMessageBox.warning(self, "提示", "请先输入电路需求！")
            return

        self._start_processing(user_input)

    def _on_demo(self) -> None:
        """运行Demo — 默认 LDO+LED 组合"""
        self.input_edit.setPlainText("5V转3.3V稳压电路，带绿色LED电源指示灯")
        self._start_processing("5V转3.3V稳压电路，带绿色LED电源指示灯")

    def _on_chat_message(self, text: str) -> None:
        """对话面板发送消息 — 当前转为生成请求"""
        self._start_processing(text)

    def _start_processing(self, user_input: str) -> None:
        """启动后台处理"""
        self.generate_btn.setEnabled(False)
        self.demo_btn.setEnabled(False)
        self.progress.show()
        self.progress_header.set_busy(True)
        self.statusbar.showMessage(f"处理中... {user_input[:40]}")
        self.quick_status.setText("正在调用引擎...")

        # 清空日志并记录开始
        self.log_panel.clear()
        self.log_panel.append_log(f"开始处理: {user_input[:60]}")

        # 在对话面板显示用户输入
        self.chat_panel.add_message("user", user_input)

        use_new_chain = self.chain_combo.currentData() == "new"

        if use_new_chain:
            self._ensure_design_session()
            assert self._design_session is not None
            self.log_panel.append_log("后端: 新主链（库驱动+IR+审查）")
            self.session_worker = DesignSessionWorker(
                self._design_session,
                user_input,
            )
            self.session_worker.finished.connect(self._on_session_result)
            self.session_worker.error.connect(self._on_error)
            self.session_worker.progress.connect(self._on_progress)
            self.session_worker.start()
        else:
            self.log_panel.append_log("后端: 经典链（模板驱动）")
            self.worker = EngineWorker(self.engine, user_input)
            self.worker.finished.connect(self._on_result)
            self.worker.error.connect(self._on_error)
            self.worker.progress.connect(self._on_progress)
            self.worker.start()

    def _on_progress(self, message: str, percentage: int) -> None:
        """处理引擎进度回调"""
        self.log_panel.append_log(message)
        self.log_panel.update_step(percentage)
        self.progress_header.handle_event(
            ProgressEvent(
                message=message,
                percentage=percentage,
                stage=message,
            )
        )

    def _on_result(self, result: EngineResult) -> None:
        """处理引擎返回结果"""
        self.generate_btn.setEnabled(True)
        self.demo_btn.setEnabled(True)
        self.progress.hide()
        self.progress_header.set_busy(False)
        self.last_result = result

        if not result.success:
            self.statusbar.showMessage(f"失败 — 阶段: {result.stage}")
            self.quick_status.setText(f"错误: {result.error}")
            self.quick_status.setStyleSheet(
                "color: #DC2626; font-size: 11px; padding: 4px;"
            )
            self.log_panel.append_log(f"❌ 失败: {result.error}")
            self.chat_panel.add_message("system", f"生成失败: {result.error}")
            QMessageBox.critical(
                self, "生成失败", f"阶段: {result.stage}\n\n{result.error}"
            )
            return

        # 成功 — 更新所有面板
        self.statusbar.showMessage(f"完成 — {result.design_name}")
        self.quick_status.setStyleSheet(
            "color: #16A34A; font-size: 11px; padding: 4px;"
        )
        self.log_panel.append_log(f"✅ 完成: {result.design_name}")
        self.chat_panel.add_message(
            "assistant",
            f"原理图已生成：{result.design_name}\n"
            f"模块: {len(result.circuits)} 个 | SVG: {len(result.svg_paths)} 个",
        )

        # SVG 预览
        self.svg_preview.load_svgs(result.svg_paths)

        # BOM
        self.bom_text.setPlainText(result.bom_text.strip())

        # SPICE
        self.spice_text.setPlainText(result.spice_text.strip())

        # ERC
        if result.erc_errors:
            erc_lines = []
            errors = [e for e in result.erc_errors if e.severity.value == "error"]
            warnings = [e for e in result.erc_errors if e.severity.value != "error"]
            if errors:
                erc_lines.append(f"=== 错误 ({len(errors)}) ===\n")
                for e in errors:
                    erc_lines.append(f"  [X] [{e.rule}] {e.message}")
            if warnings:
                erc_lines.append(f"\n=== 警告 ({len(warnings)}) ===\n")
                for e in warnings:
                    erc_lines.append(f"  [!] [{e.rule}] {e.message}")
            self.erc_text.setPlainText("\n".join(erc_lines))
            self.quick_status.setText(
                f"生成成功 | SVG: {len(result.svg_paths)} 个 | "
                f"ERC: {len(errors)} 错误, {len(warnings)} 警告"
            )
        else:
            self.erc_text.setPlainText("ERC 检查全部通过，无错误无警告。")
            self.quick_status.setText(
                f"生成成功 | SVG: {len(result.svg_paths)} 个 | ERC: 全部通过"
            )

        # 设计概要
        summary_lines = [
            f"设计名称: {result.design_name}",
            f"描述: {result.description}",
            "",
            f"模块数量: {len(result.circuits)}",
            f"SVG文件: {len(result.svg_paths)} 个",
        ]
        if result.svg_paths:
            summary_lines.append("")
            summary_lines.append("SVG文件路径:")
            for p in result.svg_paths:
                summary_lines.append(f"  {p}")
        if result.notes:
            summary_lines.append("")
            summary_lines.append(f"设计备注:\n  {result.notes}")

        self.summary_text.setPlainText("\n".join(summary_lines))

        # 自动切换到 BOM 标签
        self.result_tabs.setCurrentIndex(0)

    def _on_session_result(self, result: DesignSessionResult) -> None:
        """处理新主链 DesignSession 返回结果"""
        self.generate_btn.setEnabled(True)
        self.demo_btn.setEnabled(True)
        self.progress.hide()
        self.progress_header.set_busy(False)
        self.last_session_result = result

        if not result.success:
            self.statusbar.showMessage(f"失败 — 阶段: {result.stage}")
            self.quick_status.setText(f"错误: {result.error}")
            self.quick_status.setStyleSheet(
                "color: #DC2626; font-size: 11px; padding: 4px;"
            )
            self.log_panel.append_log(f"[X] 失败: {result.error}")
            self.chat_panel.add_message("system", f"生成失败: {result.error}")
            QMessageBox.critical(
                self,
                "生成失败",
                f"阶段: {result.stage}\n\n{result.error}",
            )
            return

        design_name = result.plan.name if result.plan else "未命名"
        self.statusbar.showMessage(f"完成 — {design_name}")
        self.quick_status.setStyleSheet(
            "color: #16A34A; font-size: 11px; padding: 4px;"
        )
        self.log_panel.append_log(f"[OK] 完成: {design_name}")
        self.chat_panel.add_message(
            "assistant",
            f"原理图已生成：{design_name}\n"
            f"模块: {len(result.modules)} 个 | SVG: {len(result.svg_paths)} 个",
        )

        # SVG 预览
        self.svg_preview.load_svgs(result.svg_paths)

        # BOM
        self.bom_text.setPlainText(result.bom_text.strip())

        # SPICE
        self.spice_text.setPlainText(result.spice_text.strip())

        # ERC — 新主链通过 design_review 显示
        if result.design_review is not None:
            review = result.design_review
            blocking = [i for i in review.issues if i.severity.value == "blocking"]
            warnings = [i for i in review.issues if i.severity.value != "blocking"]
            erc_lines = ["=== 设计审查 (新主链) ===\n"]
            if blocking:
                erc_lines.append(f"阻断: {len(blocking)}")
                for b in blocking:
                    erc_lines.append(f"  [X] [{b.rule_id}] {b.message}")
            if warnings:
                erc_lines.append(f"\n警告: {len(warnings)}")
                for w in warnings:
                    erc_lines.append(f"  [!] [{w.rule_id}] {w.message}")
            self.erc_text.setPlainText("\n".join(erc_lines))
            self.quick_status.setText(
                f"生成成功 | SVG: {len(result.svg_paths)} 个 | "
                f"审查: {len(blocking)} 阻断, {len(warnings)} 警告"
            )
        else:
            self.erc_text.setPlainText("设计审查通过，无问题。")
            self.quick_status.setText(
                f"生成成功 | SVG: {len(result.svg_paths)} 个 | 审查: 全部通过"
            )

        # 设计概要
        summary_lines = [
            f"设计名称: {design_name}",
            "后端: 新主链（库驱动+IR+审查）",
            f"模块数量: {len(result.modules)}",
            f"SVG文件: {len(result.svg_paths)} 个",
        ]
        for mr in result.modules:
            d = mr.to_dict()
            status = "OK" if d.get("review_passed") else "待审"
            summary_lines.append(
                f"  {d['role']}: {d['device'] or '未匹配'} "
                f"({d.get('solver_candidates', 0)} 候选, {status})"
            )
        if result.reference_design is not None:
            summary_lines.append(f"\n参考设计: {result.reference_design.name}")
        if result.svg_paths:
            summary_lines.append("\nSVG文件路径:")
            for p in result.svg_paths:
                summary_lines.append(f"  {p}")
        self.summary_text.setPlainText("\n".join(summary_lines))

        # 自动切换到 BOM 标签
        self.result_tabs.setCurrentIndex(0)

    def _on_error(self, error_msg: str) -> None:
        """处理引擎异常"""
        self.generate_btn.setEnabled(True)
        self.demo_btn.setEnabled(True)
        self.progress.hide()
        self.progress_header.set_busy(False)
        self.statusbar.showMessage("引擎异常")
        self.quick_status.setText(f"异常: {error_msg}")
        self.quick_status.setStyleSheet(
            "color: #DC2626; font-size: 11px; padding: 4px;"
        )
        self.log_panel.append_log(f"❌ 异常: {error_msg}")
        self.chat_panel.add_message("system", f"引擎异常: {error_msg}")
        QMessageBox.critical(self, "引擎异常", error_msg)

    def _on_clear(self) -> None:
        """清空所有内容"""
        self.input_edit.clear()
        self.svg_preview.clear()
        self.bom_text.clear()
        self.spice_text.clear()
        self.erc_text.clear()
        self.summary_text.clear()
        self.log_panel.clear()
        self.chat_panel.clear()
        self.progress_header.reset()
        self.quick_status.setText("")
        self.template_combo.setCurrentIndex(0)
        self.statusbar.showMessage("已清空")
        self.last_result = None
        self.last_session_result = None

    def _on_export_svg(self) -> None:
        """导出SVG文件"""
        if not self.last_result or not self.last_result.svg_paths:
            QMessageBox.information(
                self, "提示", "没有可导出的SVG文件，请先生成原理图。"
            )
            return

        dir_path = QFileDialog.getExistingDirectory(self, "选择导出目录")
        if not dir_path:
            return

        import shutil

        count = 0
        for src in self.last_result.svg_paths:
            if os.path.exists(src):
                dst = os.path.join(dir_path, Path(src).name)
                shutil.copy2(src, dst)
                count += 1

        QMessageBox.information(
            self, "导出完成", f"已导出 {count} 个SVG文件到:\n{dir_path}"
        )

    def _on_export_bom(self) -> None:
        """导出BOM"""
        if not self.last_result or not self.last_result.bom_text:
            QMessageBox.information(self, "提示", "没有可导出的BOM，请先生成原理图。")
            return

        file_path, _ = QFileDialog.getSaveFileName(
            self, "导出BOM", "bom.md", "Markdown (*.md);;所有文件 (*)"
        )
        if file_path:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(self.last_result.bom_text)
            QMessageBox.information(self, "导出完成", f"BOM已导出到:\n{file_path}")

    def _on_export_spice(self) -> None:
        """导出SPICE网表"""
        if not self.last_result or not self.last_result.spice_text:
            QMessageBox.information(
                self, "提示", "没有可导出的SPICE网表，请先生成原理图。"
            )
            return

        file_path, _ = QFileDialog.getSaveFileName(
            self, "导出SPICE", "circuit.spice", "SPICE (*.spice);;所有文件 (*)"
        )
        if file_path:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(self.last_result.spice_text)
            QMessageBox.information(
                self, "导出完成", f"SPICE网表已导出到:\n{file_path}"
            )


# ============================================================
# 入口
# ============================================================


def main() -> None:
    app = QApplication(sys.argv)

    # 全局字体
    font = QFont("Microsoft YaHei", 9)
    app.setFont(font)

    # 应用样式
    app.setStyle("Fusion")

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
