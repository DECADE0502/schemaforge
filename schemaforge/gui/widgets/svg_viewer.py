"""可缩放 SVG 预览控件

基于 QGraphicsView + QGraphicsSvgItem 实现，支持：
- 鼠标滚轮缩放（锚定鼠标位置）
- 拖拽平移
- 棋盘格透明背景
- 编程接口缩放 / 适配视口

用法::

    viewer = SvgZoomView()
    viewer.load_file("output.svg")
    viewer.fit_to_view()
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QPainter, QPen, QPixmap, QWheelEvent
from PySide6.QtSvgWidgets import QGraphicsSvgItem
from PySide6.QtWidgets import (
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
)

# ============================================================
# 常量
# ============================================================

_ZOOM_MIN = 0.05
_ZOOM_MAX = 50.0
_ZOOM_FACTOR = 1.15  # 每次滚轮缩放因子

_CHECKER_DARK = QColor("#262626")
_CHECKER_LIGHT = QColor("#2d2d2d")
_CHECKER_SIZE = 16  # px per tile


def _make_checker_brush() -> QBrush:
    """创建棋盘格背景画刷。"""
    tile_size = _CHECKER_SIZE * 2
    pixmap = QPixmap(tile_size, tile_size)
    pixmap.fill(_CHECKER_LIGHT)
    painter = QPainter(pixmap)
    painter.fillRect(0, 0, _CHECKER_SIZE, _CHECKER_SIZE, _CHECKER_DARK)
    painter.fillRect(_CHECKER_SIZE, _CHECKER_SIZE, _CHECKER_SIZE, _CHECKER_SIZE, _CHECKER_DARK)
    painter.end()
    return QBrush(pixmap)


# ============================================================
# SvgZoomView
# ============================================================


class SvgZoomView(QGraphicsView):
    """可缩放、可拖拽的 SVG 预览控件。"""

    zoom_changed = Signal(float)
    """当前缩放因子变化时发出（值域 [_ZOOM_MIN, _ZOOM_MAX]）。"""

    def __init__(self, parent: object | None = None) -> None:
        super().__init__(parent)  # type: ignore[arg-type]

        # --- 场景 ---
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)

        # --- SVG 图元 ---
        self._svg_item: QGraphicsSvgItem | None = None
        self._bg_rect: QGraphicsRectItem | None = None
        self._outline_rect: QGraphicsRectItem | None = None

        # --- 缩放状态 ---
        self._current_zoom: float = 1.0

        # --- 渲染提示 ---
        self.setRenderHints(
            QPainter.RenderHint.Antialiasing
            | QPainter.RenderHint.SmoothPixmapTransform,
        )

        # --- 交互模式 ---
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)

        # --- 背景 ---
        self.setBackgroundBrush(_make_checker_brush())

        # --- 外观 ---
        self.setFrameShape(QGraphicsView.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

    # ----------------------------------------------------------
    # 公开 API
    # ----------------------------------------------------------

    def load_file(self, file_path: str) -> bool:
        """从文件加载 SVG。

        Args:
            file_path: SVG 文件路径。

        Returns:
            True 如果加载成功。
        """
        path = Path(file_path)
        if not path.is_file():
            return False

        self._clear_scene()

        svg_item = QGraphicsSvgItem(str(path))
        if svg_item.boundingRect().isEmpty():
            return False

        svg_item.setCacheMode(QGraphicsSvgItem.CacheMode.NoCache)
        self._setup_scene(svg_item)
        return True

    def load_string(self, svg_content: str) -> bool:
        """从 XML 字符串加载 SVG。

        Args:
            svg_content: SVG XML 字符串。

        Returns:
            True 如果加载成功。
        """
        # QGraphicsSvgItem 不直接支持字符串输入，
        # 写入临时文件后加载。
        try:
            with tempfile.NamedTemporaryFile(
                suffix=".svg",
                delete=False,
                mode="w",
                encoding="utf-8",
            ) as tmp:
                tmp.write(svg_content)
                tmp_path = tmp.name

            return self.load_file(tmp_path)
        except Exception:
            return False

    def fit_to_view(self) -> None:
        """将整个 SVG 适配到视口内。"""
        if self._svg_item is None:
            return
        self.fitInView(self._svg_item, Qt.AspectRatioMode.KeepAspectRatio)
        self._update_zoom_from_transform()

    def reset_zoom(self) -> None:
        """重置为 1:1 缩放。"""
        self.resetTransform()
        self._current_zoom = 1.0
        self.zoom_changed.emit(self._current_zoom)

    def zoom_in(self) -> None:
        """编程式放大。"""
        self._apply_zoom(_ZOOM_FACTOR)

    def zoom_out(self) -> None:
        """编程式缩小。"""
        self._apply_zoom(1.0 / _ZOOM_FACTOR)

    # ----------------------------------------------------------
    # 鼠标滚轮
    # ----------------------------------------------------------

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        """鼠标滚轮缩放，锚定鼠标位置。"""
        angle = event.angleDelta().y()
        if angle == 0:
            return

        factor = _ZOOM_FACTOR if angle > 0 else (1.0 / _ZOOM_FACTOR)
        self._apply_zoom(factor)

    # ----------------------------------------------------------
    # 内部方法
    # ----------------------------------------------------------

    def _apply_zoom(self, factor: float) -> None:
        """应用缩放因子并发出信号。"""
        new_zoom = self._current_zoom * factor
        new_zoom = max(_ZOOM_MIN, min(_ZOOM_MAX, new_zoom))

        actual_factor = new_zoom / self._current_zoom
        if actual_factor == 1.0:
            return

        self.scale(actual_factor, actual_factor)
        self._current_zoom = new_zoom
        self.zoom_changed.emit(self._current_zoom)

    def _update_zoom_from_transform(self) -> None:
        """从当前变换矩阵反推缩放因子。"""
        transform = self.transform()
        self._current_zoom = transform.m11()
        self._current_zoom = max(_ZOOM_MIN, min(_ZOOM_MAX, self._current_zoom))
        self.zoom_changed.emit(self._current_zoom)

    def _clear_scene(self) -> None:
        """清空场景。"""
        self._scene.clear()
        self._svg_item = None
        self._bg_rect = None
        self._outline_rect = None
        self.resetTransform()
        self._current_zoom = 1.0

    def _setup_scene(self, svg_item: QGraphicsSvgItem) -> None:
        """在场景中设置 SVG 图元、白色背景和虚线轮廓。"""
        self._svg_item = svg_item
        svg_rect: QRectF = svg_item.boundingRect()

        # 白色背景矩形（让 SVG 在棋盘格上可见）
        self._bg_rect = QGraphicsRectItem(svg_rect)
        self._bg_rect.setBrush(QBrush(QColor("#ffffff")))
        self._bg_rect.setPen(QPen(Qt.PenStyle.NoPen))
        self._bg_rect.setZValue(-1)
        self._scene.addItem(self._bg_rect)

        # SVG 本体
        self._scene.addItem(svg_item)

        # 虚线轮廓框（宇宙笔宽度，不随缩放变化）
        outline_pen = QPen(QColor("#555555"), 1.0, Qt.PenStyle.DashLine)
        outline_pen.setCosmetic(True)
        self._outline_rect = QGraphicsRectItem(svg_rect)
        self._outline_rect.setPen(outline_pen)
        self._outline_rect.setBrush(QBrush(Qt.BrushStyle.NoBrush))
        self._outline_rect.setZValue(1)
        self._scene.addItem(self._outline_rect)

        # 设置场景区域（留一圈边距）
        margin = max(svg_rect.width(), svg_rect.height()) * 0.2
        padded = svg_rect.adjusted(-margin, -margin, margin, margin)
        self._scene.setSceneRect(padded)
