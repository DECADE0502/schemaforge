"""符号编辑器组件

提供引脚表格编辑与符号实时预览功能，用于在器件库中编辑 SymbolDef。
所有 UI 文案为中文。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from schemaforge.library.models import PinSide, SymbolDef, SymbolPin
from schemaforge.core.models import PinType
from schemaforge.schematic.renderer import TopologyRenderer


# ── 方位映射 ──

_SIDE_DISPLAY = {
    PinSide.LEFT: "左",
    PinSide.RIGHT: "右",
    PinSide.TOP: "上",
    PinSide.BOTTOM: "下",
}
_DISPLAY_SIDE = {v: k for k, v in _SIDE_DISPLAY.items()}

_SIDE_LABELS = ["左", "右", "上", "下"]

# ── 类型映射 ──

_TYPE_DISPLAY: dict[PinType, str] = {
    PinType.INPUT: "输入",
    PinType.OUTPUT: "输出",
    PinType.POWER_IN: "电源",
    PinType.PASSIVE: "无源",
    PinType.NO_CONNECT: "空脚",
}
_DISPLAY_TYPE = {v: k for k, v in _TYPE_DISPLAY.items()}

_TYPE_LABELS = ["输入", "输出", "电源", "无源", "空脚"]


def _pin_type_to_display(pt: PinType) -> str:
    return _TYPE_DISPLAY.get(pt, "无源")


def _display_to_pin_type(label: str) -> PinType:
    return _DISPLAY_TYPE.get(label, PinType.PASSIVE)


def _pin_side_to_display(ps: PinSide) -> str:
    return _SIDE_DISPLAY.get(ps, "左")


def _display_to_pin_side(label: str) -> PinSide:
    return _DISPLAY_SIDE.get(label, PinSide.LEFT)


# ── 列索引常量 ──

_COL_NAME = 0
_COL_NUM = 1
_COL_SIDE = 2
_COL_TYPE = 3
_COL_DESC = 4


class _AutoFitView(QGraphicsView):
    def resizeEvent(self, event):
        super().resizeEvent(event)
        items = [i for i in self.scene().items() if isinstance(i, QGraphicsPixmapItem)]
        if items:
            self.fitInView(items[0], Qt.AspectRatioMode.KeepAspectRatio)


class SymbolEditorWidget(QWidget):
    symbol_changed = Signal(object)  # SymbolDef

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._label: str = ""
        self._blocking: bool = False
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(300)
        self._preview_timer.timeout.connect(self._refresh_preview)
        self._setup_ui()

    # ── 界面构建 ──

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)

        title = QLabel("符号引脚编辑器")
        title.setProperty("class", "title")
        root.addWidget(title)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setChildrenCollapsible(False)

        # ── 上半：引脚表格 ──
        top_widget = QWidget()
        top_layout = QVBoxLayout(top_widget)
        top_layout.setContentsMargins(0, 0, 0, 0)

        pin_group = QGroupBox("引脚列表")
        pin_layout = QVBoxLayout(pin_group)

        btn_bar = QHBoxLayout()
        add_btn = QPushButton("添加引脚")
        add_btn.clicked.connect(self._add_pin_row)
        btn_bar.addWidget(add_btn)

        del_btn = QPushButton("删除选中")
        del_btn.clicked.connect(self._delete_selected_rows)
        btn_bar.addWidget(del_btn)

        btn_bar.addStretch()
        pin_layout.addLayout(btn_bar)

        self.pin_table = QTableWidget(0, 5)
        self.pin_table.setHorizontalHeaderLabels(
            ["名称", "编号", "方位", "类型", "描述"]
        )
        self.pin_table.setAlternatingRowColors(True)
        self.pin_table.setShowGrid(False)
        header = self.pin_table.horizontalHeader()
        header.setSectionResizeMode(_COL_NAME, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(_COL_NUM, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(_COL_SIDE, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(_COL_TYPE, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(_COL_DESC, QHeaderView.ResizeMode.Stretch)
        self.pin_table.setColumnWidth(_COL_NUM, 60)
        self.pin_table.setColumnWidth(_COL_SIDE, 80)
        self.pin_table.setColumnWidth(_COL_TYPE, 80)
        self.pin_table.cellChanged.connect(self._on_cell_changed)
        pin_layout.addWidget(self.pin_table)

        top_layout.addWidget(pin_group)
        splitter.addWidget(top_widget)

        # ── 下半：预览区 ──
        preview_widget = QWidget()
        preview_layout = QVBoxLayout(preview_widget)
        preview_layout.setContentsMargins(0, 0, 0, 0)

        preview_group = QGroupBox("符号预览")
        preview_inner = QVBoxLayout(preview_group)

        self._preview_scene = QGraphicsScene(self)
        self._preview_view = _AutoFitView(self._preview_scene)
        self._preview_view.setRenderHints(
            QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform
        )
        self._preview_view.setBackgroundBrush(QColor("#1a1a2e"))
        self._preview_view.setProperty("class", "symbol-preview")
        self._preview_view.setMinimumHeight(160)
        self._preview_item: QGraphicsPixmapItem | None = None
        preview_inner.addWidget(self._preview_view)

        preview_layout.addWidget(preview_group)
        splitter.addWidget(preview_widget)

        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 1)

        root.addWidget(splitter, 1)

    # ── 公开接口 ──

    def load_symbol(self, symbol: SymbolDef, label: str) -> None:
        self._label = label
        self._blocking = True
        self.pin_table.setRowCount(0)
        for pin in symbol.pins:
            self._insert_pin_row(
                name=pin.name,
                pin_number=pin.pin_number,
                side_label=_pin_side_to_display(pin.side),
                type_label=_pin_type_to_display(pin.pin_type),
                description=pin.description,
            )
        self._blocking = False
        self._schedule_preview()

    def get_symbol(self) -> SymbolDef:
        pins: list[SymbolPin] = []
        for row in range(self.pin_table.rowCount()):
            name_item = self.pin_table.item(row, _COL_NAME)
            num_item = self.pin_table.item(row, _COL_NUM)
            side_widget = self.pin_table.cellWidget(row, _COL_SIDE)
            type_widget = self.pin_table.cellWidget(row, _COL_TYPE)
            desc_item = self.pin_table.item(row, _COL_DESC)

            name = name_item.text().strip() if name_item else ""
            pin_number = num_item.text().strip() if num_item else ""
            side = (
                _display_to_pin_side(side_widget.currentText())
                if isinstance(side_widget, QComboBox)
                else PinSide.LEFT
            )
            pin_type = (
                _display_to_pin_type(type_widget.currentText())
                if isinstance(type_widget, QComboBox)
                else PinType.PASSIVE
            )
            description = desc_item.text().strip() if desc_item else ""

            pins.append(
                SymbolPin(
                    name=name,
                    pin_number=pin_number,
                    side=side,
                    pin_type=pin_type,
                    description=description,
                )
            )
        return SymbolDef(pins=pins)

    # ── 内部操作 ──

    def _insert_pin_row(
        self,
        name: str = "",
        pin_number: str = "",
        side_label: str = "左",
        type_label: str = "无源",
        description: str = "",
    ) -> None:
        row = self.pin_table.rowCount()
        self.pin_table.insertRow(row)

        self.pin_table.setItem(row, _COL_NAME, QTableWidgetItem(name))
        self.pin_table.setItem(
            row, _COL_NUM, QTableWidgetItem(pin_number or str(row + 1))
        )

        side_combo = QComboBox()
        side_combo.addItems(_SIDE_LABELS)
        idx = side_combo.findText(side_label)
        if idx >= 0:
            side_combo.setCurrentIndex(idx)
        side_combo.currentIndexChanged.connect(self._on_combo_changed)
        self.pin_table.setCellWidget(row, _COL_SIDE, side_combo)

        type_combo = QComboBox()
        type_combo.addItems(_TYPE_LABELS)
        idx = type_combo.findText(type_label)
        if idx >= 0:
            type_combo.setCurrentIndex(idx)
        type_combo.currentIndexChanged.connect(self._on_combo_changed)
        self.pin_table.setCellWidget(row, _COL_TYPE, type_combo)

        self.pin_table.setItem(row, _COL_DESC, QTableWidgetItem(description))

    def _add_pin_row(self) -> None:
        self._blocking = True
        self._insert_pin_row()
        self._blocking = False
        self._schedule_preview()

    def _delete_selected_rows(self) -> None:
        selected = sorted(
            {idx.row() for idx in self.pin_table.selectedIndexes()},
            reverse=True,
        )
        if not selected:
            return
        self._blocking = True
        for row in selected:
            self.pin_table.removeRow(row)
        self._blocking = False
        self._schedule_preview()

    # ── 信号响应 ──

    def _on_cell_changed(self, _row: int, _col: int) -> None:
        if not self._blocking:
            self._schedule_preview()

    def _on_combo_changed(self, _index: int) -> None:
        if not self._blocking:
            self._schedule_preview()

    def _schedule_preview(self) -> None:
        self._preview_timer.start()

    # ── 预览渲染 ──

    def _refresh_preview(self) -> None:
        symbol = self.get_symbol()
        if not symbol.pins:
            self._preview_scene.clear()
            self._preview_item = None
            self._preview_scene.addText("（无引脚，无法预览）").setDefaultTextColor(
                QColor("#858585")
            )
            self.symbol_changed.emit(symbol)
            return

        try:
            png_bytes = TopologyRenderer.render_symbol_preview(
                symbol, self._label, dpi=120
            )
            pixmap = QPixmap()
            pixmap.loadFromData(png_bytes)
            self._preview_scene.clear()
            self._preview_item = self._preview_scene.addPixmap(pixmap)
            self._preview_scene.setSceneRect(self._preview_item.boundingRect())
            self._preview_view.fitInView(
                self._preview_item, Qt.AspectRatioMode.KeepAspectRatio
            )
        except Exception:
            self._preview_scene.clear()
            self._preview_item = None
            self._preview_scene.addText("渲染失败").setDefaultTextColor(
                QColor("#f44747")
            )

        self.symbol_changed.emit(symbol)
