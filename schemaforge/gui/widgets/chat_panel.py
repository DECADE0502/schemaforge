"""对话面板组件

支持 AI 和用户的多轮对话，包括：
- 普通文本消息
- AI 提问待回答卡片
- 图片附件
- 系统消息
"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)


class MessageBubble(QFrame):
    """单条消息气泡"""

    def __init__(
        self,
        role: str,
        content: str,
        timestamp: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setProperty("class", f"bubble-{role}")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(2)

        # 头部：角色 + 时间
        header = QHBoxLayout()
        role_map = {
            "user": "👤 用户",
            "assistant": "🤖 AI",
            "system": "⚙️ 系统",
            "tool": "🔧 工具",
        }
        role_label = QLabel(role_map.get(role, role))
        header.addWidget(role_label)
        header.addStretch()
        if timestamp:
            time_label = QLabel(timestamp)
            time_label.setProperty("class", "muted")
            header.addWidget(time_label)
        layout.addLayout(header)

        # 内容
        content_label = QLabel(content)
        content_label.setWordWrap(True)
        content_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        layout.addWidget(content_label)


class QuestionCard(QFrame):
    """AI 提问卡片 — 结构化回答控件"""

    answered = Signal(str, str)  # (question_id, answer)

    def __init__(
        self,
        question_id: str,
        text: str,
        answer_type: str = "text",
        choices: list[str] | None = None,
        default: str = "",
        evidence: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.question_id = question_id
        self.setProperty("class", "question-card")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        # 问题标题
        header = QLabel("❓ AI 需要确认")
        layout.addWidget(header)

        # 问题内容
        q_label = QLabel(text)
        q_label.setWordWrap(True)
        layout.addWidget(q_label)

        # 证据说明
        if evidence:
            ev_label = QLabel(f"💡 依据: {evidence}")
            ev_label.setWordWrap(True)
            ev_label.setProperty("class", "muted")
            layout.addWidget(ev_label)

        # 回答控件
        answer_row = QHBoxLayout()

        if answer_type == "choice" and choices:
            self._combo = QComboBox()
            for c in choices:
                self._combo.addItem(c)
            if default and default in choices:
                self._combo.setCurrentText(default)
            answer_row.addWidget(self._combo)
            self._get_answer = lambda: self._combo.currentText()
        elif answer_type == "confirm":
            self._yes_btn = QPushButton("是")
            self._no_btn = QPushButton("否")
            self._confirm_value = default or "是"
            self._yes_btn.clicked.connect(lambda: self._submit("是"))
            self._no_btn.clicked.connect(lambda: self._submit("否"))
            answer_row.addWidget(self._yes_btn)
            answer_row.addWidget(self._no_btn)
            self._get_answer = lambda: self._confirm_value
        else:
            self._input = QLineEdit()
            self._input.setPlaceholderText("请输入...")
            if default:
                self._input.setText(default)
            answer_row.addWidget(self._input)
            self._get_answer = lambda: self._input.text()

        if answer_type != "confirm":
            submit_btn = QPushButton("提交")
            submit_btn.setProperty("class", "success")
            submit_btn.clicked.connect(self._on_submit)
            answer_row.addWidget(submit_btn)

        layout.addLayout(answer_row)

    def _on_submit(self) -> None:
        answer = self._get_answer()
        if answer:
            self.answered.emit(self.question_id, answer)
            self.setEnabled(False)
            self.setProperty("class", "card")
            self.style().unpolish(self)
            self.style().polish(self)

    def _submit(self, value: str) -> None:
        self._confirm_value = value
        self.answered.emit(self.question_id, value)
        self.setEnabled(False)


class ChatPanel(QWidget):
    """多轮对话面板

    信号:
        message_sent(str): 用户发送消息
        question_answered(str, str): 用户回答了 AI 的问题 (question_id, answer)
    """

    message_sent = Signal(str)
    question_answered = Signal(str, str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # 标题
        title = QLabel("💬 AI 对话")
        title.setProperty("class", "subtitle")
        layout.addWidget(title)

        # 消息滚动区
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._container = QWidget()
        self._msg_layout = QVBoxLayout(self._container)
        self._msg_layout.setContentsMargins(4, 4, 4, 4)
        self._msg_layout.setSpacing(4)
        self._msg_layout.addStretch()

        self._scroll.setWidget(self._container)
        layout.addWidget(self._scroll, 1)

        # 输入区
        input_row = QHBoxLayout()
        self._input = QPlainTextEdit()
        self._input.setPlaceholderText("输入消息...")
        input_row.addWidget(self._input)

        send_btn = QPushButton("发送")
        send_btn.setProperty("class", "primary")
        send_btn.clicked.connect(self._on_send)
        input_row.addWidget(send_btn)

        layout.addLayout(input_row)

    def add_message(self, role: str, content: str) -> None:
        """添加一条消息"""
        ts = datetime.now().strftime("%H:%M:%S")
        bubble = MessageBubble(role, content, ts)
        # 插入到 stretch 之前
        self._msg_layout.insertWidget(
            self._msg_layout.count() - 1,
            bubble,
        )
        # 滚动到底部
        self._scroll.verticalScrollBar().setValue(
            self._scroll.verticalScrollBar().maximum()
        )

    def add_question(
        self,
        question_id: str,
        text: str,
        answer_type: str = "text",
        choices: list[str] | None = None,
        default: str = "",
        evidence: str = "",
    ) -> None:
        """添加一个 AI 提问卡片"""
        card = QuestionCard(
            question_id=question_id,
            text=text,
            answer_type=answer_type,
            choices=choices,
            default=default,
            evidence=evidence,
        )
        card.answered.connect(self.question_answered.emit)
        self._msg_layout.insertWidget(
            self._msg_layout.count() - 1,
            card,
        )
        self._scroll.verticalScrollBar().setValue(
            self._scroll.verticalScrollBar().maximum()
        )

    def clear(self) -> None:
        """清空所有消息"""
        while self._msg_layout.count() > 1:
            item = self._msg_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _on_send(self) -> None:
        text = self._input.toPlainText().strip()
        if text:
            self.add_message("user", text)
            self.message_sent.emit(text)
            self._input.clear()
