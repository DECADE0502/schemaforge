"""进度头部组件

顶部阶段标签 + 进度条 + 取消按钮。
桥接 ProgressTracker 事件到 Qt 信号。
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QWidget,
)

from schemaforge.common.events import ProgressEvent, StateChangeEvent, WorkflowEvent


class ProgressHeader(QWidget):
    """进度头部

    显示当前阶段、进度条、取消按钮。

    信号:
        cancel_requested: 用户点击取消
    """

    cancel_requested = Signal()

    # 状态 → 中文显示名
    STATE_LABELS: dict[str, str] = {
        "idle": "就绪",
        "collecting": "采集资料",
        "extracting": "提取信息",
        "questioning": "AI 提问中",
        "validating": "校验中",
        "review": "待确认",
        "saving": "保存中",
        "searching": "搜索器件",
        "planning": "规划方案",
        "compiling": "编译设计",
        "rendering": "渲染中",
        "revision": "修改中",
        "done": "完成",
        "error": "出错",
    }

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(56)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)

        # 阶段标签
        self._stage_label = QLabel("就绪")
        self._stage_label.setFont(QFont("Microsoft YaHei", 11, QFont.Weight.Bold))
        self._stage_label.setMinimumWidth(120)
        layout.addWidget(self._stage_label)

        # 消息
        self._message_label = QLabel("")
        self._message_label.setFont(QFont("Microsoft YaHei", 10))
        self._message_label.setStyleSheet("color: #555;")
        layout.addWidget(self._message_label, 1)

        # 进度条
        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setFixedWidth(200)
        self._progress.setTextVisible(True)
        self._progress.setStyleSheet(
            "QProgressBar { border: 1px solid #ccc; border-radius: 4px; "
            "background: #f5f5f5; text-align: center; }"
            "QProgressBar::chunk { background: #2196f3; border-radius: 3px; }"
        )
        layout.addWidget(self._progress)

        # 取消按钮
        self._cancel_btn = QPushButton("取消")
        self._cancel_btn.setStyleSheet(
            "QPushButton { background: #ef5350; color: white; "
            "border-radius: 4px; padding: 4px 12px; }"
            "QPushButton:hover { background: #e53935; }"
            "QPushButton:disabled { background: #bdbdbd; }"
        )
        self._cancel_btn.clicked.connect(self.cancel_requested.emit)
        self._cancel_btn.setEnabled(False)
        layout.addWidget(self._cancel_btn)

    def handle_event(self, event: WorkflowEvent) -> None:
        """处理工作流事件，更新显示"""
        if isinstance(event, ProgressEvent):
            self._message_label.setText(event.message)
            if event.percentage > 0:
                self._progress.setRange(0, 100)
                self._progress.setValue(event.percentage)
            if event.stage:
                label = self.STATE_LABELS.get(event.stage, event.stage)
                self._stage_label.setText(label)
        elif isinstance(event, StateChangeEvent):
            label = self.STATE_LABELS.get(event.new_state, event.new_state)
            self._stage_label.setText(label)
            # 终态时禁用取消
            if event.new_state in ("done", "error", "idle"):
                self._cancel_btn.setEnabled(False)
                if event.new_state == "done":
                    self._progress.setValue(100)
            else:
                self._cancel_btn.setEnabled(True)

    def set_busy(self, busy: bool) -> None:
        """设置忙碌/空闲状态"""
        self._cancel_btn.setEnabled(busy)
        if busy:
            self._progress.setRange(0, 0)  # 不确定进度
        else:
            self._progress.setRange(0, 100)
            self._progress.setValue(0)

    def reset(self) -> None:
        """重置到初始状态"""
        self._stage_label.setText("就绪")
        self._message_label.setText("")
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._cancel_btn.setEnabled(False)
