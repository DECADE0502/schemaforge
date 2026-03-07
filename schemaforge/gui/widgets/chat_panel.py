"""AI 聊天面板控件

提供消息输入框和消息气泡展示区域。支持三种角色：用户、AI、系统。

用法::

    panel = ChatPanel()
    panel.message_sent.connect(handle_send)
    panel.add_message("ai", "你好，请描述你的电路需求。")
"""

from __future__ import annotations

import base64

from PySide6.QtCore import Qt, QBuffer, QIODevice, QTimer, Signal
from PySide6.QtGui import QKeySequence, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

# ============================================================
# 样式常量
# ============================================================

_USER_BG = "#094771"
_AI_BG = "#2d2d2d"
_SYSTEM_COLOR = "#888888"
_TEXT_COLOR = "#cccccc"
_BUBBLE_RADIUS = 8
_BUBBLE_PADDING = "10px 14px"
_MAX_BUBBLE_WIDTH = 420
_THUMBNAIL_MAX = 200  # px


# ============================================================
# 图片感知输入框
# ============================================================


class ImageAwareTextEdit(QTextEdit):
    """QTextEdit that detects image paste from clipboard."""

    image_pasted = Signal(str)  # base64 PNG

    def keyPressEvent(self, event) -> None:  # type: ignore[override]
        if event.matches(QKeySequence.StandardKey.Paste):
            clipboard = QApplication.clipboard()
            mime = clipboard.mimeData()
            if mime and mime.hasImage():
                image = clipboard.image()
                if not image.isNull():
                    buf = QBuffer()
                    buf.open(QIODevice.OpenModeFlag.WriteOnly)
                    image.save(buf, "PNG")
                    b64 = base64.b64encode(buf.data().data()).decode("ascii")
                    buf.close()
                    self.image_pasted.emit(b64)
                    return
        super().keyPressEvent(event)


# ============================================================
# 消息气泡
# ============================================================


class _MessageBubble(QFrame):
    """单条消息气泡。"""

    def __init__(
        self,
        role: str,
        text: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._role = role
        self._setup_ui(text)

    def _setup_ui(self, text: str) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)

        label = QLabel(text)
        label.setWordWrap(True)
        label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse,
        )
        label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        label.setMaximumWidth(_MAX_BUBBLE_WIDTH)

        if self._role == "user":
            self.setStyleSheet(
                f"_MessageBubble {{"
                f"  background-color: {_USER_BG};"
                f"  border-radius: {_BUBBLE_RADIUS}px;"
                f"  padding: {_BUBBLE_PADDING};"
                f"}}"
            )
            label.setStyleSheet(f"color: {_TEXT_COLOR}; background: transparent;")
            label.setAlignment(Qt.AlignmentFlag.AlignLeft)

        elif self._role == "ai":
            self.setStyleSheet(
                f"_MessageBubble {{"
                f"  background-color: {_AI_BG};"
                f"  border-radius: {_BUBBLE_RADIUS}px;"
                f"  padding: {_BUBBLE_PADDING};"
                f"}}"
            )
            label.setStyleSheet(f"color: {_TEXT_COLOR}; background: transparent;")
            label.setAlignment(Qt.AlignmentFlag.AlignLeft)

        else:  # system
            self.setStyleSheet(
                "_MessageBubble {"
                "  background-color: transparent;"
                "  padding: 4px 8px;"
                "}"
            )
            label.setStyleSheet(
                f"color: {_SYSTEM_COLOR};"
                "  font-style: italic;"
                "  background: transparent;"
            )
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout.addWidget(label)


