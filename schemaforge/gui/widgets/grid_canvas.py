"""Interactive grid-based schematic canvas.

Replaces the static SVG viewer with a QGraphicsView-based interactive editor where:
- Background is a dotted grid (50px spacing)
- Modules are draggable blocks with IC name + pins
- External components (caps, resistors, inductors) grouped near parent module
- Connections are auto-routed orthogonal lines with net labels
- GND symbols at ground connections
- Grid-snap dragging, mouse-wheel zoom, fit-to-view

Usage::

    canvas = GridCanvas()
    canvas.load_system_design(ir)
    canvas.fit_to_view()
"""

from __future__ import annotations

import logging
import math
from typing import Any

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QPainter,
    QPainterPath,
    QPen,
    QPolygonF,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsPathItem,
    QGraphicsPolygonItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsView,
)

from schemaforge.system.models import (
    ModuleInstance,
    NetType,
    ResolvedConnection,
    SystemDesignIR,
)

logger = logging.getLogger(__name__)

# ============================================================
# Constants
# ============================================================

_GRID_SIZE = 50  # pixels per grid cell
_ZOOM_MIN = 0.05
_ZOOM_MAX = 20.0
_ZOOM_FACTOR = 1.15

# Module block sizing
_MODULE_MIN_W = 200
_MODULE_MIN_H = 120
_MODULE_HEADER_H = 32
_PIN_SPACING = 24
_PIN_DOT_R = 4.0
_PIN_LEAD = 16.0
_COMPONENT_W = 60
_COMPONENT_H = 36
_COMPONENT_GAP = 8

# Category color scheme
_CATEGORY_COLORS: dict[str, str] = {
    "buck": "#2196F3",
    "ldo": "#4CAF50",
    "mcu": "#9C27B0",
    "led": "#FFC107",
    "flyback": "#FF5722",
    "sepic": "#00BCD4",
    "boost": "#3F51B5",
    "opamp": "#E91E63",
}
_DEFAULT_COLOR = "#607D8B"

# Dark theme palette
_BG_COLOR = QColor("#1e1e1e")
_GRID_DOT_COLOR = QColor("#3a3a3a")
_MODULE_BODY_COLOR = QColor("#2d2d2d")
_MODULE_BORDER_COLOR = QColor("#555555")
_TEXT_COLOR = QColor("#e0e0e0")
_TEXT_DIM_COLOR = QColor("#999999")
_PIN_DOT_COLOR = QColor("#aaaaaa")
_CONNECTION_COLOR = QColor("#4fc3f7")
_NET_LABEL_COLOR = QColor("#81d4fa")
_GND_COLOR = QColor("#ff8a65")
_COMPONENT_BG = QColor("#363636")
_COMPONENT_BORDER = QColor("#555555")

_FONT_NAME = "Segoe UI"
_FONT_SIZE_HEADER = 11
_FONT_SIZE_PIN = 9
_FONT_SIZE_LABEL = 8
_FONT_SIZE_COMPONENT = 8
_FONT_SIZE_NET = 8


# ============================================================
# Helper: snap to grid
# ============================================================


def _snap(value: float, grid: int = _GRID_SIZE) -> float:
    """Snap a value to the nearest grid point."""
    return round(value / grid) * grid


def _category_color(category: str) -> QColor:
    """Return header color for a module category."""
    cat = category.lower().strip()
    for key, hex_color in _CATEGORY_COLORS.items():
        if key in cat:
            return QColor(hex_color)
    return QColor(_DEFAULT_COLOR)


# ============================================================
# ModuleBlockItem
# ============================================================


