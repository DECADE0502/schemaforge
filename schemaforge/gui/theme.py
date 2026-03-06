"""VS Code Dark 主题 — PySide6 QSS 样式表

提供完整的 VS Code 暗色主题样式，覆盖所有常用 Qt 控件。

用法::

    from schemaforge.gui.theme import apply_theme
    app = QApplication(sys.argv)
    apply_theme(app)
"""

from __future__ import annotations

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

# ============================================================
# 颜色常量
# ============================================================

_EDITOR_BG = "#1e1e1e"
_SIDEBAR_BG = "#252526"
_TOOLBAR_BG = "#2d2d2d"
_ACCENT = "#007acc"
_TEXT = "#cccccc"
_BORDER = "#3c3c3c"
_SELECTION = "#094771"
_TEXT_SELECTION = "#264f78"

_HOVER = "#2a2d2e"
_PRESSED = "#1a1a2e"
_DISABLED_TEXT = "#5a5a5a"
_INPUT_BG = "#3c3c3c"
_SCROLLBAR_BG = "#1e1e1e"
_SCROLLBAR_HANDLE = "rgba(121, 121, 121, 0.4)"
_SCROLLBAR_HANDLE_HOVER = "rgba(121, 121, 121, 0.7)"
_MENU_BG = "#252526"
_TOOLTIP_BG = "#252526"
_GREEN = "#4ec9b0"
_STATUS_BAR_BG = "#007acc"

# ============================================================
# QSS 样式表
# ============================================================

