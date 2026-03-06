"""器件详情面板

选中器件后展示基本信息、Symbol 预览与编辑、AI 修改入口。
所有 UI 文案为中文。
"""

from __future__ import annotations

import os

from PySide6.QtCore import Qt, Signal, QThread
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from schemaforge.library.models import DeviceModel, SymbolDef
from schemaforge.gui.widgets.symbol_editor import SymbolEditorWidget


# ── AI 修改工作线程 ──


class _AiSymbolWorker(QThread):
    finished = Signal(object)  # SymbolDef | None
    error = Signal(str)

    def __init__(
        self,
        device: DeviceModel,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.device = device

    def run(self) -> None:
        try:
            from schemaforge.ingest.ai_analyzer import analyze_datasheet_text

            use_mock = os.environ.get("SCHEMAFORGE_MOCK", "0").lower() in (
                "1",
                "true",
                "yes",
            )

            text_parts = [
                f"器件型号: {self.device.part_number}",
                f"制造商: {self.device.manufacturer}",
                f"类别: {self.device.category}",
                f"封装: {self.device.package}",
                f"描述: {self.device.description}",
            ]
            if self.device.specs:
                text_parts.append("电气参数:")
                for k, v in self.device.specs.items():
                    text_parts.append(f"  {k}: {v}")
            if self.device.symbol and self.device.symbol.pins:
                text_parts.append("现有引脚:")
                for p in self.device.symbol.pins:
                    text_parts.append(
                        f"  {p.name} (#{p.pin_number}, {p.side.value}, {p.pin_type.value})"
                    )

            text = "\n".join(text_parts)
            hint = f"请重新分析并完善 {self.device.part_number} 的引脚定义"

            result = analyze_datasheet_text(text, hint=hint, use_mock=use_mock)
            if not result.success or result.data is None:
                self.error.emit("AI 分析失败")
                return

            from schemaforge.core.models import PinType
            from schemaforge.library.models import PinSide, SymbolPin

            type_map = {
                "input": PinType.INPUT,
                "output": PinType.OUTPUT,
                "power": PinType.POWER_IN,
                "passive": PinType.PASSIVE,
                "nc": PinType.NO_CONNECT,
                "bidirectional": PinType.BIDIRECTIONAL,
            }
            side_map = {
                "left": PinSide.LEFT,
                "right": PinSide.RIGHT,
                "top": PinSide.TOP,
                "bottom": PinSide.BOTTOM,
            }

            analysis = result.data
            pins: list[SymbolPin] = []
            for p in analysis.pins:
                if isinstance(p, dict):
                    pins.append(
                        SymbolPin(
                            name=p.get("name", ""),
                            pin_number=p.get("number", ""),
                            side=side_map.get(p.get("side", "left"), PinSide.LEFT),
                            pin_type=type_map.get(
                                p.get("type", "passive"), PinType.PASSIVE
                            ),
                            description=p.get("description", ""),
                        )
                    )

            if pins:
                self.finished.emit(SymbolDef(pins=pins))
            else:
                self.error.emit("AI 未返回有效引脚定义")

        except Exception as exc:
            self.error.emit(f"AI 调用异常: {exc}")


class DeviceDetailPanel(QWidget):
    symbol_saved = Signal(str, object)  # (part_number, SymbolDef)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._current_device: DeviceModel | None = None
        self._ai_worker: _AiSymbolWorker | None = None
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        title = QLabel("📋 器件详情")
        title.setFont(QFont("Microsoft YaHei", 13, QFont.Weight.Bold))
        layout.addWidget(title)

        self._empty_label = QLabel("← 请在左侧列表中选择一个器件")
        self._empty_label.setStyleSheet("color: #999; font-size: 12px;")
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._empty_label)

        # ── 详情容器（选中器件后显示） ──
        self._detail_container = QWidget()
        detail_layout = QVBoxLayout(self._detail_container)
        detail_layout.setContentsMargins(0, 0, 0, 0)

        # ── 基本信息 ──
        self._info_group = QGroupBox("基本信息")
        self._info_layout = QFormLayout(self._info_group)
        self._info_layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self._info_labels: dict[str, QLabel] = {}
        for key, label_text in [
            ("part_number", "料号:"),
            ("manufacturer", "制造商:"),
            ("category", "类别:"),
            ("package", "封装:"),
            ("description", "描述:"),
            ("source", "来源:"),
            ("confidence", "置信度:"),
        ]:
            val = QLabel("")
            val.setWordWrap(True)
            self._info_labels[key] = val
            self._info_layout.addRow(label_text, val)

        detail_layout.addWidget(self._info_group)

        # ── Symbol 编辑器 ──
        self._symbol_editor = SymbolEditorWidget()
        detail_layout.addWidget(self._symbol_editor, 1)

        # ── 操作按钮 ──
        btn_row = QHBoxLayout()

        self._save_btn = QPushButton("💾 保存 Symbol")
        self._save_btn.setFont(QFont("Microsoft YaHei", 11, QFont.Weight.Bold))
        self._save_btn.setMinimumHeight(36)
        self._save_btn.setStyleSheet(
            "QPushButton { background: #198754; color: white; "
            "border-radius: 6px; padding: 6px 16px; }"
            "QPushButton:hover { background: #157347; }"
        )
        self._save_btn.clicked.connect(self._on_save)
        btn_row.addWidget(self._save_btn)

        self._ai_btn = QPushButton("🤖 AI 重新分析引脚")
        self._ai_btn.setFont(QFont("Microsoft YaHei", 11))
        self._ai_btn.setMinimumHeight(36)
        self._ai_btn.setStyleSheet(
            "QPushButton { background: #0d6efd; color: white; "
            "border-radius: 6px; padding: 6px 16px; }"
            "QPushButton:hover { background: #0b5ed7; }"
            "QPushButton:disabled { background: #6c757d; }"
        )
        self._ai_btn.clicked.connect(self._on_ai_modify)
        btn_row.addWidget(self._ai_btn)

        detail_layout.addLayout(btn_row)

        self._detail_container.hide()
        layout.addWidget(self._detail_container, 1)

    # ── 公开接口 ──

    def load_device(self, device: DeviceModel) -> None:
        self._current_device = device
        self._empty_label.hide()
        self._detail_container.show()

        self._info_labels["part_number"].setText(device.part_number)
        self._info_labels["manufacturer"].setText(device.manufacturer or "—")
        self._info_labels["category"].setText(device.category or "—")
        self._info_labels["package"].setText(device.package or "—")
        self._info_labels["description"].setText(device.description or "—")
        self._info_labels["source"].setText(device.source or "—")
        self._info_labels["confidence"].setText(f"{device.confidence:.0%}")

        if device.symbol and device.symbol.pins:
            self._symbol_editor.load_symbol(device.symbol, device.part_number)
        else:
            self._symbol_editor.load_symbol(SymbolDef(pins=[]), device.part_number)

    def clear(self) -> None:
        self._current_device = None
        self._detail_container.hide()
        self._empty_label.show()

    # ── 保存 ──

    def _on_save(self) -> None:
        if self._current_device is None:
            return
        symbol = self._symbol_editor.get_symbol()
        self.symbol_saved.emit(self._current_device.part_number, symbol)

    # ── AI 修改 ──

    def _on_ai_modify(self) -> None:
        if self._current_device is None:
            return

        self._ai_btn.setEnabled(False)
        self._ai_btn.setText("🤖 AI 分析中...")

        self._ai_worker = _AiSymbolWorker(self._current_device)
        self._ai_worker.finished.connect(self._on_ai_done)
        self._ai_worker.error.connect(self._on_ai_error)
        self._ai_worker.start()

    def _on_ai_done(self, symbol: object) -> None:
        self._ai_btn.setEnabled(True)
        self._ai_btn.setText("🤖 AI 重新分析引脚")

        if not isinstance(symbol, SymbolDef) or self._current_device is None:
            return

        self._symbol_editor.load_symbol(symbol, self._current_device.part_number)
        QMessageBox.information(
            self,
            "AI 分析完成",
            f"AI 返回了 {len(symbol.pins)} 个引脚定义。\n"
            "请检查后点击「保存 Symbol」确认修改。",
        )

    def _on_ai_error(self, msg: str) -> None:
        self._ai_btn.setEnabled(True)
        self._ai_btn.setText("🤖 AI 重新分析引脚")
        QMessageBox.warning(self, "AI 分析失败", msg)