class ModuleBlockItem(QGraphicsRectItem):
    """Draggable module block on the grid canvas.

    Displays the IC part number, category, and pin names arranged on
    left/right sides. Background header color varies by category.
    Snaps to 50px grid when dragged.
    """

    def __init__(
        self,
        module_id: str,
        instance: ModuleInstance,
        parent: QGraphicsItem | None = None,
    ) -> None:
        self.module_id = module_id
        self.instance = instance

        # Determine pin lists for left/right
        self._left_pins: list[str] = []
        self._right_pins: list[str] = []
        self._classify_pins(instance)

        # Calculate block size
        pin_rows = max(len(self._left_pins), len(self._right_pins), 2)
        w = max(_MODULE_MIN_W, 200)
        h = max(_MODULE_MIN_H, _MODULE_HEADER_H + pin_rows * _PIN_SPACING + 16)

        # Snap dimensions to grid
        w = int(math.ceil(w / _GRID_SIZE) * _GRID_SIZE)
        h = int(math.ceil(h / _GRID_SIZE) * _GRID_SIZE)

        super().__init__(0, 0, w, h, parent)

        # Visual setup
        self.setBrush(QBrush(_MODULE_BODY_COLOR))
        self.setPen(QPen(_MODULE_BORDER_COLOR, 1.5))
        self.setZValue(10)

        # Draggable
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, True)
        self.setCursor(Qt.CursorShape.OpenHandCursor)

        # Build child items
        self._build_header(w, instance)
        self._build_pins(w, h)

        # Pin anchor points (scene-relative computed on demand)
        self._pin_anchors: dict[str, QPointF] = {}
        self._compute_pin_anchors(w, h)

        # Tooltip
        dev = instance.device
        pn = dev.part_number if dev else "N/A"
        tip = f"{module_id}: {pn}\n{instance.role}"
        if instance.parameters:
            for k, v in list(instance.parameters.items())[:5]:
                tip += f"\n  {k}: {v}"
        self.setToolTip(tip)

    # ----------------------------------------------------------
    # Pin classification
    # ----------------------------------------------------------

    def _classify_pins(self, instance: ModuleInstance) -> None:
        """Classify pins into left/right using device symbol (preferred) or resolved ports."""
        dev = instance.device

        # Priority 1: Use device symbol pins (correct left/right/top/bottom from library)
        if dev is not None:
            symbol = getattr(dev, "symbol", None)
            if symbol is not None:
                pins = getattr(symbol, "pins", [])
                if pins:
                    for pin in pins:
                        side = getattr(pin, "side", None)
                        name = getattr(pin, "name", "?")
                        side_val = side.value if hasattr(side, "value") else str(side)
                        if side_val in ("left",):
                            self._left_pins.append(name)
                        elif side_val in ("right",):
                            self._right_pins.append(name)
                        elif side_val == "top":
                            # Top pins go to left side (displayed first)
                            self._left_pins.insert(0, f"{name}")
                        elif side_val == "bottom":
                            # Bottom pins go to right side (GND etc)
                            self._right_pins.append(f"{name}")
                    if self._left_pins or self._right_pins:
                        return

        # Priority 2: Use resolved ports
        input_roles = {"power_in", "input", "enable", "ground"}
        for role, port in instance.resolved_ports.items():
            name = port.pin_name or role
            if role in input_roles or "in" in role.lower():
                self._left_pins.append(name)
            else:
                self._right_pins.append(name)

        # Fallback: at least show something
        if not self._left_pins and not self._right_pins:
            self._left_pins = ["IN"]
            self._right_pins = ["OUT"]

    # ----------------------------------------------------------
    # Build child items
    # ----------------------------------------------------------

    def _build_header(self, w: float, instance: ModuleInstance) -> None:
        """Draw the colored header bar with part number and category."""
        dev = instance.device
        pn = dev.part_number if dev else instance.module_id
        category = instance.resolved_category or (dev.category if dev else "")

        color = _category_color(category)

        # Header background
        header = QGraphicsRectItem(0, 0, w, _MODULE_HEADER_H, self)
        header.setBrush(QBrush(color))
        header.setPen(QPen(Qt.PenStyle.NoPen))

        # Part number text
        pn_text = QGraphicsSimpleTextItem(pn, self)
        pn_text.setFont(QFont(_FONT_NAME, _FONT_SIZE_HEADER, QFont.Weight.Bold))
        pn_text.setBrush(QBrush(QColor("#ffffff")))
        pn_rect = pn_text.boundingRect()
        pn_text.setPos(8, (_MODULE_HEADER_H - pn_rect.height()) / 2)

        # Category label (right side of header)
        if category:
            cat_text = QGraphicsSimpleTextItem(f"({category})", self)
            cat_text.setFont(QFont(_FONT_NAME, _FONT_SIZE_LABEL))
            cat_text.setBrush(QBrush(QColor(255, 255, 255, 180)))
            cat_rect = cat_text.boundingRect()
            cat_text.setPos(w - cat_rect.width() - 8, (_MODULE_HEADER_H - cat_rect.height()) / 2)

    def _build_pins(self, w: float, h: float) -> None:
        """Draw pin dots and labels on left/right sides."""
        y_start = _MODULE_HEADER_H + 12

        # Left pins
        for i, name in enumerate(self._left_pins):
            y = y_start + i * _PIN_SPACING
            # Dot
            dot = QGraphicsRectItem(-_PIN_DOT_R, y - _PIN_DOT_R, _PIN_DOT_R * 2, _PIN_DOT_R * 2, self)
            dot.setBrush(QBrush(_PIN_DOT_COLOR))
            dot.setPen(QPen(Qt.PenStyle.NoPen))
            # Label
            label = QGraphicsSimpleTextItem(name, self)
            label.setFont(QFont(_FONT_NAME, _FONT_SIZE_PIN))
            label.setBrush(QBrush(_TEXT_COLOR))
            label.setPos(6, y - label.boundingRect().height() / 2)

        # Right pins
        for i, name in enumerate(self._right_pins):
            y = y_start + i * _PIN_SPACING
            # Dot
            dot = QGraphicsRectItem(w - _PIN_DOT_R, y - _PIN_DOT_R, _PIN_DOT_R * 2, _PIN_DOT_R * 2, self)
            dot.setBrush(QBrush(_PIN_DOT_COLOR))
            dot.setPen(QPen(Qt.PenStyle.NoPen))
            # Label
            label = QGraphicsSimpleTextItem(name, self)
            label.setFont(QFont(_FONT_NAME, _FONT_SIZE_PIN))
            lb = label.boundingRect()
            label.setBrush(QBrush(_TEXT_COLOR))
            label.setPos(w - lb.width() - 6, y - lb.height() / 2)

    def _compute_pin_anchors(self, w: float, h: float) -> None:
        """Compute connection anchor points for each pin (in item-local coords)."""
        y_start = _MODULE_HEADER_H + 12

        for i, name in enumerate(self._left_pins):
            y = y_start + i * _PIN_SPACING
            self._pin_anchors[name.lower()] = QPointF(0, y)

        for i, name in enumerate(self._right_pins):
            y = y_start + i * _PIN_SPACING
            self._pin_anchors[name.lower()] = QPointF(w, y)

        # Also store generic anchors
        if self._left_pins:
            self._pin_anchors["_left_center"] = QPointF(0, y_start + len(self._left_pins) * _PIN_SPACING / 2)
        if self._right_pins:
            self._pin_anchors["_right_center"] = QPointF(w, y_start + len(self._right_pins) * _PIN_SPACING / 2)

        self._pin_anchors["_top_center"] = QPointF(w / 2, 0)
        self._pin_anchors["_bottom_center"] = QPointF(w / 2, h)

    def get_anchor_scene_pos(self, pin_name: str) -> QPointF:
        """Get scene-coordinate anchor for a pin name."""
        key = pin_name.lower()
        if key in self._pin_anchors:
            return self.mapToScene(self._pin_anchors[key])

        # Fuzzy match
        for k, pt in self._pin_anchors.items():
            if k.startswith("_"):
                continue
            if key in k or k in key:
                return self.mapToScene(pt)

        # Default: right center for outputs, left center for inputs
        fallback = self._pin_anchors.get("_right_center") or self._pin_anchors.get("_left_center")
        if fallback:
            return self.mapToScene(fallback)
        return self.mapToScene(QPointF(self.rect().width() / 2, self.rect().height() / 2))

    # ----------------------------------------------------------
    # Drag with grid snap
    # ----------------------------------------------------------

    def itemChange(self, change: QGraphicsItem.GraphicsItemChange, value: Any) -> Any:  # noqa: N802
        """Snap position to grid on move."""
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionChange and self.scene():
            new_pos = value  # QPointF
            snapped = QPointF(_snap(new_pos.x()), _snap(new_pos.y()))
            return snapped
        return super().itemChange(change, value)

    def mouseReleaseEvent(self, event: Any) -> None:  # noqa: N802
        """Emit module_moved signal after drag."""
        super().mouseReleaseEvent(event)
        scene = self.scene()
        if scene is not None:
            for view in scene.views():
                if isinstance(view, GridCanvas):
                    view.module_moved.emit(self.module_id, self.pos().x(), self.pos().y())
                    view._update_connections()
                    break


