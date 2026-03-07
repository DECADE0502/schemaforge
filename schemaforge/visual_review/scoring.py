"""本地渲染质量评分（V041-V050）。

所有函数都是确定性的，不调用 AI。
基于 RenderMetadata 中的几何数据计算硬指标。
"""

from __future__ import annotations

from schemaforge.system.models import RenderMetadata, SystemDesignIR
from schemaforge.visual_review.models import RenderScore, VisualReviewReport


# ============================================================
# 几何工具
# ============================================================


def _bboxes_intersect(
    a: tuple[float, float, float, float],
    b: tuple[float, float, float, float],
) -> bool:
    """判断两个 (x, y, w, h) 矩形是否重叠。"""
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return not (
        ax + aw <= bx
        or bx + bw <= ax
        or ay + ah <= by
        or by + bh <= ay
    )


def _segments_cross(
    p1: tuple[float, float],
    p2: tuple[float, float],
    p3: tuple[float, float],
    p4: tuple[float, float],
) -> bool:
    """判断线段 (p1-p2) 与 (p3-p4) 是否交叉。

    使用叉积方向判定。
    """
    def _cross(o: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> float:
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    d1 = _cross(p3, p4, p1)
    d2 = _cross(p3, p4, p2)
    d3 = _cross(p1, p2, p3)
    d4 = _cross(p1, p2, p4)

    if ((d1 > 0 and d2 < 0) or (d1 < 0 and d2 > 0)) and (
        (d3 > 0 and d4 < 0) or (d3 < 0 and d4 > 0)
    ):
        return True
    return False


# ============================================================
# V041: 模块可见性
# ============================================================


def check_all_modules_visible(metadata: RenderMetadata, ir: SystemDesignIR) -> float:
    """1.0 if all modules have bboxes, 0.0 if any missing."""
    if not ir.module_instances:
        return 1.0
    total = len(ir.module_instances)
    visible = sum(1 for mid in ir.module_instances if mid in metadata.module_bboxes)
    return visible / total


# ============================================================
# V042: 标签重叠
# ============================================================


def check_label_overlap(metadata: RenderMetadata) -> float:
    """1.0 if no overlaps, decrease per overlap. Use bbox intersection."""
    labels = list(metadata.label_bboxes.values())
    if len(labels) <= 1:
        return 1.0
    overlap_count = 0
    for i in range(len(labels)):
        for j in range(i + 1, len(labels)):
            if _bboxes_intersect(labels[i], labels[j]):
                overlap_count += 1
    max_pairs = len(labels) * (len(labels) - 1) // 2
    if max_pairs == 0:
        return 1.0
    return max(0.0, 1.0 - overlap_count / max_pairs)


# ============================================================
# V043: 标签溢出
# ============================================================


def check_label_overflow(
    metadata: RenderMetadata,
    canvas_size: tuple[float, float] | None = None,
) -> float:
    """1.0 if all labels within canvas."""
    cw, ch = canvas_size or metadata.canvas_size
    if not metadata.label_bboxes:
        return 1.0
    total = len(metadata.label_bboxes)
    overflow_count = 0
    for _lid, (lx, ly, lw, lh) in metadata.label_bboxes.items():
        if lx < 0 or ly < 0 or lx + lw > cw or ly + lh > ch:
            overflow_count += 1
    return max(0.0, 1.0 - overflow_count / total)


# ============================================================
# V044: 模块重叠
# ============================================================


def check_module_overlap(metadata: RenderMetadata) -> float:
    """1.0 if no module bboxes intersect."""
    bboxes = list(metadata.module_bboxes.values())
    if len(bboxes) <= 1:
        return 1.0
    overlap_count = 0
    for i in range(len(bboxes)):
        for j in range(i + 1, len(bboxes)):
            if _bboxes_intersect(bboxes[i], bboxes[j]):
                overlap_count += 1
    max_pairs = len(bboxes) * (len(bboxes) - 1) // 2
    if max_pairs == 0:
        return 1.0
    return max(0.0, 1.0 - overlap_count / max_pairs)


# ============================================================
# V045: 连线交叉
# ============================================================


def count_line_crossings(metadata: RenderMetadata) -> float:
    """Negative penalty per crossing. 0 = no crossings."""
    if len(metadata.wire_paths) <= 1:
        return 0.0

    # 把每条路径拆成线段列表
    all_segments: list[tuple[tuple[float, float], tuple[float, float], int]] = []
    for wire_idx, (_src, _dst, points) in enumerate(metadata.wire_paths):
        for k in range(len(points) - 1):
            all_segments.append((points[k], points[k + 1], wire_idx))

    crossing_count = 0
    for i in range(len(all_segments)):
        for j in range(i + 1, len(all_segments)):
            # 同一条路径的相邻线段不算交叉
            if all_segments[i][2] == all_segments[j][2]:
                continue
            if _segments_cross(
                all_segments[i][0],
                all_segments[i][1],
                all_segments[j][0],
                all_segments[j][1],
            ):
                crossing_count += 1

    return -0.5 * crossing_count


# ============================================================
# V046: 最小间距
# ============================================================


def check_min_spacing(metadata: RenderMetadata, min_gap: float = 1.0) -> float:
    """1.0 if all modules have sufficient spacing."""
    bboxes = list(metadata.module_bboxes.items())
    if len(bboxes) <= 1:
        return 1.0

    violation_count = 0
    total_pairs = 0
    for i in range(len(bboxes)):
        for j in range(i + 1, len(bboxes)):
            total_pairs += 1
            _id_a, (ax, ay, aw, ah) = bboxes[i]
            _id_b, (bx, by, bw, bh) = bboxes[j]
            # 计算两个矩形之间的最小水平和垂直间隙
            h_gap = max(0.0, max(bx - (ax + aw), ax - (bx + bw)))
            v_gap = max(0.0, max(by - (ay + ah), ay - (by + bh)))
            gap = max(h_gap, v_gap)
            # 如果矩形重叠则 gap=0
            if _bboxes_intersect(bboxes[i][1], bboxes[j][1]):
                gap = 0.0
            if gap < min_gap:
                violation_count += 1

    if total_pairs == 0:
        return 1.0
    return max(0.0, 1.0 - violation_count / total_pairs)


# ============================================================
# V047: 连接可见性
# ============================================================


def check_connections_visible(metadata: RenderMetadata, ir: SystemDesignIR) -> float:
    """1.0 if all connections have visible wire paths."""
    if not ir.connections:
        return 1.0

    # 构建已有路径的 (src, dst) 集合
    wired_pairs: set[tuple[str, str]] = set()
    for src, dst, _pts in metadata.wire_paths:
        wired_pairs.add((src, dst))
        wired_pairs.add((dst, src))  # 双向查找

    total = len(ir.connections)
    visible = 0
    for conn in ir.connections:
        src_mod = conn.src_port.module_id
        dst_mod = conn.dst_port.module_id
        if (src_mod, dst_mod) in wired_pairs:
            visible += 1

    return visible / total


# ============================================================
# V050: 综合评分
# ============================================================


def score_render_quality(
    metadata: RenderMetadata,
    ir: SystemDesignIR,
    ai_report: VisualReviewReport | None = None,
) -> RenderScore:
    """Compute local hard metrics + combine with AI soft score."""
    score = RenderScore(
        all_modules_visible=check_all_modules_visible(metadata, ir),
        no_label_overlap=check_label_overlap(metadata),
        no_label_overflow=check_label_overflow(metadata),
        no_module_overlap=check_module_overlap(metadata),
        min_spacing_ok=check_min_spacing(metadata),
        connections_visible=check_connections_visible(metadata, ir),
        crossing_penalty=count_line_crossings(metadata),
    )
    if ai_report is not None:
        score.ai_score = ai_report.overall_score
        score.ai_confidence = 1.0 if ai_report.overall_score > 0 else 0.0
    return score
