"""SchemaForge 全局 UI 主题

VS Code 风格深色主题，基于 pyqtdarktheme 底层 + 自定义 QSS 覆盖。
统一所有颜色、字体、间距、组件样式的中心化管理。
"""

from __future__ import annotations

# ── 设计令牌 (Design Tokens) ─────────────────────────────────

# 背景色阶
BG_BASE = "#1e1e1e"  # 编辑器背景
BG_PANEL = "#252526"  # 侧栏/面板
BG_ELEVATED = "#2d2d30"  # 卡片、输入框、下拉
BG_HOVER = "#2a2d2e"  # 悬停态
BG_ACTIVE = "#37373d"  # 激活/选中行

# 边框
BORDER = "#3e3e42"
BORDER_LIGHT = "#4e4e52"

# 强调色
ACCENT = "#0e7fd4"  # 主蓝 (VS Code blue)
ACCENT_HOVER = "#1a8fe3"
ACCENT_PRESSED = "#0b6ab5"
ACCENT_MUTED = "#094771"  # 选中行背景

# 语义色
SUCCESS = "#4ec9b0"
SUCCESS_BG = "#1a3a2a"
WARNING = "#dcdcaa"
WARNING_BG = "#3a3a1a"
ERROR = "#f44747"
ERROR_BG = "#3a1a1a"

# 文字
TEXT_PRIMARY = "#cccccc"
TEXT_MUTED = "#858585"
TEXT_ON_ACCENT = "#ffffff"

# 字体
FONT_FAMILY = (
    '"Microsoft YaHei", "Segoe UI", "PingFang SC", "Noto Sans CJK SC", sans-serif'
)
FONT_MONO = (
    '"Cascadia Code", "Consolas", "JetBrains Mono", "Source Code Pro", monospace'
)
FONT_SIZE_BASE = 13  # px
FONT_SIZE_SMALL = 11
FONT_SIZE_TITLE = 14
FONT_SIZE_HEADING = 16

# 间距
RADIUS_SM = 4
RADIUS_MD = 6
RADIUS_LG = 8

# ── 全局 QSS 覆盖层 ──────────────────────────────────────────