# ============================================================
# ComponentItem
# ============================================================


class ComponentItem(QGraphicsRectItem):
    """Small external component (cap, resistor, inductor) near its parent module.

    Displays reference designator and value in a compact rectangle.
    """

    def __init__(
        self,
        ref: str,
        role: str,
        value: str,
        parent_block: ModuleBlockItem,
        offset_x: float = 0,
        offset_y: float = 0,
    ) -> None:
        super().__init__(0, 0, _COMPONENT_W, _COMPONENT_H, parent_block)
        self.setPos(offset_x, offset_y)

        self.setBrush(QBrush(_COMPONENT_BG))
        self.setPen(QPen(_COMPONENT_BORDER, 1.0))
        self.setZValue(8)

        # Ref label (e.g. "C1")
        ref_text = QGraphicsSimpleTextItem(ref, self)
        ref_text.setFont(QFont(_FONT_NAME, _FONT_SIZE_COMPONENT, QFont.Weight.Bold))
        ref_text.setBrush(QBrush(_TEXT_COLOR))
        ref_rect = ref_text.boundingRect()
        ref_text.setPos((_COMPONENT_W - ref_rect.width()) / 2, 2)

        # Value label (e.g. "10uF")
        if value:
            val_text = QGraphicsSimpleTextItem(value, self)
            val_text.setFont(QFont(_FONT_NAME, _FONT_SIZE_COMPONENT))
            val_text.setBrush(QBrush(_TEXT_DIM_COLOR))
            val_rect = val_text.boundingRect()
            val_text.setPos((_COMPONENT_W - val_rect.width()) / 2, 16)

        # Tooltip
        self.setToolTip(f"{ref}: {role}\n{value}")


