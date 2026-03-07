"""截图生成：将系统设计渲染为 PNG 截图供 AI 审稿使用。

V021-V030: 截图生成阶段。
- 从 SystemDesignIR 重新渲染为 PNG（支持标准/高清 DPI）
- 按 module_bboxes 裁剪模块区域
- 构建审稿清单 ReviewManifest
"""

from __future__ import annotations

import logging
from pathlib import Path

import schemdraw
import schemdraw.elements as elm

from schemaforge.render.base import output_path
from schemaforge.system.models import (
    RenderMetadata,
    SystemBundle,
    SystemDesignIR,
)
from schemaforge.system.rendering import (
    _build_global_ref_map,
    draw_intermodule_wires,
    draw_net_labels,
    layout_control_side,
    layout_power_chain,
)
from schemaforge.visual_review.models import (
    ReviewImageSet,
    ReviewManifest,
    VisualReviewConfig,
)

logger = logging.getLogger(__name__)

# PIL 为可选依赖
try:
    from PIL import Image

    _HAS_PIL = True
except ImportError:  # pragma: no cover
    _HAS_PIL = False


# ============================================================
# V021-V025: PNG 渲染
# ============================================================


def _render_png_from_ir(
    ir: SystemDesignIR,
    filepath: str,
    dpi: int = 150,
) -> dict[str, dict[str, tuple[float, float]]]:
    """从 SystemDesignIR 重新渲染为 PNG 文件。

    复用 rendering.py 的布局函数，但输出为 PNG 而非 SVG。

    Args:
        ir: 系统设计中间表示
        filepath: 输出 PNG 路径
        dpi: 渲染 DPI

    Returns:
        所有模块的锚点映射 module_id -> {port_role: (x, y)}
    """
    ref_map = _build_global_ref_map(ir)

    with schemdraw.Drawing(show=False) as d:
        d.config(fontsize=10, unit=3)

        # 电源链布局
        all_anchors = layout_power_chain(d, ir, ref_map)

        # 空系统占位
        if not ir.module_instances:
            elm.Label().at((0, 0)).label("(empty system)", "top")

        # 控制支路布局
        layout_control_side(d, ir, all_anchors, ref_map)

        # 模块间连线
        draw_intermodule_wires(d, ir.connections, all_anchors)

        # 网络标签
        draw_net_labels(d, ir.nets, all_anchors)

        # 保存为 PNG
        d.save(filepath, dpi=dpi)

    return all_anchors


def _crop_module_regions(
    full_image_path: str,
    metadata: RenderMetadata,
    output_dir: str,
    dpi: int = 150,
) -> dict[str, str]:
    """按 module_bboxes 裁剪模块区域。

    Args:
        full_image_path: 完整 PNG 路径
        metadata: 渲染元数据（含 module_bboxes）
        output_dir: 裁剪图输出目录
        dpi: 渲染 DPI（用于坐标换算）

    Returns:
        module_id -> 裁剪图路径
    """
    if not _HAS_PIL:
        logger.warning("PIL 不可用，跳过模块区域裁剪")
        return {}

    if not metadata.module_bboxes:
        return {}

    img = Image.open(full_image_path)
    img_w, img_h = img.size
    crops: dict[str, str] = {}

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # schemdraw 坐标到像素坐标的缩放因子
    # 使用 DPI 和 schemdraw 的默认单位来估算
    # schemdraw 默认 unit=3 英寸，DPI 决定像素密度
    scale = dpi / 72.0  # 基础缩放（matplotlib 默认 72 DPI）

    for module_id, bbox in metadata.module_bboxes.items():
        x, y, w, h = bbox

        # 添加 padding（10% 的额外边距）
        pad_x = w * 0.1
        pad_y = h * 0.1

        # 转换坐标到像素（注意 Y 轴翻转）
        left = max(0, int((x - pad_x) * scale))
        upper = max(0, int((y - pad_y) * scale))
        right = min(img_w, int((x + w + pad_x) * scale))
        lower = min(img_h, int((y + h + pad_y) * scale))

        # 确保裁剪区域有效
        if right <= left or lower <= upper:
            logger.warning("模块 %s 裁剪区域无效: (%d,%d,%d,%d)", module_id, left, upper, right, lower)
            continue

        crop = img.crop((left, upper, right, lower))
        crop_path = str(out_dir / f"module_{module_id}.png")
        crop.save(crop_path)
        crops[module_id] = crop_path

    return crops


