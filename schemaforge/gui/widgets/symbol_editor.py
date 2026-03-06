"""器件符号预览 / 编辑器

SymbolEditorWidget 提供两种模式:
  - 预览模式: 只读显示器件 IC 符号 (矩形 + 引脚线 + 标签)，可缩放平移
  - 编辑模式: 可拖拽引脚到四个边 (left/right/top/bottom)，修改引脚名/类型/编号，
             调整 slot 顺序，保存回 SymbolDef

采用 QGraphicsView 原生绘制，不依赖 schemdraw 渲染，实现实时交互。
"""

from __future__ import annotations

import logging
import math
from copy import deepcopy
from PySide6.QtCore import QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QPainter,
    QPen,
    QPixmap,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGraphicsEllipseItem,
    QGraphicsItem,
    QGraphicsLineItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from schemaforge.library.models import PinSide, SymbolDef, SymbolPin

logger = logging.getLogger(__name__)

# ── 常量 ────────────────────────────────────────────────
_IC_COLOR = QColor("#3c3c3c")
_IC_BORDER = QColor("#007acc")
_PIN_LINE_COLOR = QColor("#cccccc")
_PIN_DOT_COLOR = QColor("#007acc")
_PIN_TEXT_COLOR = QColor("#cccccc")
_SIDE_HIGHLIGHT = QColor(0, 122, 204, 40)  # 半透明蓝色

_UNIT = 40.0  # 1 schemdraw unit = 40px
_PIN_LEAD = 30.0  # 引脚引线长度 (px)
_PIN_DOT_R = 5.0  # 引脚端点圆点半径
_FONT_SIZE = 11
_LABEL_OFFSET = 6.0  # 文本偏移

# 边的法线方向 (dx, dy) — 引线从 IC 向外延伸的方向
_SIDE_NORMALS: dict[PinSide, tuple[float, float]] = {
    PinSide.LEFT: (-1, 0),
    PinSide.RIGHT: (1, 0),
    PinSide.TOP: (0, -1),
    PinSide.BOTTOM: (0, 1),
}

_SIDE_LABELS: dict[PinSide, str] = {
    PinSide.LEFT: "左",
    PinSide.RIGHT: "右",
    PinSide.TOP: "上",
    PinSide.BOTTOM: "下",
}

_PIN_TYPE_NAMES: dict[str, str] = {
    "power_in": "电源输入",
    "power_out": "电源输出",
    "ground": "地",
    "input": "输入",
    "output": "输出",
    "passive": "无源",
    "bidirectional": "双向",
    "no_connect": "悬空",
}


# ============================================================
# 可拖拽引脚图形项
# ============================================================


class PinItem(QGraphicsEllipseItem):
    """可拖拽的引脚端点。

    预览模式下不可交互；编辑模式下可拖拽，拖拽释放后根据最近的边
    自动吸附到对应 side 并更新 slot。
    """

    def __init__(
        self,
        pin: SymbolPin,
        center: QPointF,
        ic_rect: QRectF,
        editable: bool = False,
    ) -> None:
        r = _PIN_DOT_R
        super().__init__(-r, -r, r * 2, r * 2)
        self.pin = pin
        self._ic_rect = ic_rect
        self.setPos(center)
        self.setBrush(QBrush(_PIN_DOT_COLOR))
        self.setPen(QPen(Qt.PenStyle.NoPen))
        self.setZValue(10)

        if editable:
            self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
            self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, True)
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        else:
            self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)

        # tooltip
        tip = f"{pin.name}"
        if pin.pin_number:
            tip += f" (#{pin.pin_number})"
        tip += f"\n{_SIDE_LABELS.get(pin.side, '?')}侧"
        tip += f"\n类型: {_PIN_TYPE_NAMES.get(pin.pin_type.value, pin.pin_type.value)}"
        self.setToolTip(tip)

    def mouseReleaseEvent(self, event):  # type: ignore[override]
        """拖拽释放时，吸附到最近的 IC 边。"""
        super().mouseReleaseEvent(event)
        pos = self.scenePos()
        self.pin.side = self._nearest_side(pos)
        # 让 scene 知道需要重绘
        scene = self.scene()
        if scene is not None:
            for view in scene.views():
                if isinstance(view, SymbolCanvas):
                    view.request_rebuild()

    def _nearest_side(self, pos: QPointF) -> PinSide:
        """计算距离 IC 矩形四条边最近的一侧。"""
        r = self._ic_rect
        distances = {
            PinSide.LEFT: abs(pos.x() - r.left()),
            PinSide.RIGHT: abs(pos.x() - r.right()),
            PinSide.TOP: abs(pos.y() - r.top()),
            PinSide.BOTTOM: abs(pos.y() - r.bottom()),
        }
        return min(distances, key=distances.get)  # type: ignore[arg-type]

    def mouseDoubleClickEvent(self, event):  # type: ignore[override]
        """双击弹出引脚编辑对话框。"""
        super().mouseDoubleClickEvent(event)
        scene = self.scene()
        if scene is None:
            return
        for view in scene.views():
            if isinstance(view, SymbolCanvas):
                view.edit_pin_dialog(self.pin)
                break