# ============================================================
# GndSymbol
# ============================================================


class GndSymbol(QGraphicsPolygonItem):
    """Ground symbol (downward triangle)."""

    def __init__(self, parent: QGraphicsItem | None = None) -> None:
        tri = QPolygonF([
            QPointF(-8, 0),
            QPointF(8, 0),
            QPointF(0, 10),
        ])
        super().__init__(tri, parent)
        self.setBrush(QBrush(_GND_COLOR))
        self.setPen(QPen(_GND_COLOR, 1.5))
        self.setZValue(12)


# ============================================================
# ConnectionLine
# ============================================================


class ConnectionLine(QGraphicsPathItem):
    """Orthogonal auto-routed connection between modules.

    Draws an L-shaped or Z-shaped path and labels with net name at midpoint.
    """

    def __init__(
        self,
        src_pos: QPointF,
        dst_pos: QPointF,
        net_name: str,
        parent: QGraphicsItem | None = None,
    ) -> None:
        super().__init__(parent)
        self.net_name = net_name
        self._label: QGraphicsSimpleTextItem | None = None

        pen = QPen(_CONNECTION_COLOR, 1.5)
        pen.setCosmetic(True)
        self.setPen(pen)
        self.setZValue(5)

        self.update_path(src_pos, dst_pos)

    def update_path(self, src: QPointF, dst: QPointF) -> None:
        """Recalculate the orthogonal path between two points."""
        path = QPainterPath()
        path.moveTo(src)

        dx = dst.x() - src.x()
        dy = dst.y() - src.y()

        if abs(dx) < 1 or abs(dy) < 1:
            # Nearly aligned: single L
            mid_x = src.x() + dx / 2
            path.lineTo(mid_x, src.y())
            path.lineTo(mid_x, dst.y())
            path.lineTo(dst)
        else:
            # Z-shaped: horizontal, vertical, horizontal
            mid_x = src.x() + dx / 2
            path.lineTo(mid_x, src.y())
            path.lineTo(mid_x, dst.y())
            path.lineTo(dst)

        self.setPath(path)

        # Update or create label
        mid_pt = QPointF(src.x() + dx / 2, src.y() + dy / 2)
        if self._label is None:
            self._label = QGraphicsSimpleTextItem(self.net_name, self)
            self._label.setFont(QFont(_FONT_NAME, _FONT_SIZE_NET))
            self._label.setBrush(QBrush(_NET_LABEL_COLOR))
            self._label.setZValue(6)

        lb = self._label.boundingRect()
        self._label.setPos(mid_pt.x() - lb.width() / 2, mid_pt.y() - lb.height() - 2)


# ============================================================
# GridCanvas (main widget)
# ============================================================