def _crop_connection_regions(
    full_image_path: str,
    metadata: RenderMetadata,
    output_dir: str,
    dpi: int = 150,
) -> list[str]:
    """裁剪模块间连接区域。

    对每对相邻模块，裁剪它们之间的区域。

    Args:
        full_image_path: 完整 PNG 路径
        metadata: 渲染元数据
        output_dir: 裁剪图输出目录
        dpi: 渲染 DPI

    Returns:
        连接区域裁剪图路径列表
    """
    if not _HAS_PIL:
        return []

    if len(metadata.module_bboxes) < 2:
        return []

    img = Image.open(full_image_path)
    img_w, img_h = img.size
    scale = dpi / 72.0
    paths: list[str] = []

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # 取所有模块的 bbox，按 X 排序，裁剪相邻模块之间的区域
    sorted_modules = sorted(metadata.module_bboxes.items(), key=lambda kv: kv[1][0])

    for i in range(len(sorted_modules) - 1):
        id_a, bbox_a = sorted_modules[i]
        id_b, bbox_b = sorted_modules[i + 1]

        # 连接区域：从 A 的右边到 B 的左边
        x_a, y_a, w_a, h_a = bbox_a
        x_b, y_b, _w_b, h_b = bbox_b

        left = max(0, int((x_a + w_a) * scale))
        right = min(img_w, int(x_b * scale))
        upper = max(0, int(min(y_a, y_b) * scale))
        lower = min(img_h, int(max(y_a + h_a, y_b + h_b) * scale))

        if right <= left or lower <= upper:
            continue

        crop = img.crop((left, upper, right, lower))
        crop_path = str(out_dir / f"conn_{id_a}_{id_b}.png")
        crop.save(crop_path)
        paths.append(crop_path)

    return paths


# ============================================================
# V026: render_review_images
# ============================================================


def render_review_images(
    bundle: SystemBundle,
    config: VisualReviewConfig | None = None,
) -> ReviewImageSet:
    """将系统设计渲染为 PNG 截图集合供 AI 审稿使用。

    1. 从 bundle.design_ir 重新渲染全图 PNG（标准 DPI）
    2. 渲染高清 PNG（HD DPI）
    3. 按 render_metadata.module_bboxes 裁剪模块区域
    4. 裁剪模块间连接区域
    5. 返回 ReviewImageSet

    Args:
        bundle: 系统设计产物包
        config: 审稿配置（含 DPI 设置），None 则使用默认值

    Returns:
        ReviewImageSet 包含所有截图路径
    """
    if config is None:
        config = VisualReviewConfig()

    ir = bundle.design_ir
    std_dpi = config.image_dpi
    hd_dpi = config.hd_dpi

    # 输出目录
    review_dir = str(Path(output_path("visual_review")).parent / "visual_review")
    Path(review_dir).mkdir(parents=True, exist_ok=True)

    # 渲染标准 DPI 全图
    full_path = str(Path(review_dir) / "full_review.png")
    _render_png_from_ir(ir, full_path, dpi=std_dpi)

    # 渲染高清全图
    hd_path = str(Path(review_dir) / "full_review_hd.png")
    _render_png_from_ir(ir, hd_path, dpi=hd_dpi)

    # 裁剪模块区域
    module_crops = _crop_module_regions(
        full_path, bundle.render_metadata, review_dir, dpi=std_dpi,
    )

    # 裁剪连接区域
    connection_crops = _crop_connection_regions(
        full_path, bundle.render_metadata, review_dir, dpi=std_dpi,
    )

    result = ReviewImageSet(
        full_image_path=full_path,
        full_image_hd_path=hd_path,
        module_crops=module_crops,
        connection_crops=connection_crops,
        dpi=std_dpi,
    )

    logger.info(
        "截图生成完成: full=%s, hd=%s, modules=%d, connections=%d",
        full_path, hd_path, len(module_crops), len(connection_crops),
    )
    return result


# ============================================================
# V027-V030: build_review_manifest
# ============================================================


def build_review_manifest(
    ir: SystemDesignIR,
    metadata: RenderMetadata | None = None,
) -> ReviewManifest:
    """从 SystemDesignIR 构建审稿清单。

    清单包含模块列表、连接关系、元件统计等信息，
    作为文本上下文提供给 AI 审稿员。

    Args:
        ir: 系统设计中间表示
        metadata: 可选渲染元数据

    Returns:
        ReviewManifest 审稿素材清单
    """
    # 模块列表
    module_list: list[dict[str, str]] = []
    for module_id, instance in ir.module_instances.items():
        device_name = ""
        if instance.device is not None:
            device_name = getattr(instance.device, "part_number", "")
        module_list.append({
            "module_id": module_id,
            "device": device_name or module_id,
            "role": instance.role,
            "category": instance.resolved_category,
            "status": instance.status.value,
        })

    # 连接列表
    connection_list: list[dict[str, str]] = []
    for conn in ir.connections:
        connection_list.append({
            "from": f"{conn.src_port.module_id}.{conn.src_port.pin_name}",
            "to": f"{conn.dst_port.module_id}.{conn.dst_port.pin_name}",
            "net": conn.net_name,
            "rule": conn.rule_id,
        })

    # 未解决项
    unresolved = [
        item.get("detail", item.get("type", "unknown"))
        for item in ir.unresolved_items
    ]

    # 元件总数
    total_components = sum(
        len(inst.external_components)
        for inst in ir.module_instances.values()
    ) + len(ir.module_instances)  # 加上 IC 本身

    # 画布尺寸（从 metadata 推断或使用默认值）
    canvas_size = (0.0, 0.0)
    if metadata and metadata.module_bboxes:
        max_x = max(
            x + w for x, y, w, h in metadata.module_bboxes.values()
        )
        max_y = max(
            y + h for x, y, w, h in metadata.module_bboxes.values()
        )
        canvas_size = (max_x, max_y)

    return ReviewManifest(
        module_list=module_list,
        connection_list=connection_list,
        unresolved_items=unresolved,
        total_components=total_components,
        total_nets=len(ir.nets),
        canvas_size=canvas_size,
    )
