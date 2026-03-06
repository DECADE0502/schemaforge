"""进度状态栏控件

显示当前处理阶段、进度条、阶段指示灯和取消按钮。

6 个阶段步骤：AI调用 → 验证 → 实例化 → ERC检查 → 渲染 → 导出

用法::

    header = ProgressHeader()
    header.set_busy(True)
    header.update_progress("正在调用AI模型...", 15)
    header.set_stage("验证")
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QWidget,
)

# ============================================================
# 常量
# ============================================================

_STAGES: list[str] = ["AI调用", "验证", "实例化", "ERC检查", "渲染", "导出"]

_COLOR_PENDING = "#5a5a5a"
_COLOR_ACTIVE = "#007acc"
_COLOR_DONE = "#4ec9b0"
_BG_COLOR = "#252526"


# ============================================================
# ProgressHeader
# ============================================================


class ProgressHeader(QWidget):
    """进度状态栏。

    Signals:
        cancel_clicked(): 用户点击取消按钮时发出。
    """

    cancel_clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._current_stage_index: int = -1
        self._setup_ui()
        self.reset()

    # ----------------------------------------------------------
    # UI 构建
    # ----------------------------------------------------------

    def _setup_ui(self) -> None:
        self.setStyleSheet(f"ProgressHeader {{ background-color: {_BG_COLOR}; }}")
        self.setFixedHeight(52)

        root = QHBoxLayout(self)
        root.setContentsMargins(12, 4, 12, 4)
        root.setSpacing(10)

        # --- 阶段标签 ---
        self._stage_label = QLabel("就绪")
        self._stage_label.setStyleSheet(
            "font-weight: bold; color: #cccccc; min-width: 60px;"
        )
        root.addWidget(self._stage_label)

        # --- 消息标签 ---
        self._message_label = QLabel("")
        self._message_label.setStyleSheet("color: #cccccc;")
        self._message_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred,
        )
        root.addWidget(self._message_label, 1)

        # --- 阶段步骤指示灯 ---
        self._step_labels: list[QLabel] = []
        for stage_name in _STAGES:
            lbl = QLabel(stage_name)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet(
                f"color: {_COLOR_PENDING}; padding: 0 4px; font-size: 11px;"
            )
            self._step_labels.append(lbl)
            root.addWidget(lbl)

        # --- 进度条 ---
        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._progress_bar.setTextVisible(False)
        self._progress_bar.setFixedWidth(120)
        self._progress_bar.setFixedHeight(4)
        root.addWidget(self._progress_bar)

        # --- 取消按钮 ---
        self._cancel_button = QPushButton("取消")
        self._cancel_button.setFixedWidth(50)
        self._cancel_button.setStyleSheet(
            "QPushButton {"
            "  background-color: transparent;"
            "  color: #cccccc;"
            "  border: 1px solid #3c3c3c;"
            "  border-radius: 2px;"
            "  padding: 2px 8px;"
            "}"
            "QPushButton:hover {"
            "  background-color: #2a2d2e;"
            "  border-color: #cccccc;"
            "}"
        )
        self._cancel_button.clicked.connect(self.cancel_clicked.emit)
        root.addWidget(self._cancel_button)

    # ----------------------------------------------------------
    # 公开 API
    # ----------------------------------------------------------

    def set_busy(self, busy: bool) -> None:
        """显示或隐藏进度条和取消按钮。

        Args:
            busy: True 时显示进度条和取消按钮。
        """
        self._progress_bar.setVisible(busy)
        self._cancel_button.setVisible(busy)
        if not busy:
            self._progress_bar.setValue(0)

    def update_progress(self, message: str, percentage: int) -> None:
        """更新消息和进度条。

        Args:
            message: 描述当前操作的文本。
            percentage: 0–100 的进度值。
        """
        self._message_label.setText(message)
        self._progress_bar.setValue(max(0, min(100, percentage)))

    def set_stage(self, stage: str) -> None:
        """更新当前阶段并刷新步骤指示灯。

        Args:
            stage: 阶段名称，应与 _STAGES 中的某项匹配。
        """
        self._stage_label.setText(stage)

        # 查找当前阶段索引
        try:
            idx = _STAGES.index(stage)
        except ValueError:
            # 未知阶段名，不更新指示灯
            return

        self._current_stage_index = idx
        self._refresh_step_indicators()

    def reset(self) -> None:
        """重置为初始状态：清除所有文本，隐藏进度条。"""
        self._stage_label.setText("就绪")
        self._message_label.setText("")
        self._progress_bar.setValue(0)
        self._current_stage_index = -1
        self.set_busy(False)
        self._refresh_step_indicators()

    # ----------------------------------------------------------
    # 内部方法
    # ----------------------------------------------------------

    def _refresh_step_indicators(self) -> None:
        """根据当前阶段索引刷新所有步骤指示灯。"""
        for i, lbl in enumerate(self._step_labels):
            stage_name = _STAGES[i]
            if i < self._current_stage_index:
                # 已完成：绿色 + ✓
                lbl.setText(f"✓ {stage_name}")
                lbl.setStyleSheet(
                    f"color: {_COLOR_DONE}; padding: 0 4px; font-size: 11px;"
                )
            elif i == self._current_stage_index:
                # 当前激活：蓝色 + 加粗
                lbl.setText(stage_name)
                lbl.setStyleSheet(
                    f"color: {_COLOR_ACTIVE}; font-weight: bold;"
                    f" padding: 0 4px; font-size: 11px;"
                )
            else:
                # 待完成：灰色
                lbl.setText(stage_name)
                lbl.setStyleSheet(
                    f"color: {_COLOR_PENDING}; padding: 0 4px; font-size: 11px;"
                )