class GridCanvas(QGraphicsView):
    """Interactive grid-based schematic canvas.

    Provides:
    - Dotted grid background
    - Draggable module blocks with grid snapping
    - External components grouped near parent modules
    - Orthogonal auto-routed connection lines
    - Net labels and GND symbols
    - Mouse wheel zoom, fit-to-view
    - SVG export
    """

    # Signals
    zoom_changed = Signal(float)
    module_moved = Signal(str, float, float)  # module_id, new_x, new_y

    def __init__(self, parent: Any | None = None) -> None:
        super().__init__(parent)  # type: ignore[arg-type]

        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)

        self._grid_size = _GRID_SIZE
        self._current_zoom: float = 1.0

        # State
        self._module_blocks: dict[str, ModuleBlockItem] = {}
        self._connections: list[ConnectionLine] = []
        self._connection_data: list[ResolvedConnection] = []
        self._current_ir: SystemDesignIR | None = None

        # Rendering
        self.setRenderHints(
            QPainter.RenderHint.Antialiasing
            | QPainter.RenderHint.SmoothPixmapTransform,
        )
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        self.setFrameShape(QGraphicsView.Shape.NoFrame)

        # Background
        self.setBackgroundBrush(QBrush(_BG_COLOR))

        # Scrollbars
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

    # ==========================================================
    # Public API
    # ==========================================================

    def load_system_design(self, ir: SystemDesignIR) -> None:
        """Load a system design into the canvas.

        1. Clear existing items
        2. Create ModuleBlockItem for each resolved module
        3. Layout modules: power chain left-to-right, control below
        4. Create ComponentItem for each external component
        5. Create ConnectionLine for each resolved connection
        6. Add net labels and GND symbols
        """
        self._clear()
        self._current_ir = ir

        resolved = ir.get_resolved_modules()
        if not resolved:
            logger.warning("No resolved modules to display")
            return

        # Create module blocks
        for inst in resolved:
            block = ModuleBlockItem(inst.module_id, inst)
            self._module_blocks[inst.module_id] = block
            self._scene.addItem(block)

        # Layout
        self._layout_modules(ir)

        # External components
        for mid, block in self._module_blocks.items():
            inst = ir.get_module(mid)
            if inst and inst.external_components:
                self._add_external_components(block, inst.external_components)

        # Connections
        self._connection_data = list(ir.connections)
        self._build_connections()

        # GND symbols
        self._add_gnd_symbols(ir)

        # Fit
        self._update_scene_rect()

    def fit_to_view(self) -> None:
        """Fit all items in view."""
        sr = self._scene.sceneRect()
        if sr.isEmpty():
            return
        self.fitInView(sr, Qt.AspectRatioMode.KeepAspectRatio)
        self._update_zoom_from_transform()

    def reset_zoom(self) -> None:
        """Reset to 1:1 zoom."""
        self.resetTransform()
        self._current_zoom = 1.0
        self.zoom_changed.emit(self._current_zoom)

    def zoom_in(self) -> None:
        """Programmatic zoom in."""
        self._apply_zoom(_ZOOM_FACTOR)

    def zoom_out(self) -> None:
        """Programmatic zoom out."""
        self._apply_zoom(1.0 / _ZOOM_FACTOR)

    def export_svg(self, filepath: str) -> str:
        """Export current canvas to SVG file.

        Returns:
            The filepath written, or empty string on failure.
        """
        try:
            from PySide6.QtSvg import QSvgGenerator

            sr = self._scene.sceneRect()
            generator = QSvgGenerator()
            generator.setFileName(filepath)
            generator.setSize(sr.size().toSize())
            generator.setViewBox(sr)
            generator.setTitle("SchemaForge Grid Canvas Export")

            painter = QPainter(generator)
            self._scene.render(painter)
            painter.end()
            return filepath
        except Exception:
            logger.exception("SVG export failed")
            return ""

    # ==========================================================
    # Layout
    # ==========================================================

    def _layout_modules(self, ir: SystemDesignIR) -> None:
        """Auto-layout modules on the grid.

        Power chain modules go left-to-right.
        MCU/control modules go below the power chain.
        LED/other go to the right of control row.
        """
        power_modules: list[str] = []
        control_modules: list[str] = []

        for mid, inst in ir.module_instances.items():
            if mid not in self._module_blocks:
                continue
            cat = (inst.resolved_category or "").lower()
            placement = ""
            # Check original intent for placement hint
            for intent in ir.request.modules:
                if intent.intent_id == mid:
                    placement = intent.placement_hint
                    break

            if placement == "control_side" or cat in ("mcu", "led", "sensor"):
                control_modules.append(mid)
            else:
                power_modules.append(mid)

        # Sort power modules by priority (from request intents)
        intent_map = {m.intent_id: m.priority for m in ir.request.modules}
        power_modules.sort(key=lambda mid: intent_map.get(mid, 99))
        control_modules.sort(key=lambda mid: intent_map.get(mid, 99))

        # Place power chain: left to right
        x = _GRID_SIZE * 2
        y = _GRID_SIZE * 2
        for mid in power_modules:
            block = self._module_blocks[mid]
            block.setPos(_snap(x), _snap(y))
            x += block.rect().width() + _GRID_SIZE * 3

        # Place control row below
        x = _GRID_SIZE * 2
        y_control = y + _GRID_SIZE * 6  # Below power chain
        for mid in control_modules:
            block = self._module_blocks[mid]
            block.setPos(_snap(x), _snap(y_control))
            x += block.rect().width() + _GRID_SIZE * 3

    # ==========================================================
    # External components
    # ==========================================================

    _ref_counter: dict[str, int] = {}

    def _add_external_components(
        self,
        block: ModuleBlockItem,
        components: list[dict[str, Any]],
    ) -> None:
        """Create ComponentItem children below the parent module block."""
        block_h = block.rect().height()
        x_offset = 0.0

        # Ref prefix by role
        _ROLE_PREFIX: dict[str, str] = {
            "input_cap": "C", "output_cap": "C", "boot_cap": "C",
            "decoupling_cap": "C", "bulk_cap": "C",
            "inductor": "L",
            "fb_upper": "R", "fb_lower": "R", "led_resistor": "R",
            "catch_diode": "D", "diode": "D",
        }

        for comp in components:
            ref = comp.get("ref", "")
            role = comp.get("role", "")
            value = comp.get("value", "")

            # Auto-assign ref if missing
            if not ref or ref == "?":
                prefix = _ROLE_PREFIX.get(role, "X")
                self._ref_counter[prefix] = self._ref_counter.get(prefix, 0) + 1
                ref = f"{prefix}{self._ref_counter[prefix]}"

            ComponentItem(
                ref=ref,
                role=role,
                value=value,
                parent_block=block,
                offset_x=x_offset,
                offset_y=block_h + _COMPONENT_GAP,
            )
            x_offset += _COMPONENT_W + _COMPONENT_GAP

    # ==========================================================
    # Connections
    # ==========================================================

    def _build_connections(self) -> None:
        """Create ConnectionLine items for all resolved connections."""
        for conn in self._connection_data:
            src_mid = conn.src_port.module_id
            dst_mid = conn.dst_port.module_id
            src_block = self._module_blocks.get(src_mid)
            dst_block = self._module_blocks.get(dst_mid)

            if src_block is None or dst_block is None:
                continue

            src_pin = conn.src_port.pin_name or conn.src_port.port_role
            dst_pin = conn.dst_port.pin_name or conn.dst_port.port_role
            src_pos = src_block.get_anchor_scene_pos(src_pin)
            dst_pos = dst_block.get_anchor_scene_pos(dst_pin)

            net_name = conn.net_name or ""
            line = ConnectionLine(src_pos, dst_pos, net_name)
            self._scene.addItem(line)
            self._connections.append(line)

    def _update_connections(self) -> None:
        """Recalculate all connection paths after a module move."""
        # Remove old
        for line in self._connections:
            self._scene.removeItem(line)
        self._connections.clear()

        # Rebuild
        self._build_connections()

    # ==========================================================
    # GND symbols
    # ==========================================================

    def _add_gnd_symbols(self, ir: SystemDesignIR) -> None:
        """Add GND symbols at ground net connection points."""
        for net in ir.nets.values():
            if net.net_type != NetType.GROUND:
                continue
            for member in net.members:
                block = self._module_blocks.get(member.module_id)
                if block is None:
                    continue
                pin_name = member.pin_name or "gnd"
                anchor = block.get_anchor_scene_pos(pin_name)

                gnd = GndSymbol()
                gnd.setPos(anchor.x(), anchor.y() + 2)
                self._scene.addItem(gnd)

                # GND label
                label = QGraphicsSimpleTextItem("GND")
                label.setFont(QFont(_FONT_NAME, _FONT_SIZE_NET))
                label.setBrush(QBrush(_GND_COLOR))
                lb = label.boundingRect()
                label.setPos(anchor.x() - lb.width() / 2, anchor.y() + 14)
                label.setZValue(12)
                self._scene.addItem(label)

    # ==========================================================
    # Grid drawing
    # ==========================================================

    def drawBackground(self, painter: QPainter, rect: QRectF) -> None:  # noqa: N802
        """Draw dotted grid background."""
        super().drawBackground(painter, rect)

        # Determine visible grid range
        left = int(math.floor(rect.left() / self._grid_size)) * self._grid_size
        top = int(math.floor(rect.top() / self._grid_size)) * self._grid_size
        right = int(math.ceil(rect.right() / self._grid_size)) * self._grid_size
        bottom = int(math.ceil(rect.bottom() / self._grid_size)) * self._grid_size

        pen = QPen(_GRID_DOT_COLOR, 1.5)
        pen.setCosmetic(True)
        painter.setPen(pen)

        # Draw dots at grid intersections
        x = left
        while x <= right:
            y = top
            while y <= bottom:
                painter.drawPoint(QPointF(x, y))
                y += self._grid_size
            x += self._grid_size

    # ==========================================================
    # Zoom
    # ==========================================================

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        """Zoom with mouse wheel."""
        angle = event.angleDelta().y()
        if angle == 0:
            return
        factor = _ZOOM_FACTOR if angle > 0 else (1.0 / _ZOOM_FACTOR)
        self._apply_zoom(factor)

    def _apply_zoom(self, factor: float) -> None:
        """Apply zoom factor and emit signal."""
        new_zoom = self._current_zoom * factor
        new_zoom = max(_ZOOM_MIN, min(_ZOOM_MAX, new_zoom))
        actual = new_zoom / self._current_zoom
        if actual == 1.0:
            return
        self.scale(actual, actual)
        self._current_zoom = new_zoom
        self.zoom_changed.emit(self._current_zoom)

    def _update_zoom_from_transform(self) -> None:
        """Derive current zoom from the transform matrix."""
        t = self.transform()
        self._current_zoom = max(_ZOOM_MIN, min(_ZOOM_MAX, t.m11()))
        self.zoom_changed.emit(self._current_zoom)

    # ==========================================================
    # Internal helpers
    # ==========================================================

    def _clear(self) -> None:
        """Clear all items from the scene."""
        self._scene.clear()
        self._module_blocks.clear()
        self._connections.clear()
        self._connection_data.clear()
        self._ref_counter.clear()
        self._current_ir = None
        self.resetTransform()
        self._current_zoom = 1.0

    def _update_scene_rect(self) -> None:
        """Recompute scene rect with padding."""
        items_rect = self._scene.itemsBoundingRect()
        if items_rect.isEmpty():
            return
        margin = max(items_rect.width(), items_rect.height()) * 0.3
        margin = max(margin, _GRID_SIZE * 4)
        padded = items_rect.adjusted(-margin, -margin, margin, margin)
        self._scene.setSceneRect(padded)

    # ==========================================================
    # Compatibility shims (for design_page.py drop-in)
    # ==========================================================

    def load_file(self, file_path: str) -> bool:
        """Compatibility: load an SVG file (delegates to internal SVG viewer).

        When no SystemDesignIR is available, fall back to SVG display.
        """
        try:
            from PySide6.QtSvgWidgets import QGraphicsSvgItem

            self._clear()
            item = QGraphicsSvgItem(file_path)
            if item.boundingRect().isEmpty():
                return False

            # White background behind SVG
            bg = QGraphicsRectItem(item.boundingRect())
            bg.setBrush(QBrush(QColor("#ffffff")))
            bg.setPen(QPen(Qt.PenStyle.NoPen))
            bg.setZValue(-1)
            self._scene.addItem(bg)
            self._scene.addItem(item)

            sr = item.boundingRect()
            margin = max(sr.width(), sr.height()) * 0.2
            self._scene.setSceneRect(sr.adjusted(-margin, -margin, margin, margin))
            return True
        except Exception:
            logger.exception("Failed to load SVG file: %s", file_path)
            return False