# ============================================================
# 符号画布 — QGraphicsView
# ============================================================


class SymbolCanvas(QGraphicsView):
    """器件符号画布，支持缩放/平移，预览或编辑模式。"""

    symbol_changed = Signal()  # 编辑模式下引脚变动后触发
    _rebuild_pending: bool = False

    ZOOM_STEP = 0.15
    ZOOM_MIN = 0.2
    ZOOM_MAX = 10.0

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHints(
            QPainter.RenderHint.Antialiasing
            | QPainter.RenderHint.SmoothPixmapTransform
        )
        self.setTransformationAnchor(
            QGraphicsView.ViewportAnchor.AnchorUnderMouse
        )
        self.setResizeAnchor(
            QGraphicsView.ViewportAnchor.AnchorUnderMouse
        )
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setViewportUpdateMode(
            QGraphicsView.ViewportUpdateMode.FullViewportUpdate
        )
        self.setFrameShape(QGraphicsView.Shape.NoFrame)

        # 棋盘背景
        tile = QPixmap(64, 64)
        tile.fill(QColor("#2d2d2d"))
        p = QPainter(tile)
        dk = QColor("#262626")
        p.fillRect(0, 0, 32, 32, dk)
        p.fillRect(32, 32, 32, 32, dk)
        p.end()
        self.setBackgroundBrush(QBrush(tile))

        self._symbol: SymbolDef | None = None
        self._label: str = ""
        self._editable: bool = False
        self._zoom: float = 1.0
        self._pin_items: list[PinItem] = []

    # ── 公开 API ──────────────────────────────────────

    def set_symbol(
        self,
        symbol: SymbolDef | None,
        label: str = "",
        editable: bool = False,
    ) -> None:
        """加载 / 切换符号。"""
        self._symbol = deepcopy(symbol) if symbol else None
        self._label = label
        self._editable = editable
        self._rebuild()
        self.fit_in_view()

    def get_symbol(self) -> SymbolDef | None:
        """返回当前（可能被编辑过的）SymbolDef。"""
        return deepcopy(self._symbol) if self._symbol else None

    def fit_in_view(self) -> None:
        sr = self._scene.sceneRect()
        if not sr.isEmpty():
            self.fitInView(sr, Qt.AspectRatioMode.KeepAspectRatio)
            self._zoom = 1.0

    def request_rebuild(self) -> None:
        """标记需要重绘（用于引脚拖拽后延迟重建）。"""
        if not self._rebuild_pending:
            self._rebuild_pending = True
            from PySide6.QtCore import QTimer

            QTimer.singleShot(0, self._do_deferred_rebuild)

    def _do_deferred_rebuild(self) -> None:
        self._rebuild_pending = False
        self._rebuild()
        self.symbol_changed.emit()

    # ── 绘制 ──────────────────────────────────────────

    def _rebuild(self) -> None:
        """清空场景并重新绘制 IC 矩形 + 引脚。"""
        self._scene.clear()
        self._pin_items.clear()
        sym = self._symbol
        if sym is None:
            return

        # 计算 IC 矩形尺寸
        pins_per_side = self._count_pins_per_side(sym)
        max_lr = max(pins_per_side.get(PinSide.LEFT, 0),
                     pins_per_side.get(PinSide.RIGHT, 0), 1)
        max_tb = max(pins_per_side.get(PinSide.TOP, 0),
                     pins_per_side.get(PinSide.BOTTOM, 0), 1)

        if sym.size:
            w = sym.size[0] * _UNIT
            h = sym.size[1] * _UNIT
        else:
            w = max(max_tb * sym.pin_spacing * _UNIT + 2 * sym.edge_pad_w * _UNIT,
                    4 * _UNIT)
            h = max(max_lr * sym.pin_spacing * _UNIT + 2 * sym.edge_pad_h * _UNIT,
                    3 * _UNIT)

        ic_rect = QRectF(0, 0, w, h)

        # IC 矩形
        rect_item = QGraphicsRectItem(ic_rect)
        rect_item.setBrush(QBrush(_IC_COLOR))
        rect_item.setPen(QPen(_IC_BORDER, 2))
        rect_item.setZValue(0)
        self._scene.addItem(rect_item)

        # IC 标签
        if self._label:
            label_item = QGraphicsSimpleTextItem(self._label)
            label_item.setFont(QFont("Segoe UI", _FONT_SIZE, QFont.Weight.Bold))
            label_item.setBrush(QBrush(QColor("#ffffff")))
            lb = label_item.boundingRect()
            label_item.setPos(
                ic_rect.center().x() - lb.width() / 2,
                ic_rect.center().y() - lb.height() / 2,
            )
            label_item.setZValue(5)
            self._scene.addItem(label_item)

        # 编辑模式：绘制四侧半透明高亮区域
        if self._editable:
            self._draw_side_zones(ic_rect)

        # 自动分配 slot
        self._assign_slots(sym)

        # 绘制引脚
        for pin in sym.pins:
            self._draw_pin(pin, ic_rect)

        # 场景边距 — 足够容纳引脚引线 + 标签文字
        pad = _PIN_LEAD + 60
        self._scene.setSceneRect(ic_rect.adjusted(-pad, -pad, pad, pad))

    def _draw_side_zones(self, ic_rect: QRectF) -> None:
        """编辑模式: 在四侧画半透明高亮条，提示拖放目标。"""
        zone_w = 20.0
        zones = {
            PinSide.LEFT: QRectF(
                ic_rect.left() - zone_w, ic_rect.top(),
                zone_w, ic_rect.height()),
            PinSide.RIGHT: QRectF(
                ic_rect.right(), ic_rect.top(),
                zone_w, ic_rect.height()),
            PinSide.TOP: QRectF(
                ic_rect.left(), ic_rect.top() - zone_w,
                ic_rect.width(), zone_w),
            PinSide.BOTTOM: QRectF(
                ic_rect.left(), ic_rect.bottom(),
                ic_rect.width(), zone_w),
        }
        for side, r in zones.items():
            z = QGraphicsRectItem(r)
            z.setBrush(QBrush(_SIDE_HIGHLIGHT))
            z.setPen(QPen(Qt.PenStyle.NoPen))
            z.setZValue(1)
            z.setToolTip(f"拖放引脚到{_SIDE_LABELS[side]}侧")
            self._scene.addItem(z)

    def _draw_pin(self, pin: SymbolPin, ic_rect: QRectF) -> None:
        """绘制单个引脚: 引线 + 端点 + 名称标签。"""
        edge_pt = self._pin_edge_point(pin, ic_rect)
        nx, ny = _SIDE_NORMALS[pin.side]
        tip_pt = QPointF(
            edge_pt.x() + nx * _PIN_LEAD,
            edge_pt.y() + ny * _PIN_LEAD,
        )

        # 引线
        line = QGraphicsLineItem(edge_pt.x(), edge_pt.y(), tip_pt.x(), tip_pt.y())
        line.setPen(QPen(_PIN_LINE_COLOR, 1.5))
        line.setZValue(2)
        self._scene.addItem(line)

        # 端点（可拖拽）
        dot = PinItem(pin, tip_pt, ic_rect, editable=self._editable)
        self._scene.addItem(dot)
        self._pin_items.append(dot)

        # 名称文本（在引线外侧）
        name_item = QGraphicsSimpleTextItem(pin.name)
        name_item.setFont(QFont("Segoe UI", _FONT_SIZE - 1))
        name_item.setBrush(QBrush(_PIN_TEXT_COLOR))
        nb = name_item.boundingRect()

        if pin.side == PinSide.LEFT:
            name_item.setPos(tip_pt.x() - nb.width() - _LABEL_OFFSET,
                             tip_pt.y() - nb.height() / 2)
        elif pin.side == PinSide.RIGHT:
            name_item.setPos(tip_pt.x() + _LABEL_OFFSET,
                             tip_pt.y() - nb.height() / 2)
        elif pin.side == PinSide.TOP:
            name_item.setPos(tip_pt.x() - nb.width() / 2,
                             tip_pt.y() - nb.height() - _LABEL_OFFSET)
        else:  # BOTTOM
            name_item.setPos(tip_pt.x() - nb.width() / 2,
                             tip_pt.y() + _LABEL_OFFSET)

        name_item.setZValue(3)
        self._scene.addItem(name_item)

        # 引脚编号小标签（在IC矩形内侧）
        if pin.pin_number:
            num_item = QGraphicsSimpleTextItem(pin.pin_number)
            num_item.setFont(QFont("Segoe UI", _FONT_SIZE - 2))
            num_item.setBrush(QBrush(QColor("#888888")))
            nmb = num_item.boundingRect()
            if pin.side == PinSide.LEFT:
                num_item.setPos(edge_pt.x() + 3,
                                edge_pt.y() - nmb.height() / 2)
            elif pin.side == PinSide.RIGHT:
                num_item.setPos(edge_pt.x() - nmb.width() - 3,
                                edge_pt.y() - nmb.height() / 2)
            elif pin.side == PinSide.TOP:
                num_item.setPos(edge_pt.x() - nmb.width() / 2,
                                edge_pt.y() + 3)
            else:
                num_item.setPos(edge_pt.x() - nmb.width() / 2,
                                edge_pt.y() - nmb.height() - 3)
            num_item.setZValue(3)
            self._scene.addItem(num_item)

    # ── 引脚定位计算 ──────────────────────────────────

    @staticmethod
    def _count_pins_per_side(sym: SymbolDef) -> dict[PinSide, int]:
        counts: dict[PinSide, int] = {}
        for p in sym.pins:
            counts[p.side] = counts.get(p.side, 0) + 1
        return counts

    @staticmethod
    def _assign_slots(sym: SymbolDef) -> None:
        """根据 side 分组，自动为每个引脚分配 slot 值。"""
        by_side: dict[PinSide, list[SymbolPin]] = {}
        for p in sym.pins:
            by_side.setdefault(p.side, []).append(p)
        for side, pins in by_side.items():
            total = len(pins)
            for i, p in enumerate(pins):
                p.slot = f"{i + 1}/{total}"

    def _pin_edge_point(self, pin: SymbolPin, ic_rect: QRectF) -> QPointF:
        """计算引脚在 IC 矩形边缘的连接点。"""
        slot_parts = pin.slot.split("/") if pin.slot else ["1", "1"]
        idx = int(slot_parts[0])
        total = int(slot_parts[1])

        if pin.side in (PinSide.LEFT, PinSide.RIGHT):
            # 纵向分布
            span = ic_rect.height()
            pad = self._symbol.edge_pad_h * _UNIT if self._symbol else 20
            usable = span - 2 * pad
            spacing = usable / max(total, 1)
            y = ic_rect.top() + pad + spacing * (idx - 0.5)
            x = ic_rect.left() if pin.side == PinSide.LEFT else ic_rect.right()
        else:
            # 横向分布
            span = ic_rect.width()
            pad = self._symbol.edge_pad_w * _UNIT if self._symbol else 20
            usable = span - 2 * pad
            spacing = usable / max(total, 1)
            x = ic_rect.left() + pad + spacing * (idx - 0.5)
            y = ic_rect.top() if pin.side == PinSide.TOP else ic_rect.bottom()

        return QPointF(x, y)

    # ── 缩放 ──────────────────────────────────────────

    def wheelEvent(self, event: QWheelEvent) -> None:
        delta = event.angleDelta().y() / 120.0
        direction = math.copysign(1, delta)
        factor = (1.0 + self.ZOOM_STEP * direction) ** abs(delta)
        resulting = self._zoom * factor
        if resulting < self.ZOOM_MIN:
            factor = self.ZOOM_MIN / self._zoom
        elif resulting > self.ZOOM_MAX:
            factor = self.ZOOM_MAX / self._zoom
        self.scale(factor, factor)
        self._zoom *= factor
        event.accept()

    def drawBackground(self, painter: QPainter, rect: QRectF | QRect) -> None:
        painter.save()
        painter.resetTransform()
        painter.drawTiledPixmap(
            self.viewport().rect(),
            self.backgroundBrush().texture(),
        )
        painter.restore()

    # ── 引脚编辑对话框 ───────────────────────────────

    def edit_pin_dialog(self, pin: SymbolPin) -> None:
        """弹出引脚属性编辑对话框。"""
        dlg = PinEditDialog(pin, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            dlg.apply_to_pin(pin)
            self._rebuild()
            self.symbol_changed.emit()


# ============================================================
# 引脚编辑对话框
# ============================================================


class PinEditDialog(QDialog):
    """引脚属性编辑对话框。"""

    def __init__(self, pin: SymbolPin, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"编辑引脚: {pin.name}")
        self.resize(360, 280)

        form = QFormLayout()

        self._name = QLineEdit(pin.name)
        form.addRow("名称", self._name)

        self._pin_number = QLineEdit(pin.pin_number)
        form.addRow("引脚编号", self._pin_number)

        self._side = QComboBox()
        for s in PinSide:
            self._side.addItem(_SIDE_LABELS[s], s.value)
        self._side.setCurrentIndex(list(PinSide).index(pin.side))
        form.addRow("方位", self._side)

        self._pin_type = QComboBox()
        for val, name in _PIN_TYPE_NAMES.items():
            self._pin_type.addItem(name, val)
        # 尝试选中当前类型
        for i in range(self._pin_type.count()):
            if self._pin_type.itemData(i) == pin.pin_type.value:
                self._pin_type.setCurrentIndex(i)
                break
        form.addRow("类型", self._pin_type)

        self._description = QLineEdit(pin.description)
        form.addRow("描述", self._description)

        self._inverted = QComboBox()
        self._inverted.addItem("否", False)
        self._inverted.addItem("是", True)
        self._inverted.setCurrentIndex(1 if pin.inverted else 0)
        form.addRow("反相", self._inverted)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def apply_to_pin(self, pin: SymbolPin) -> None:
        """将对话框中的值写回引脚。"""
        from schemaforge.core.models import PinType as PT

        pin.name = self._name.text().strip() or pin.name
        pin.pin_number = self._pin_number.text().strip()
        pin.side = PinSide(self._side.currentData())
        pin.pin_type = PT(self._pin_type.currentData())
        pin.description = self._description.text().strip()
        pin.inverted = bool(self._inverted.currentData())


# ============================================================
# 符号编辑器组合控件 (预览/编辑模式 + 工具栏)
# ============================================================


class SymbolEditorWidget(QWidget):
    """器件符号预览/编辑 组合控件。

    包含:
    - 工具栏: [预览/编辑模式切换] [适应窗口] [添加引脚] [保存]
    - SymbolCanvas 画布
    - 引脚列表提示

    信号:
    - symbol_saved(str, SymbolDef): 保存时发射 (part_number, new_symbol)
    """

    symbol_saved = Signal(str, object)  # (part_number, SymbolDef)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._part_number: str = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # 工具栏
        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)

        self._mode_label = QLabel("📋 预览模式")
        self._mode_label.setStyleSheet("font-weight: bold;")
        toolbar.addWidget(self._mode_label)

        self._btn_toggle = QPushButton("切换到编辑")
        self._btn_toggle.setFixedWidth(100)
        self._btn_toggle.clicked.connect(self._toggle_mode)
        toolbar.addWidget(self._btn_toggle)

        toolbar.addSpacing(12)

        btn_fit = QPushButton("适应")
        btn_fit.setFixedWidth(50)
        btn_fit.clicked.connect(self._on_fit)
        toolbar.addWidget(btn_fit)

        self._btn_add_pin = QPushButton("+ 引脚")
        self._btn_add_pin.setFixedWidth(70)
        self._btn_add_pin.setVisible(False)
        self._btn_add_pin.clicked.connect(self._on_add_pin)
        toolbar.addWidget(self._btn_add_pin)

        self._btn_del_pin = QPushButton("- 引脚")
        self._btn_del_pin.setFixedWidth(70)
        self._btn_del_pin.setVisible(False)
        self._btn_del_pin.clicked.connect(self._on_del_pin)
        toolbar.addWidget(self._btn_del_pin)

        self._btn_save = QPushButton("💾 保存")
        self._btn_save.setFixedWidth(80)
        self._btn_save.setVisible(False)
        self._btn_save.clicked.connect(self._on_save)
        toolbar.addWidget(self._btn_save)

        toolbar.addStretch()

        self._pin_count_label = QLabel("")
        toolbar.addWidget(self._pin_count_label)

        layout.addLayout(toolbar)

        # 画布
        self._canvas = SymbolCanvas()
        self._canvas.symbol_changed.connect(self._on_symbol_changed)
        layout.addWidget(self._canvas, 1)

        # 空状态提示
        self._empty_label = QLabel("选择一个器件以预览其符号")
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.setStyleSheet("color: #666; font-size: 14px;")
        layout.addWidget(self._empty_label)

        self._is_edit_mode = False

    # ── 公开 API ──────────────────────────────────────

    def load_device(
        self,
        part_number: str,
        symbol: SymbolDef | None,
    ) -> None:
        """加载器件符号到预览模式。"""
        self._part_number = part_number
        self._is_edit_mode = False
        self._update_ui_mode()

        if symbol and symbol.pins:
            self._empty_label.setVisible(False)
            self._canvas.setVisible(True)
            self._canvas.set_symbol(symbol, label=part_number, editable=False)
            self._pin_count_label.setText(f"{len(symbol.pins)} 个引脚")
        else:
            self._empty_label.setVisible(True)
            self._canvas.setVisible(False)
            self._pin_count_label.setText("无符号定义")

    def clear(self) -> None:
        self._part_number = ""
        self._canvas.set_symbol(None)
        self._empty_label.setVisible(True)
        self._canvas.setVisible(False)
        self._pin_count_label.setText("")

    # ── 内部 ──────────────────────────────────────────

    def _toggle_mode(self) -> None:
        sym = self._canvas.get_symbol()
        if sym is None:
            return
        self._is_edit_mode = not self._is_edit_mode
        self._update_ui_mode()
        self._canvas.set_symbol(sym, label=self._part_number,
                                editable=self._is_edit_mode)

    def _update_ui_mode(self) -> None:
        if self._is_edit_mode:
            self._mode_label.setText("✏️ 编辑模式")
            self._btn_toggle.setText("切换到预览")
            self._btn_add_pin.setVisible(True)
            self._btn_del_pin.setVisible(True)
            self._btn_save.setVisible(True)
        else:
            self._mode_label.setText("📋 预览模式")
            self._btn_toggle.setText("切换到编辑")
            self._btn_add_pin.setVisible(False)
            self._btn_del_pin.setVisible(False)
            self._btn_save.setVisible(False)

    def _on_fit(self) -> None:
        self._canvas.fit_in_view()

    def _on_symbol_changed(self) -> None:
        sym = self._canvas.get_symbol()
        if sym:
            self._pin_count_label.setText(f"{len(sym.pins)} 个引脚")

    def _on_add_pin(self) -> None:
        sym = self._canvas.get_symbol()
        if sym is None:
            return
        new_pin = SymbolPin(
            name=f"P{len(sym.pins) + 1}",
            pin_number=str(len(sym.pins) + 1),
            side=PinSide.LEFT,
        )
        sym.pins.append(new_pin)
        self._canvas.set_symbol(sym, label=self._part_number, editable=True)

    def _on_del_pin(self) -> None:
        """删除最后一个引脚（简化操作，也可选中删除）。"""
        sym = self._canvas.get_symbol()
        if sym is None or not sym.pins:
            return
        reply = QMessageBox.question(
            self,
            "删除引脚",
            f"确定删除引脚 '{sym.pins[-1].name}'？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            sym.pins.pop()
            self._canvas.set_symbol(sym, label=self._part_number, editable=True)

    def _on_save(self) -> None:
        sym = self._canvas.get_symbol()
        if sym is None or not self._part_number:
            return
        self.symbol_saved.emit(self._part_number, sym)
        self._is_edit_mode = False
        self._update_ui_mode()
        self._canvas.set_symbol(sym, label=self._part_number, editable=False)