VSCODE_DARK_QSS: str = f"""
/* ========== 全局字体 ========== */
* {{
    font-family: "Segoe UI";
    font-size: 13px;
    color: {_TEXT};
}}

/* ========== QMainWindow ========== */
QMainWindow {{
    background-color: {_EDITOR_BG};
}}

/* ========== QWidget ========== */
QWidget {{
    background-color: {_EDITOR_BG};
    color: {_TEXT};
    selection-background-color: {_TEXT_SELECTION};
    selection-color: {_TEXT};
}}

/* ========== QLabel ========== */
QLabel {{
    background-color: transparent;
    color: {_TEXT};
    padding: 1px;
}}

/* ========== QGroupBox ========== */
QGroupBox {{
    background-color: {_SIDEBAR_BG};
    border: 1px solid {_BORDER};
    border-radius: 4px;
    margin-top: 8px;
    padding-top: 16px;
    font-weight: bold;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 10px;
    padding: 0 4px;
    color: {_TEXT};
}}

/* ========== QPushButton (primary) ========== */
QPushButton {{
    background-color: {_ACCENT};
    color: #ffffff;
    border: none;
    border-radius: 2px;
    padding: 5px 14px;
    min-height: 20px;
}}
QPushButton:hover {{
    background-color: #1e8ad6;
}}
QPushButton:pressed {{
    background-color: #005a9e;
}}
QPushButton:disabled {{
    background-color: {_BORDER};
    color: {_DISABLED_TEXT};
}}

/* ========== QPushButton secondary (flat) ========== */
QPushButton[flat="true"],
QPushButton.secondary {{
    background-color: transparent;
    color: {_TEXT};
    border: 1px solid {_BORDER};
}}
QPushButton[flat="true"]:hover,
QPushButton.secondary:hover {{
    background-color: {_HOVER};
    border-color: {_TEXT};
}}
QPushButton[flat="true"]:pressed,
QPushButton.secondary:pressed {{
    background-color: {_PRESSED};
}}

/* ========== QToolButton ========== */
QToolButton {{
    background-color: transparent;
    color: {_TEXT};
    border: none;
    border-radius: 2px;
    padding: 4px;
}}
QToolButton:hover {{
    background-color: {_HOVER};
}}
QToolButton:pressed {{
    background-color: {_BORDER};
}}
QToolButton:checked {{
    background-color: {_BORDER};
}}

/* ========== QLineEdit ========== */
QLineEdit {{
    background-color: {_INPUT_BG};
    color: {_TEXT};
    border: 1px solid {_BORDER};
    border-radius: 2px;
    padding: 4px 8px;
    selection-background-color: {_TEXT_SELECTION};
}}
QLineEdit:focus {{
    border-color: {_ACCENT};
}}
QLineEdit:disabled {{
    color: {_DISABLED_TEXT};
    background-color: {_SIDEBAR_BG};
}}

/* ========== QTextEdit / QPlainTextEdit ========== */
QTextEdit, QPlainTextEdit {{
    background-color: {_EDITOR_BG};
    color: {_TEXT};
    border: 1px solid {_BORDER};
    border-radius: 2px;
    padding: 4px;
    selection-background-color: {_TEXT_SELECTION};
}}
QTextEdit:focus, QPlainTextEdit:focus {{
    border-color: {_ACCENT};
}}

/* ========== QComboBox ========== */
QComboBox {{
    background-color: {_INPUT_BG};
    color: {_TEXT};
    border: 1px solid {_BORDER};
    border-radius: 2px;
    padding: 4px 8px;
    min-height: 20px;
}}
QComboBox:hover {{
    border-color: {_ACCENT};
}}
QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 20px;
    border-left: 1px solid {_BORDER};
    background-color: transparent;
}}
QComboBox::down-arrow {{
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid {_TEXT};
    margin-right: 6px;
}}
QComboBox QAbstractItemView {{
    background-color: {_MENU_BG};
    color: {_TEXT};
    border: 1px solid {_BORDER};
    selection-background-color: {_SELECTION};
    outline: none;
}}

/* ========== QTabWidget ========== */
QTabWidget::pane {{
    background-color: {_EDITOR_BG};
    border: none;
    border-top: 1px solid {_BORDER};
}}

/* ========== QTabBar (VS Code style) ========== */
QTabBar {{
    background-color: {_SIDEBAR_BG};
    qproperty-drawBase: 0;
}}
QTabBar::tab {{
    background-color: {_SIDEBAR_BG};
    color: {_DISABLED_TEXT};
    border: none;
    border-bottom: 2px solid transparent;
    padding: 8px 16px;
    min-width: 80px;
}}
QTabBar::tab:selected {{
    background-color: {_EDITOR_BG};
    color: {_TEXT};
    border-bottom: 2px solid {_ACCENT};
}}
QTabBar::tab:hover:!selected {{
    background-color: {_HOVER};
    color: {_TEXT};
}}
QTabBar::close-button {{
    image: none;
    subcontrol-position: right;
}}

/* ========== QTreeView ========== */
QTreeView {{
    background-color: {_SIDEBAR_BG};
    color: {_TEXT};
    border: none;
    outline: none;
    show-decoration-selected: 1;
}}
QTreeView::item {{
    padding: 3px 4px;
    border: none;
}}
QTreeView::item:hover {{
    background-color: {_HOVER};
}}
QTreeView::item:selected {{
    background-color: {_SELECTION};
    color: {_TEXT};
}}
QTreeView::branch {{
    background-color: transparent;
}}

/* ========== QTableView ========== */
QTableView {{
    background-color: {_EDITOR_BG};
    color: {_TEXT};
    border: 1px solid {_BORDER};
    gridline-color: {_BORDER};
    selection-background-color: {_SELECTION};
    outline: none;
}}
QTableView::item {{
    padding: 4px;
}}
QTableView::item:selected {{
    background-color: {_SELECTION};
    color: {_TEXT};
}}

/* ========== QHeaderView ========== */
QHeaderView {{
    background-color: {_SIDEBAR_BG};
    border: none;
}}
QHeaderView::section {{
    background-color: {_SIDEBAR_BG};
    color: {_TEXT};
    border: none;
    border-right: 1px solid {_BORDER};
    border-bottom: 1px solid {_BORDER};
    padding: 5px 8px;
    font-weight: bold;
}}
QHeaderView::section:hover {{
    background-color: {_HOVER};
}}

/* ========== QSplitter ========== */
QSplitter::handle {{
    background-color: {_BORDER};
}}
QSplitter::handle:horizontal {{
    width: 2px;
}}
QSplitter::handle:vertical {{
    height: 2px;
}}
QSplitter::handle:hover {{
    background-color: {_ACCENT};
}}

/* ========== QProgressBar ========== */
QProgressBar {{
    background-color: {_BORDER};
    border: none;
    border-radius: 2px;
    height: 4px;
    text-align: center;
    color: transparent;
}}
QProgressBar::chunk {{
    background-color: {_ACCENT};
    border-radius: 2px;
}}

/* ========== QScrollBar (thin VS Code style) ========== */
QScrollBar:vertical {{
    background-color: {_SCROLLBAR_BG};
    width: 10px;
    margin: 0;
    border: none;
}}
QScrollBar::handle:vertical {{
    background-color: {_SCROLLBAR_HANDLE};
    border-radius: 5px;
    min-height: 30px;
    margin: 0 1px;
}}
QScrollBar::handle:vertical:hover {{
    background-color: {_SCROLLBAR_HANDLE_HOVER};
}}
QScrollBar::add-line:vertical,
QScrollBar::sub-line:vertical {{
    height: 0;
    border: none;
    background: none;
}}
QScrollBar::add-page:vertical,
QScrollBar::sub-page:vertical {{
    background: none;
}}

QScrollBar:horizontal {{
    background-color: {_SCROLLBAR_BG};
    height: 10px;
    margin: 0;
    border: none;
}}
QScrollBar::handle:horizontal {{
    background-color: {_SCROLLBAR_HANDLE};
    border-radius: 5px;
    min-width: 30px;
    margin: 1px 0;
}}
QScrollBar::handle:horizontal:hover {{
    background-color: {_SCROLLBAR_HANDLE_HOVER};
}}
QScrollBar::add-line:horizontal,
QScrollBar::sub-line:horizontal {{
    width: 0;
    border: none;
    background: none;
}}
QScrollBar::add-page:horizontal,
QScrollBar::sub-page:horizontal {{
    background: none;
}}

/* ========== QMenu ========== */
QMenu {{
    background-color: {_MENU_BG};
    color: {_TEXT};
    border: 1px solid {_BORDER};
    padding: 4px 0;
}}
QMenu::item {{
    padding: 6px 30px 6px 20px;
    background-color: transparent;
}}
QMenu::item:selected {{
    background-color: {_SELECTION};
}}
QMenu::item:disabled {{
    color: {_DISABLED_TEXT};
}}
QMenu::separator {{
    height: 1px;
    background-color: {_BORDER};
    margin: 4px 10px;
}}
QMenu::indicator {{
    width: 14px;
    height: 14px;
    margin-left: 4px;
}}

/* ========== QToolBar ========== */
QToolBar {{
    background-color: {_TOOLBAR_BG};
    border: none;
    border-bottom: 1px solid {_BORDER};
    padding: 2px;
    spacing: 2px;
}}
QToolBar::separator {{
    width: 1px;
    background-color: {_BORDER};
    margin: 4px 2px;
}}

/* ========== QStatusBar (VS Code blue) ========== */
QStatusBar {{
    background-color: {_STATUS_BAR_BG};
    color: #ffffff;
    border: none;
    padding: 0;
    min-height: 22px;
}}
QStatusBar::item {{
    border: none;
}}
QStatusBar QLabel {{
    color: #ffffff;
    padding: 0 8px;
    background-color: transparent;
}}

/* ========== QToolTip ========== */
QToolTip {{
    background-color: {_TOOLTIP_BG};
    color: {_TEXT};
    border: 1px solid {_BORDER};
    padding: 4px 8px;
}}
"""


def apply_theme(app: QApplication) -> None:
    """将 VS Code 暗色主题应用到整个应用程序。

    设置全局字体 (Segoe UI, 13px) 和 QSS 样式表。

    Args:
        app: PySide6 QApplication 实例。
    """
    font = QFont("Segoe UI", 10)  # 10pt ≈ 13px at 96 DPI
    font.setPixelSize(13)
    app.setFont(font)
    app.setStyleSheet(VSCODE_DARK_QSS)