class _ImageBubble(QFrame):
    """显示图片缩略图的消息气泡。"""

    def __init__(
        self,
        role: str,
        base64_png: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._role = role
        self._setup_ui(base64_png)

    def _setup_ui(self, base64_png: str) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)

        img_label = QLabel()
        img_label.setAlignment(Qt.AlignmentFlag.AlignLeft)

        try:
            raw = base64.b64decode(base64_png)
            pixmap = QPixmap()
            pixmap.loadFromData(raw, "PNG")
            if not pixmap.isNull():
                scaled = pixmap.scaled(
                    _THUMBNAIL_MAX,
                    _THUMBNAIL_MAX,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                img_label.setPixmap(scaled)
            else:
                img_label.setText("[图片加载失败]")
        except Exception:
            img_label.setText("[图片加载失败]")

        if self._role == "user":
            self.setStyleSheet(
                f"_ImageBubble {{"
                f"  background-color: {_USER_BG};"
                f"  border-radius: {_BUBBLE_RADIUS}px;"
                f"  padding: {_BUBBLE_PADDING};"
                f"}}"
            )
        else:
            self.setStyleSheet(
                f"_ImageBubble {{"
                f"  background-color: {_AI_BG};"
                f"  border-radius: {_BUBBLE_RADIUS}px;"
                f"  padding: {_BUBBLE_PADDING};"
                f"}}"
            )

        layout.addWidget(img_label)


# ============================================================
# ChatPanel
# ============================================================


class ChatPanel(QWidget):
    """AI 聊天面板。

    Signals:
        message_sent(str): 用户发送消息时发出。
        image_pasted(str): 用户粘贴图片时发出 base64 PNG 字符串。
    """

    message_sent = Signal(str)
    image_pasted = Signal(str)  # base64 PNG

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._setup_ui()

    # ----------------------------------------------------------
    # UI 构建
    # ----------------------------------------------------------

    def _setup_ui(self) -> None:
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # --- 消息展示区 ---
        self._scroll_area = QScrollArea()
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff,
        )
        self._scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll_area.setStyleSheet("QScrollArea { border: none; }")

        self._messages_container = QWidget()
        self._messages_layout = QVBoxLayout(self._messages_container)
        self._messages_layout.setContentsMargins(8, 8, 8, 8)
        self._messages_layout.setSpacing(6)
        self._messages_layout.addStretch()  # 底部弹性空间，消息从上往下排列

        self._scroll_area.setWidget(self._messages_container)
        root_layout.addWidget(self._scroll_area, 1)

        # --- 输入区域 ---
        input_frame = QFrame()
        input_frame.setStyleSheet(
            "QFrame { background-color: #252526; border-top: 1px solid #3c3c3c; }"
        )
        input_layout = QHBoxLayout(input_frame)
        input_layout.setContentsMargins(8, 6, 8, 6)
        input_layout.setSpacing(6)

        self._input_field = QLineEdit()
        self._input_field.setPlaceholderText("输入消息... (Ctrl+V 可粘贴图片)")
        self._input_field.returnPressed.connect(self._on_send)
        input_layout.addWidget(self._input_field, 1)

        self._send_button = QPushButton("发送")
        self._send_button.setFixedWidth(60)
        self._send_button.clicked.connect(self._on_send)
        input_layout.addWidget(self._send_button)

        # 隐藏的 ImageAwareTextEdit，仅用于捕获 Ctrl+V 图片
        self._image_input = ImageAwareTextEdit()
        self._image_input.setMaximumHeight(0)
        self._image_input.setVisible(False)
        self._image_input.image_pasted.connect(self._on_image_pasted)
        input_layout.addWidget(self._image_input)

        root_layout.addWidget(input_frame)

        # 安装事件过滤器以捕获 QLineEdit 的 Ctrl+V
        self._input_field.installEventFilter(self)

    # ----------------------------------------------------------
    # 事件过滤
    # ----------------------------------------------------------

    def eventFilter(self, watched, event) -> bool:  # type: ignore[override]
        from PySide6.QtCore import QEvent

        if watched is self._input_field and event.type() == QEvent.Type.KeyPress:
            key_event = event  # type: QKeyEvent
            if key_event.matches(QKeySequence.StandardKey.Paste):
                clipboard = QApplication.clipboard()
                mime = clipboard.mimeData()
                if mime and mime.hasImage():
                    image = clipboard.image()
                    if not image.isNull():
                        buf = QBuffer()
                        buf.open(QIODevice.OpenModeFlag.WriteOnly)
                        image.save(buf, "PNG")
                        b64 = base64.b64encode(buf.data().data()).decode("ascii")
                        buf.close()
                        self._on_image_pasted(b64)
                        return True
        return super().eventFilter(watched, event)

    # ----------------------------------------------------------
    # 公开 API
    # ----------------------------------------------------------

    def add_message(self, role: str, text: str) -> None:
        """添加一条消息。

        Args:
            role: 角色，"user" / "ai" / "system"。
            text: 消息文本。
        """
        bubble = _MessageBubble(role, text, self._messages_container)
        self._insert_bubble(role, bubble)

    def add_image_message(self, role: str, base64_png: str) -> None:
        """在聊天历史中展示图片缩略图。

        Args:
            role: 角色，"user" / "ai" / "system"。
            base64_png: base64 编码的 PNG 字符串。
        """
        bubble = _ImageBubble(role, base64_png, self._messages_container)
        self._insert_bubble(role, bubble)

    def clear(self) -> None:
        """移除所有消息。"""
        # 删除 stretch 之前的所有项目
        while self._messages_layout.count() > 1:
            item = self._messages_layout.takeAt(0)
            if item is not None:
                widget = item.widget()
                if widget is not None:
                    widget.deleteLater()
                else:
                    # 嵌套 layout
                    sub_layout = item.layout()
                    if sub_layout is not None:
                        self._clear_layout(sub_layout)

    # ----------------------------------------------------------
    # 内部方法
    # ----------------------------------------------------------

    def _insert_bubble(self, role: str, bubble: QFrame) -> None:
        """将气泡插入消息区，对齐方式按角色决定。"""
        wrapper = QHBoxLayout()
        wrapper.setContentsMargins(0, 0, 0, 0)

        if role == "user":
            wrapper.addStretch()
            wrapper.addWidget(bubble)
        elif role == "ai":
            wrapper.addWidget(bubble)
            wrapper.addStretch()
        else:  # system
            wrapper.addStretch()
            wrapper.addWidget(bubble)
            wrapper.addStretch()

        # 插入到弹性空间之前（stretch 是最后一项）
        count = self._messages_layout.count()
        self._messages_layout.insertLayout(count - 1, wrapper)

        # 自动滚动到底部
        self._scroll_to_bottom()

    def _on_send(self) -> None:
        """处理发送动作。"""
        text = self._input_field.text().strip()
        if not text:
            return
        self._input_field.clear()
        self.add_message("user", text)
        self.message_sent.emit(text)

    def _on_image_pasted(self, b64: str) -> None:
        """处理图片粘贴动作。"""
        self.add_image_message("user", b64)
        self.add_message("system", "[图片已粘贴]")
        self.image_pasted.emit(b64)

    def _scroll_to_bottom(self) -> None:
        """滚动到消息区域底部。"""
        QTimer.singleShot(10, self._do_scroll_bottom)

    def _do_scroll_bottom(self) -> None:
        vbar = self._scroll_area.verticalScrollBar()
        if vbar is not None:
            vbar.setValue(vbar.maximum())

    @staticmethod
    def _clear_layout(layout: object) -> None:
        """递归清理布局内所有控件。"""
        from PySide6.QtWidgets import QLayout

        if not isinstance(layout, QLayout):
            return
        while layout.count():
            item = layout.takeAt(0)
            if item is None:
                continue
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
            else:
                sub = item.layout()
                if sub is not None:
                    ChatPanel._clear_layout(sub)
