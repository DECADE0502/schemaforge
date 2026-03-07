"""Tests for visual review scoring (V041-V050).

验证本地渲染质量评分：模块可见性、标签重叠、标签溢出、
模块重叠、连线交叉、最小间距、连接可见性、综合评分。
"""

from __future__ import annotations

from schemaforge.system.models import (
    ModuleInstance,
    ModuleStatus,
    NetType,
    PortRef,
    RenderMetadata,
    ResolvedConnection,
    SystemDesignIR,
    SystemDesignRequest,
)
from schemaforge.visual_review.models import VisualReviewReport
from schemaforge.visual_review.scoring import (
    check_all_modules_visible,
    check_connections_visible,
    check_label_overlap,
    check_label_overflow,
    check_min_spacing,
    check_module_overlap,
    count_line_crossings,
    score_render_quality,
)


# ============================================================
# Helpers
# ============================================================


def _make_request() -> SystemDesignRequest:
    return SystemDesignRequest(raw_text="test")


def _make_module(module_id: str) -> ModuleInstance:
    return ModuleInstance(
        module_id=module_id,
        role="test",
        resolved_category="buck",
        status=ModuleStatus.RESOLVED,
        resolved_ports={
            "VIN": PortRef(module_id=module_id, port_role="power_in", pin_name="VIN", net_class=NetType.POWER),
            "VOUT": PortRef(module_id=module_id, port_role="power_out", pin_name="VOUT", net_class=NetType.POWER),
        },
    )


def _make_ir(module_ids: list[str], connections: list[ResolvedConnection] | None = None) -> SystemDesignIR:
    modules = {mid: _make_module(mid) for mid in module_ids}
    return SystemDesignIR(
        request=_make_request(),
        module_instances=modules,
        connections=connections or [],
    )


def _make_connection(src: str, dst: str) -> ResolvedConnection:
    return ResolvedConnection(
        resolved_connection_id=f"conn_{src}_{dst}",
        src_port=PortRef(module_id=src, port_role="power_out", pin_name="VOUT", net_class=NetType.POWER),
        dst_port=PortRef(module_id=dst, port_role="power_in", pin_name="VIN", net_class=NetType.POWER),
        net_name="NET_5V",
    )


# ============================================================
# V041: check_all_modules_visible
# ============================================================


class TestAllModulesVisible:
    """V041: 模块可见性评分。"""

    def test_all_visible(self) -> None:
        """所有模块都有 bbox → 1.0。"""
        ir = _make_ir(["buck1", "ldo1"])
        meta = RenderMetadata(
            module_bboxes={"buck1": (0, 0, 5, 3), "ldo1": (8, 0, 5, 3)},
        )
        assert check_all_modules_visible(meta, ir) == 1.0

    def test_one_missing(self) -> None:
        """一个模块无 bbox → 0.5。"""
        ir = _make_ir(["buck1", "ldo1"])
        meta = RenderMetadata(
            module_bboxes={"buck1": (0, 0, 5, 3)},
        )
        assert check_all_modules_visible(meta, ir) == 0.5

    def test_empty_ir(self) -> None:
        """空 IR → 1.0（无模块等于全部可见）。"""
        ir = SystemDesignIR(request=_make_request())
        meta = RenderMetadata()
        assert check_all_modules_visible(meta, ir) == 1.0


# ============================================================
# V042: check_label_overlap
# ============================================================


class TestLabelOverlap:
    """V042: 标签重叠评分。"""

    def test_no_overlap(self) -> None:
        """标签不重叠 → 1.0。"""
        meta = RenderMetadata(
            label_bboxes={
                "L1": (0, 0, 2, 1),
                "L2": (5, 0, 2, 1),
            },
        )
        assert check_label_overlap(meta) == 1.0

    def test_full_overlap(self) -> None:
        """两个标签完全重叠 → 0.0。"""
        meta = RenderMetadata(
            label_bboxes={
                "L1": (0, 0, 2, 1),
                "L2": (0, 0, 2, 1),
            },
        )
        assert check_label_overlap(meta) == 0.0

    def test_single_label(self) -> None:
        """单个标签 → 1.0。"""
        meta = RenderMetadata(label_bboxes={"L1": (0, 0, 2, 1)})
        assert check_label_overlap(meta) == 1.0


