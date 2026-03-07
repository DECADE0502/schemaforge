"""SchemaForge 主窗口

MainWindow — 顶层应用窗口，承载 DesignPage + LibraryPage 双标签页，
提供菜单栏（导出/视图/帮助）和状态栏。
"""

from __future__ import annotations

import shutil
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QFileDialog,
    QLabel,
    QMainWindow,
    QMenuBar,
    QMessageBox,
    QStatusBar,
    QTabWidget,
    QWidget,
)

from schemaforge.gui.pages.design_page import DesignPage
from schemaforge.gui.pages.library_page import LibraryPage


class MainWindow(QMainWindow):
    """SchemaForge 主窗口。

    Layout::

        ┌─────────────────────────────────────────────┐
        │ Menu: [文件] [视图] [帮助]                    │
        ├─────────────────────────────────────────────┤
        │ QTabWidget                                   │
        │   [⚡ 原理图设计]  [📦 器件库管理]              │
        ├─────────────────────────────────────────────┤
        │ QStatusBar: 就绪            SchemaForge v1.0 │
        └─────────────────────────────────────────────┘
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("SchemaForge — 约束驱动的AI原理图生成器")
        self.resize(1400, 900)

        self._init_pages()
        self._init_menubar()
        self._init_statusbar()

    # ==========================================================
    # 页面初始化
    # ==========================================================

    def _init_pages(self) -> None:
        self._tabs = QTabWidget()
        self.setCentralWidget(self._tabs)

        self._design_page = DesignPage()
        self._library_page = LibraryPage()

        self._tabs.addTab(self._design_page, "⚡ 原理图设计")
        self._tabs.addTab(self._library_page, "📦 器件库管理")

    # ==========================================================
    # 菜单栏
    # ==========================================================

    def _init_menubar(self) -> None:
        menubar: QMenuBar = self.menuBar()

        # ---- 文件菜单 ----
        file_menu = menubar.addMenu("文件")

        self._act_export_svg = QAction("导出SVG", self)
        self._act_export_svg.setShortcut(QKeySequence("Ctrl+E"))
        self._act_export_svg.triggered.connect(self._on_export_svg)
        file_menu.addAction(self._act_export_svg)

        self._act_export_bom = QAction("导出BOM", self)
        self._act_export_bom.triggered.connect(self._on_export_bom)
        file_menu.addAction(self._act_export_bom)

        self._act_export_spice = QAction("导出SPICE", self)
        self._act_export_spice.triggered.connect(self._on_export_spice)
        file_menu.addAction(self._act_export_spice)

        file_menu.addSeparator()

        self._act_quit = QAction("退出", self)
        self._act_quit.setShortcut(QKeySequence("Ctrl+Q"))
        self._act_quit.triggered.connect(self.close)
        file_menu.addAction(self._act_quit)

        # ---- 视图菜单 ----
        view_menu = menubar.addMenu("视图")

        self._act_fit = QAction("适应窗口", self)
        self._act_fit.setShortcut(QKeySequence("Ctrl+0"))
        self._act_fit.triggered.connect(self._on_fit_view)
        view_menu.addAction(self._act_fit)

        self._act_zoom_in = QAction("放大", self)
        self._act_zoom_in.setShortcut(QKeySequence("Ctrl+="))
        self._act_zoom_in.triggered.connect(self._on_zoom_in)
        view_menu.addAction(self._act_zoom_in)

        self._act_zoom_out = QAction("缩小", self)
        self._act_zoom_out.setShortcut(QKeySequence("Ctrl+-"))
        self._act_zoom_out.triggered.connect(self._on_zoom_out)
        view_menu.addAction(self._act_zoom_out)

        # ---- 帮助菜单 ----
        help_menu = menubar.addMenu("帮助")

        self._act_about = QAction("关于", self)
        self._act_about.triggered.connect(self._on_about)
        help_menu.addAction(self._act_about)

    # ==========================================================
    # 状态栏
    # ==========================================================

    def _init_statusbar(self) -> None:
        statusbar: QStatusBar = self.statusBar()

        self._status_label = QLabel("就绪")
        statusbar.addWidget(self._status_label, stretch=1)

        self._version_label = QLabel("SchemaForge v1.0")
        self._version_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        statusbar.addPermanentWidget(self._version_label)

    # ==========================================================
    # 菜单动作
    # ==========================================================

    def _on_export_svg(self) -> None:
        """导出当前 SVG 到用户选择的路径。"""
        svg_path = self._design_page.get_current_svg_path()
        if not svg_path:
            QMessageBox.information(self, "导出SVG", "当前没有可导出的 SVG 原理图。")
            return

        dest, _ = QFileDialog.getSaveFileName(
            self,
            "导出SVG",
            "schematic.svg",
            "SVG 文件 (*.svg)",
        )
        if not dest:
            return

        try:
            shutil.copy2(svg_path, dest)
            self._status_label.setText(f"SVG 已导出: {dest}")
        except OSError as exc:
            QMessageBox.warning(self, "导出失败", f"无法导出 SVG: {exc}")

    def _on_export_bom(self) -> None:
        """导出 BOM 文本到用户选择的路径。"""
        bom_text = self._design_page.get_bom_text()
        if not bom_text or bom_text.startswith("（"):
            QMessageBox.information(self, "导出BOM", "当前没有可导出的 BOM 数据。")
            return

        dest, _ = QFileDialog.getSaveFileName(
            self,
            "导出BOM",
            "bom.txt",
            "文本文件 (*.txt);;CSV 文件 (*.csv)",
        )
        if not dest:
            return

        try:
            Path(dest).write_text(bom_text, encoding="utf-8")
            self._status_label.setText(f"BOM 已导出: {dest}")
        except OSError as exc:
            QMessageBox.warning(self, "导出失败", f"无法导出 BOM: {exc}")

    def _on_export_spice(self) -> None:
        """导出 SPICE 网表到用户选择的路径。"""
        spice_text = self._design_page.get_spice_text()
        if not spice_text or spice_text.startswith("（"):
            QMessageBox.information(self, "导出SPICE", "当前没有可导出的 SPICE 数据。")
            return

        dest, _ = QFileDialog.getSaveFileName(
            self,
            "导出SPICE网表",
            "netlist.spice",
            "SPICE 文件 (*.spice *.cir);;文本文件 (*.txt)",
        )
        if not dest:
            return

        try:
            Path(dest).write_text(spice_text, encoding="utf-8")
            self._status_label.setText(f"SPICE 已导出: {dest}")
        except OSError as exc:
            QMessageBox.warning(self, "导出失败", f"无法导出 SPICE: {exc}")

    def _on_fit_view(self) -> None:
        """适应窗口（Grid Canvas 预览）。"""
        self._design_page._grid_canvas.fit_to_view()

    def _on_zoom_in(self) -> None:
        """放大（Grid Canvas 预览）。"""
        self._design_page._grid_canvas.zoom_in()

    def _on_zoom_out(self) -> None:
        """缩小（Grid Canvas 预览）。"""
        self._design_page._grid_canvas.zoom_out()

    def _on_about(self) -> None:
        """显示关于对话框。"""
        QMessageBox.about(
            self,
            "关于 SchemaForge",
            "<h3>SchemaForge v1.0</h3>"
            "<p>约束驱动的AI原理图生成器</p>"
            "<p>从自然语言需求到 SVG 原理图 + BOM + SPICE 网表，全流程本地化。</p>"
            "<p><b>技术栈:</b> PySide6 + kimi-k2.5 + schemdraw</p>"
            "<hr>"
            "<p>双主链架构：经典模板驱动 + 新主链库驱动IR审查</p>",
        )