def get_app_stylesheet() -> str:
    """返回全局 QSS 字符串 (叠加在 qdarktheme 之上)"""
    import qdarktheme

    base = qdarktheme.load_stylesheet("dark")

    overlay = f"""
/* ================================================================
   SchemaForge — VS Code Dark Theme Overlay
   ================================================================ */

/* ── 全局基础 ──────────────────────────────────────────────── */
QWidget {{
    font-family: {FONT_FAMILY};
    font-size: {FONT_SIZE_BASE}px;
}}

QMainWindow {{
    background-color: {BG_BASE};
}}

QMainWindow::separator {{
    background-color: {BORDER};
    width: 1px;
    height: 1px;
}}

/* ── 标签页 (Tabs) ─────────────────────────────────────────── */
QTabWidget::pane {{
    border: none;
    background-color: {BG_BASE};
}}

QTabBar {{
    background-color: {BG_PANEL};
}}

QTabBar::tab {{
    background-color: {BG_PANEL};
    color: {TEXT_MUTED};
    border: none;
    border-bottom: 2px solid transparent;
    padding: 8px 18px;
    min-width: 100px;
    font-size: {FONT_SIZE_BASE}px;
    font-weight: 500;
}}

QTabBar::tab:selected {{
    background-color: {BG_BASE};
    color: {TEXT_PRIMARY};
    border-bottom: 2px solid {ACCENT};
}}

QTabBar::tab:hover:!selected {{
    background-color: {BG_HOVER};
    color: {TEXT_PRIMARY};
}}

/* ── 按钮 ──────────────────────────────────────────────────── */
QPushButton {{
    background-color: {BG_ELEVATED};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER};
    border-radius: {RADIUS_SM}px;
    padding: 6px 16px;
    min-height: 28px;
    font-weight: 500;
}}

QPushButton:hover {{
    background-color: {BG_HOVER};
    border-color: {BORDER_LIGHT};
}}

QPushButton:pressed {{
    background-color: {BG_ACTIVE};
}}

QPushButton:disabled {{
    background-color: {BG_ELEVATED};
    color: {TEXT_MUTED};
    border-color: {BORDER};
}}

QPushButton[class="primary"] {{
    background-color: {ACCENT};
    color: {TEXT_ON_ACCENT};
    border: none;
}}

QPushButton[class="primary"]:hover {{
    background-color: {ACCENT_HOVER};
}}

QPushButton[class="primary"]:pressed {{
    background-color: {ACCENT_PRESSED};
}}

QPushButton[class="primary"]:disabled {{
    background-color: {BG_ELEVATED};
    color: {TEXT_MUTED};
}}

QPushButton[class="success"] {{
    background-color: #2ea043;
    color: {TEXT_ON_ACCENT};
    border: none;
}}

QPushButton[class="success"]:hover {{
    background-color: #3fb950;
}}

QPushButton[class="danger"] {{
    background-color: #d73a49;
    color: {TEXT_ON_ACCENT};
    border: none;
}}

QPushButton[class="danger"]:hover {{
    background-color: #f85149;
}}

/* ── 输入框 ────────────────────────────────────────────────── */
QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox {{
    background-color: #3c3c3c;
    border: 1px solid {BORDER};
    border-radius: {RADIUS_SM}px;
    color: {TEXT_PRIMARY};
    padding: 6px 10px;
    selection-background-color: {ACCENT_MUTED};
    font-size: {FONT_SIZE_BASE}px;
}}

QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus {{
    border-color: {ACCENT};
}}

QLineEdit:disabled, QTextEdit:disabled, QPlainTextEdit:disabled {{
    background-color: {BG_ELEVATED};
    color: {TEXT_MUTED};
}}

/* ── 下拉框 ────────────────────────────────────────────────── */
QComboBox {{
    background-color: #3c3c3c;
    border: 1px solid {BORDER};
    border-radius: {RADIUS_SM}px;
    color: {TEXT_PRIMARY};
    padding: 4px 10px;
    min-height: 26px;
}}

QComboBox:hover {{
    border-color: {BORDER_LIGHT};
}}

QComboBox:focus {{
    border-color: {ACCENT};
}}

QComboBox::drop-down {{
    border: none;
    width: 24px;
}}

QComboBox QAbstractItemView {{
    background-color: {BG_PANEL};
    border: 1px solid {BORDER};
    color: {TEXT_PRIMARY};
    selection-background-color: {ACCENT_MUTED};
    selection-color: {TEXT_ON_ACCENT};
    outline: none;
}}

/* ── 表格 ──────────────────────────────────────────────────── */
QTableWidget, QTableView {{
    background-color: {BG_BASE};
    alternate-background-color: {BG_PANEL};
    border: 1px solid {BORDER};
    border-radius: {RADIUS_SM}px;
    gridline-color: {BG_ELEVATED};
    selection-background-color: {ACCENT_MUTED};
    selection-color: {TEXT_ON_ACCENT};
    color: {TEXT_PRIMARY};
    font-size: {FONT_SIZE_BASE}px;
}}

QTableWidget::item, QTableView::item {{
    padding: 6px 10px;
    border: none;
}}

QTableWidget::item:hover, QTableView::item:hover {{
    background-color: {BG_HOVER};
}}

QTableWidget::item:selected, QTableView::item:selected {{
    background-color: {ACCENT_MUTED};
    color: {TEXT_ON_ACCENT};
}}

QHeaderView::section {{
    background-color: {BG_PANEL};
    color: {TEXT_MUTED};
    font-size: {FONT_SIZE_SMALL}px;
    font-weight: 600;
    text-transform: uppercase;
    padding: 6px 10px;
    border: none;
    border-right: 1px solid {BORDER};
    border-bottom: 1px solid {BORDER};
}}

QHeaderView::section:hover {{
    background-color: {BG_HOVER};
    color: {TEXT_PRIMARY};
}}

QTableCornerButton::section {{
    background-color: {BG_PANEL};
    border: none;
    border-right: 1px solid {BORDER};
    border-bottom: 1px solid {BORDER};
}}

/* ── 分割器 ────────────────────────────────────────────────── */
QSplitter::handle {{
    background-color: {BORDER};
}}

QSplitter::handle:horizontal {{
    width: 1px;
}}

QSplitter::handle:vertical {{
    height: 1px;
}}

QSplitter::handle:hover {{
    background-color: {ACCENT};
}}

/* ── 滚动条 ────────────────────────────────────────────────── */
QScrollBar:vertical {{
    background: {BG_BASE};
    width: 10px;
    border: none;
}}

QScrollBar::handle:vertical {{
    background: #424242;
    border-radius: 5px;
    min-height: 30px;
    margin: 2px;
}}

QScrollBar::handle:vertical:hover {{
    background: #686868;
}}

QScrollBar::add-line:vertical,
QScrollBar::sub-line:vertical {{
    height: 0;
}}

QScrollBar::add-page:vertical,
QScrollBar::sub-page:vertical {{
    background: none;
}}

QScrollBar:horizontal {{
    background: {BG_BASE};
    height: 10px;
    border: none;
}}

QScrollBar::handle:horizontal {{
    background: #424242;
    border-radius: 5px;
    min-width: 30px;
    margin: 2px;
}}

QScrollBar::handle:horizontal:hover {{
    background: #686868;
}}

QScrollBar::add-line:horizontal,
QScrollBar::sub-line:horizontal {{
    width: 0;
}}

/* ── 分组框 ────────────────────────────────────────────────── */
QGroupBox {{
    background-color: {BG_PANEL};
    border: 1px solid {BORDER};
    border-radius: {RADIUS_MD}px;
    margin-top: 12px;
    padding-top: 16px;
    font-weight: 600;
    color: {TEXT_PRIMARY};
}}

QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 12px;
    padding: 0 6px;
    color: {TEXT_MUTED};
    font-size: {FONT_SIZE_SMALL}px;
}}

/* ── 进度条 ────────────────────────────────────────────────── */
QProgressBar {{
    background-color: {BG_ELEVATED};
    border: 1px solid {BORDER};
    border-radius: {RADIUS_SM}px;
    text-align: center;
    color: {TEXT_PRIMARY};
    font-size: {FONT_SIZE_SMALL}px;
    min-height: 20px;
}}

QProgressBar::chunk {{
    background-color: {ACCENT};
    border-radius: 3px;
}}

/* ── 工具栏 ────────────────────────────────────────────────── */
QToolBar {{
    background-color: {BG_PANEL};
    border: none;
    border-bottom: 1px solid {BORDER};
    padding: 4px 8px;
    spacing: 4px;
}}

QToolBar::separator {{
    background-color: {BORDER};
    width: 1px;
    margin: 4px 6px;
}}

QToolButton {{
    background-color: transparent;
    color: {TEXT_PRIMARY};
    border: none;
    border-radius: {RADIUS_SM}px;
    padding: 6px 12px;
    font-size: {FONT_SIZE_BASE}px;
}}

QToolButton:hover {{
    background-color: {BG_HOVER};
}}

QToolButton:pressed {{
    background-color: {BG_ACTIVE};
}}

/* ── 状态栏 ────────────────────────────────────────────────── */
QStatusBar {{
    background-color: {ACCENT};
    color: {TEXT_ON_ACCENT};
    font-size: {FONT_SIZE_SMALL}px;
    border: none;
}}

QStatusBar::item {{
    border: none;
}}

/* ── 提示框 ────────────────────────────────────────────────── */
QToolTip {{
    background-color: {BG_PANEL};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER_LIGHT};
    border-radius: {RADIUS_SM}px;
    padding: 6px 10px;
    font-size: {FONT_SIZE_SMALL}px;
}}

/* ── 菜单 ──────────────────────────────────────────────────── */
QMenu {{
    background-color: {BG_PANEL};
    border: 1px solid {BORDER};
    border-radius: {RADIUS_MD}px;
    padding: 4px 0;
}}

QMenu::item {{
    padding: 6px 28px 6px 12px;
    color: {TEXT_PRIMARY};
}}

QMenu::item:selected {{
    background-color: {ACCENT_MUTED};
    color: {TEXT_ON_ACCENT};
}}

QMenu::separator {{
    height: 1px;
    background-color: {BORDER};
    margin: 4px 0;
}}

/* ── 对话框 ────────────────────────────────────────────────── */
QMessageBox {{
    background-color: {BG_PANEL};
}}

/* ── 滚动区域 ─────────────────────────────────────────────── */
QScrollArea {{
    border: none;
    background-color: transparent;
}}

/* ── 标签 ──────────────────────────────────────────────────── */
QLabel {{
    color: {TEXT_PRIMARY};
    background: transparent;
}}

QLabel[class="muted"] {{
    color: {TEXT_MUTED};
    font-size: {FONT_SIZE_SMALL}px;
}}

QLabel[class="title"] {{
    font-size: {FONT_SIZE_HEADING}px;
    font-weight: 700;
    color: {TEXT_PRIMARY};
}}

QLabel[class="subtitle"] {{
    font-size: {FONT_SIZE_TITLE}px;
    font-weight: 600;
    color: {TEXT_PRIMARY};
}}

QLabel[class="error"] {{
    color: {ERROR};
    font-size: {FONT_SIZE_SMALL}px;
}}

QLabel[class="success"] {{
    color: {SUCCESS};
    font-size: {FONT_SIZE_SMALL}px;
}}

/* ── 文件列表 ─────────────────────────────────────────────── */
QListWidget {{
    background-color: {BG_ELEVATED};
    border: 1px solid {BORDER};
    border-radius: {RADIUS_SM}px;
    color: {TEXT_PRIMARY};
}}

QListWidget::item {{
    padding: 4px 8px;
}}

QListWidget::item:hover {{
    background-color: {BG_HOVER};
}}

QListWidget::item:selected {{
    background-color: {ACCENT_MUTED};
    color: {TEXT_ON_ACCENT};
}}

/* ── SchemaForge 特定组件 ─────────────────────────────────── */

/* 卡片容器 */
QFrame[class="card"] {{
    background-color: {BG_PANEL};
    border: 1px solid {BORDER};
    border-radius: {RADIUS_LG}px;
    padding: 12px;
}}

QFrame[class="card"]:hover {{
    border-color: {BORDER_LIGHT};
}}

/* 消息气泡 */
QFrame[class="bubble-user"] {{
    background-color: {ACCENT_MUTED};
    border: 1px solid #0a5a9e;
    border-radius: {RADIUS_LG}px;
    padding: 8px;
    margin: 2px 4px;
}}

QFrame[class="bubble-assistant"] {{
    background-color: {BG_ELEVATED};
    border: 1px solid {BORDER};
    border-radius: {RADIUS_LG}px;
    padding: 8px;
    margin: 2px 4px;
}}

QFrame[class="bubble-system"] {{
    background-color: {WARNING_BG};
    border: 1px solid #5a5a2a;
    border-radius: {RADIUS_LG}px;
    padding: 8px;
    margin: 2px 4px;
}}

/* 追问卡片 */
QFrame[class="question-card"] {{
    background-color: {WARNING_BG};
    border: 1px solid {WARNING};
    border-radius: {RADIUS_LG}px;
    padding: 10px;
    margin: 4px;
}}

/* 搜索结果卡片 */
QFrame[class="hit-card"] {{
    background-color: {BG_ELEVATED};
    border: 1px solid {BORDER};
    border-radius: {RADIUS_MD}px;
    padding: 10px;
    margin: 2px 0;
}}

QFrame[class="hit-card"]:hover {{
    border-color: {ACCENT};
    background-color: {BG_HOVER};
}}

/* Symbol 预览区 */
QGraphicsView[class="symbol-preview"] {{
    background-color: #1a1a2e;
    border: 1px solid {BORDER};
    border-radius: {RADIUS_SM}px;
}}

/* 日志面板 */
QPlainTextEdit[class="log-output"] {{
    background-color: #0d1117;
    color: #c9d1d9;
    border: 1px solid {BORDER};
    border-radius: {RADIUS_SM}px;
    font-family: {FONT_MONO};
    font-size: {FONT_SIZE_SMALL}px;
}}

"""
    return base + "\n" + overlay


def apply_theme(app: object) -> None:
    """应用完整主题到 QApplication"""
    from PySide6.QtGui import QFont

    stylesheet = get_app_stylesheet()
    app.setStyleSheet(stylesheet)  # type: ignore[union-attr]

    # 全局字体
    font = QFont("Microsoft YaHei", 10)
    font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    app.setFont(font)  # type: ignore[union-attr]