# ============================================================
# V043: check_label_overflow
# ============================================================


class TestLabelOverflow:
    """V043: 标签溢出评分。"""

    def test_all_inside(self) -> None:
        """所有标签在画布内 → 1.0。"""
        meta = RenderMetadata(
            label_bboxes={"L1": (1, 1, 2, 1)},
            canvas_size=(20.0, 15.0),
        )
        assert check_label_overflow(meta) == 1.0

    def test_one_outside(self) -> None:
        """一个标签溢出 → < 1.0。"""
        meta = RenderMetadata(
            label_bboxes={
                "L1": (1, 1, 2, 1),
                "L2": (19, 14, 3, 3),  # 超出 20x15 画布
            },
            canvas_size=(20.0, 15.0),
        )
        score = check_label_overflow(meta)
        assert score == 0.5

    def test_custom_canvas(self) -> None:
        """自定义画布大小。"""
        meta = RenderMetadata(
            label_bboxes={"L1": (9, 0, 2, 1)},
            canvas_size=(20.0, 15.0),
        )
        # 标签在 (9, 0)-(11, 1), 画布 10x10 → 溢出
        assert check_label_overflow(meta, canvas_size=(10.0, 10.0)) == 0.0


# ============================================================
# V044: check_module_overlap
# ============================================================


class TestModuleOverlap:
    """V044: 模块重叠评分。"""

    def test_no_overlap(self) -> None:
        """模块不重叠 → 1.0。"""
        meta = RenderMetadata(
            module_bboxes={
                "buck1": (0, 0, 4, 3),
                "ldo1": (8, 0, 4, 3),
            },
        )
        assert check_module_overlap(meta) == 1.0

    def test_overlap(self) -> None:
        """两个模块重叠 → 0.0。"""
        meta = RenderMetadata(
            module_bboxes={
                "buck1": (0, 0, 5, 3),
                "ldo1": (3, 0, 5, 3),  # 与 buck1 水平重叠
            },
        )
        assert check_module_overlap(meta) == 0.0


# ============================================================
# V045: count_line_crossings
# ============================================================


class TestLineCrossings:
    """V045: 连线交叉惩罚。"""

    def test_no_crossings(self) -> None:
        """平行线无交叉 → 0.0 惩罚。"""
        meta = RenderMetadata(
            wire_paths=[
                ("a", "b", [(0, 0), (10, 0)]),
                ("c", "d", [(0, 2), (10, 2)]),
            ],
        )
        assert count_line_crossings(meta) == 0.0

    def test_one_crossing(self) -> None:
        """X 形交叉 → -0.5 惩罚。"""
        meta = RenderMetadata(
            wire_paths=[
                ("a", "b", [(0, 0), (10, 10)]),
                ("c", "d", [(0, 10), (10, 0)]),
            ],
        )
        assert count_line_crossings(meta) == -0.5

    def test_single_wire(self) -> None:
        """单条线 → 0.0。"""
        meta = RenderMetadata(
            wire_paths=[("a", "b", [(0, 0), (10, 0)])],
        )
        assert count_line_crossings(meta) == 0.0


# ============================================================
# V046: check_min_spacing
# ============================================================


class TestMinSpacing:
    """V046: 最小间距评分。"""

    def test_sufficient_spacing(self) -> None:
        """间距足够 → 1.0。"""
        meta = RenderMetadata(
            module_bboxes={
                "buck1": (0, 0, 3, 2),
                "ldo1": (6, 0, 3, 2),  # gap = 3 > 1.0
            },
        )
        assert check_min_spacing(meta, min_gap=1.0) == 1.0

    def test_insufficient_spacing(self) -> None:
        """间距不足 → < 1.0。"""
        meta = RenderMetadata(
            module_bboxes={
                "buck1": (0, 0, 3, 2),
                "ldo1": (3.5, 0, 3, 2),  # gap = 0.5 < 1.0
            },
        )
        assert check_min_spacing(meta, min_gap=1.0) == 0.0


# ============================================================
# V047: check_connections_visible
# ============================================================


class TestConnectionsVisible:
    """V047: 连接可见性评分。"""

    def test_all_visible(self) -> None:
        """所有连接都有路径 → 1.0。"""
        conn = _make_connection("buck1", "ldo1")
        ir = _make_ir(["buck1", "ldo1"], connections=[conn])
        meta = RenderMetadata(
            wire_paths=[("buck1", "ldo1", [(5, 0), (8, 0)])],
        )
        assert check_connections_visible(meta, ir) == 1.0

    def test_missing_wire(self) -> None:
        """缺少路径 → 0.0。"""
        conn = _make_connection("buck1", "ldo1")
        ir = _make_ir(["buck1", "ldo1"], connections=[conn])
        meta = RenderMetadata(wire_paths=[])
        assert check_connections_visible(meta, ir) == 0.0

    def test_no_connections(self) -> None:
        """无连接 → 1.0。"""
        ir = _make_ir(["buck1"])
        meta = RenderMetadata()
        assert check_connections_visible(meta, ir) == 1.0


# ============================================================
# V050: score_render_quality
# ============================================================


class TestScoreRenderQuality:
    """V050: 综合评分。"""

    def test_perfect_layout(self) -> None:
        """完美布局 → 所有硬指标 1.0，local_score = 10.0。"""
        ir = _make_ir(["buck1", "ldo1"])
        meta = RenderMetadata(
            module_bboxes={"buck1": (0, 0, 4, 3), "ldo1": (8, 0, 4, 3)},
            label_bboxes={"L1": (1, 4, 2, 1), "L2": (9, 4, 2, 1)},
            wire_paths=[("buck1", "ldo1", [(4, 1), (8, 1)])],
            canvas_size=(20.0, 15.0),
        )
        ir.connections = [_make_connection("buck1", "ldo1")]

        score = score_render_quality(meta, ir)

        assert score.all_modules_visible == 1.0
        assert score.no_label_overlap == 1.0
        assert score.no_module_overlap == 1.0
        assert score.min_spacing_ok == 1.0
        assert score.connections_visible == 1.0
        assert score.crossing_penalty == 0.0
        assert score.local_score == 10.0

    def test_with_ai_report(self) -> None:
        """综合评分包含 AI 软指标。"""
        ir = _make_ir(["buck1"])
        meta = RenderMetadata(
            module_bboxes={"buck1": (0, 0, 4, 3)},
            canvas_size=(20.0, 15.0),
        )
        ai_report = VisualReviewReport(overall_score=8.0)

        score = score_render_quality(meta, ir, ai_report=ai_report)
        assert score.ai_score == 8.0
        assert score.combined_score > score.local_score * 0.7

    def test_degraded_layout(self) -> None:
        """有问题的布局 → local_score < 10.0。"""
        ir = _make_ir(["buck1", "ldo1"])
        meta = RenderMetadata(
            module_bboxes={
                "buck1": (0, 0, 5, 3),
                "ldo1": (3, 0, 5, 3),  # 重叠
            },
            label_bboxes={
                "L1": (0, 0, 2, 1),
                "L2": (0, 0, 2, 1),  # 标签重叠
            },
            canvas_size=(20.0, 15.0),
        )
        score = score_render_quality(meta, ir)
        assert score.local_score < 10.0
        assert score.no_module_overlap == 0.0
        assert score.no_label_overlap == 0.0
