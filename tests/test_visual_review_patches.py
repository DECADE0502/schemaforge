"""Tests for visual review patch planner + executor (V051-V070).

验证补丁规划器和执行器：禁止动作拒绝、有效动作生成、
布局状态修改、IR 不被篡改、差异记录。
"""

from __future__ import annotations

from copy import deepcopy

from schemaforge.system.models import (
    ModuleInstance,
    ModuleStatus,
    NetType,
    PortRef,
    RenderMetadata,
    SystemDesignIR,
    SystemDesignRequest,
)
from schemaforge.visual_review.models import (
    FORBIDDEN_ACTIONS,
    IssueSeverity,
    LayoutPatchAction,
    LayoutPatchPlan,
    PatchActionType,
    VisualIssue,
    VisualReviewReport,
)
from schemaforge.visual_review.patch_executor import (
    LayoutState,
    apply_visual_patches,
    create_layout_state_from_metadata,
    diff_layout_states,
)
from schemaforge.visual_review.patch_planner import (
    plan_visual_patches,
)


# ============================================================
# Helpers
# ============================================================


def _make_request() -> SystemDesignRequest:
    return SystemDesignRequest(raw_text="test")


def _make_ir() -> SystemDesignIR:
    return SystemDesignIR(
        request=_make_request(),
        module_instances={
            "buck1": ModuleInstance(
                module_id="buck1",
                role="降压",
                resolved_category="buck",
                status=ModuleStatus.RESOLVED,
                resolved_ports={
                    "VIN": PortRef(module_id="buck1", port_role="power_in", pin_name="VIN", net_class=NetType.POWER),
                    "VOUT": PortRef(module_id="buck1", port_role="power_out", pin_name="VOUT", net_class=NetType.POWER),
                },
            ),
            "ldo1": ModuleInstance(
                module_id="ldo1",
                role="稳压",
                resolved_category="ldo",
                status=ModuleStatus.RESOLVED,
                resolved_ports={
                    "VIN": PortRef(module_id="ldo1", port_role="power_in", pin_name="VIN", net_class=NetType.POWER),
                    "VOUT": PortRef(module_id="ldo1", port_role="power_out", pin_name="VOUT", net_class=NetType.POWER),
                },
            ),
        },
    )


def _make_metadata() -> RenderMetadata:
    return RenderMetadata(
        module_bboxes={
            "buck1": (0.0, 0.0, 5.0, 3.0),
            "ldo1": (8.0, 0.0, 5.0, 3.0),
        },
        label_bboxes={
            "L_buck1": (1.0, 3.5, 3.0, 1.0),
            "L_ldo1": (9.0, 3.5, 3.0, 1.0),
        },
        canvas_size=(20.0, 15.0),
    )


def _make_issue(
    issue_id: str = "ISS01",
    category: str = "overlap",
    suggested_fix: str = "increase_spacing",
    affected: list[str] | None = None,
    severity: IssueSeverity = IssueSeverity.WARNING,
) -> VisualIssue:
    return VisualIssue(
        issue_id=issue_id,
        severity=severity,
        category=category,
        description="test issue",
        affected_elements=affected or ["buck1", "ldo1"],
        suggested_fix=suggested_fix,
    )


def _make_report(issues: list[VisualIssue] | None = None) -> VisualReviewReport:
    return VisualReviewReport(
        issues=issues or [],
        overall_score=5.0,
    )


# ============================================================
# V051: Patch planner — forbidden actions rejected (VC04, VC09)
# ============================================================


class TestPatchPlannerForbidden:
    """V051: 禁止动作必须被拒绝。"""

    def test_reject_add_component(self) -> None:
        """add_component 在黑名单中 → 被拒绝。"""
        issue = _make_issue(suggested_fix="add_component")
        report = _make_report([issue])
        meta = _make_metadata()
        ir = _make_ir()

        plan = plan_visual_patches(report, meta, ir)

        assert len(plan.actions) == 0
        assert len(plan.rejected_actions) == 1
        assert "禁止" in plan.rejected_actions[0]["reason"]

    def test_reject_all_forbidden_actions(self) -> None:
        """所有 FORBIDDEN_ACTIONS 都被拒绝。"""
        for forbidden in FORBIDDEN_ACTIONS:
            issue = _make_issue(issue_id=f"ISS_{forbidden}", suggested_fix=forbidden)
            report = _make_report([issue])
            meta = _make_metadata()
            ir = _make_ir()

            plan = plan_visual_patches(report, meta, ir)
            assert len(plan.actions) == 0, f"{forbidden} 应该被拒绝"
            assert len(plan.rejected_actions) == 1

    def test_reject_change_value(self) -> None:
        """change_value 在黑名单中 → 被拒绝。"""
        issue = _make_issue(suggested_fix="change_value")
        report = _make_report([issue])
        plan = plan_visual_patches(report, _make_metadata(), _make_ir())

        assert len(plan.actions) == 0
        assert len(plan.rejected_actions) == 1


# ============================================================
# V052: Patch planner — valid actions
# ============================================================


class TestPatchPlannerValid:
    """V052: 有效动作生成。"""

    def test_increase_spacing_action(self) -> None:
        """increase_spacing → 生成 INCREASE_MODULE_SPACING 动作。"""
        issue = _make_issue(suggested_fix="increase_spacing")
        report = _make_report([issue])

        plan = plan_visual_patches(report, _make_metadata(), _make_ir())

        assert len(plan.actions) == 1
        assert plan.actions[0].action_type == PatchActionType.INCREASE_MODULE_SPACING
        assert "dx" in plan.actions[0].parameters

    def test_move_module_action(self) -> None:
        """move_module → 生成 MOVE_MODULE 动作。"""
        issue = _make_issue(
            suggested_fix="move_module",
            affected=["buck1"],
        )
        report = _make_report([issue])

        plan = plan_visual_patches(report, _make_metadata(), _make_ir())

        assert len(plan.actions) == 1
        assert plan.actions[0].action_type == PatchActionType.MOVE_MODULE
        assert plan.actions[0].target == "buck1"

    def test_expand_canvas_action(self) -> None:
        """expand_canvas → 生成 EXPAND_CANVAS 动作。"""
        issue = _make_issue(suggested_fix="expand_canvas", category="overflow")
        report = _make_report([issue])

        plan = plan_visual_patches(report, _make_metadata(), _make_ir())

        assert len(plan.actions) == 1
        assert plan.actions[0].action_type == PatchActionType.EXPAND_CANVAS
        assert plan.actions[0].parameters["new_width"] > 20.0

    def test_move_label_action(self) -> None:
        """move_label → 生成 MOVE_LABEL 动作。"""
        issue = _make_issue(
            suggested_fix="move_label",
            affected=["L_buck1"],
            category="label",
        )
        report = _make_report([issue])

        plan = plan_visual_patches(report, _make_metadata(), _make_ir())

        assert len(plan.actions) == 1
        assert plan.actions[0].action_type == PatchActionType.MOVE_LABEL

    def test_category_inference_fallback(self) -> None:
        """无法从 suggested_fix 分类时，从 category 推断。"""
        issue = _make_issue(
            suggested_fix="please fix this somehow",
            category="spacing",
            affected=["buck1", "ldo1"],
        )
        report = _make_report([issue])

        plan = plan_visual_patches(report, _make_metadata(), _make_ir())

        # 应该从 category="spacing" 推断为 INCREASE_MODULE_SPACING
        assert len(plan.actions) == 1
        assert plan.actions[0].action_type == PatchActionType.INCREASE_MODULE_SPACING

    def test_empty_report(self) -> None:
        """空报告 → 空计划。"""
        report = _make_report([])
        plan = plan_visual_patches(report, _make_metadata(), _make_ir())
        assert len(plan.actions) == 0
        assert len(plan.rejected_actions) == 0


# ============================================================
# V061: Patch executor — layout state from metadata
# ============================================================


class TestLayoutStateFromMetadata:
    """V061: 从 RenderMetadata 初始化 LayoutState。"""

    def test_positions_extracted(self) -> None:
        """module_bboxes → module_positions。"""
        meta = _make_metadata()
        state = create_layout_state_from_metadata(meta)

        assert "buck1" in state.module_positions
        assert "ldo1" in state.module_positions
        assert state.module_positions["buck1"] == (0.0, 0.0)
        assert state.module_positions["ldo1"] == (8.0, 0.0)

    def test_canvas_size_copied(self) -> None:
        """canvas_size 正确复制。"""
        meta = _make_metadata()
        state = create_layout_state_from_metadata(meta)

        assert state.canvas_width == 20.0
        assert state.canvas_height == 15.0


# ============================================================
# V063-V067: Patch executor — apply patches
# ============================================================


class TestApplyPatches:
    """V063-V067: 补丁执行。"""

    def test_move_module_changes_position(self) -> None:
        """MOVE_MODULE 修改模块位置。"""
        state = LayoutState(module_positions={"buck1": (0.0, 0.0)})
        plan = LayoutPatchPlan(actions=[
            LayoutPatchAction(
                action_type=PatchActionType.MOVE_MODULE,
                target="buck1",
                parameters={"dx": 3.0, "dy": 1.5},
            ),
        ])

        new_state = apply_visual_patches(state, plan)

        assert new_state.module_positions["buck1"] == (3.0, 1.5)
        # 原始状态不变
        assert state.module_positions["buck1"] == (0.0, 0.0)

    def test_expand_canvas(self) -> None:
        """EXPAND_CANVAS 扩大画布。"""
        state = LayoutState(canvas_width=20.0, canvas_height=15.0)
        plan = LayoutPatchPlan(actions=[
            LayoutPatchAction(
                action_type=PatchActionType.EXPAND_CANVAS,
                target="canvas",
                parameters={"new_width": 30.0, "new_height": 20.0},
            ),
        ])

        new_state = apply_visual_patches(state, plan)

        assert new_state.canvas_width == 30.0
        assert new_state.canvas_height == 20.0

    def test_move_label_changes_offset(self) -> None:
        """MOVE_LABEL 修改标签偏移。"""
        state = LayoutState(label_offsets={"L1": (0.0, 0.0)})
        plan = LayoutPatchPlan(actions=[
            LayoutPatchAction(
                action_type=PatchActionType.MOVE_LABEL,
                target="L1",
                parameters={"dx": 1.0, "dy": -0.5},
            ),
        ])

        new_state = apply_visual_patches(state, plan)

        assert new_state.label_offsets["L1"] == (1.0, -0.5)

    def test_does_not_modify_ir(self) -> None:
        """补丁执行不修改 SystemDesignIR (VC05)。"""
        ir = _make_ir()
        ir_copy = deepcopy(ir)

        meta = _make_metadata()
        state = create_layout_state_from_metadata(meta)

        plan = LayoutPatchPlan(actions=[
            LayoutPatchAction(
                action_type=PatchActionType.MOVE_MODULE,
                target="buck1",
                parameters={"dx": 5.0, "dy": 0.0},
            ),
        ])

        apply_visual_patches(state, plan)

        # IR 未被修改
        assert ir.module_instances.keys() == ir_copy.module_instances.keys()
        for mid in ir.module_instances:
            assert ir.module_instances[mid].status == ir_copy.module_instances[mid].status
            assert ir.module_instances[mid].parameters == ir_copy.module_instances[mid].parameters

    def test_increase_spacing_updates_scale(self) -> None:
        """INCREASE_MODULE_SPACING 更新 spacing_scale。"""
        state = LayoutState(
            module_positions={"ldo1": (8.0, 0.0)},
            module_spacing_scale=1.0,
        )
        plan = LayoutPatchPlan(actions=[
            LayoutPatchAction(
                action_type=PatchActionType.INCREASE_MODULE_SPACING,
                target="ldo1",
                parameters={"dx": 2.0, "dy": 0.0},
            ),
        ])

        new_state = apply_visual_patches(state, plan)

        assert new_state.module_positions["ldo1"] == (10.0, 0.0)
        assert new_state.module_spacing_scale > 1.0


# ============================================================
# V070: diff_layout_states
# ============================================================


class TestDiffLayoutStates:
    """V070: 布局差异记录。"""

    def test_diff_records_module_move(self) -> None:
        """模块移动被记录。"""
        before = LayoutState(module_positions={"buck1": (0.0, 0.0)})
        after = LayoutState(module_positions={"buck1": (3.0, 1.5)})

        diff = diff_layout_states(before, after)

        assert len(diff["moved_modules"]) == 1
        assert diff["moved_modules"][0]["module_id"] == "buck1"
        assert diff["moved_modules"][0]["before"] == (0.0, 0.0)
        assert diff["moved_modules"][0]["after"] == (3.0, 1.5)

    def test_diff_records_canvas_change(self) -> None:
        """画布变化被记录。"""
        before = LayoutState(canvas_width=20.0, canvas_height=15.0)
        after = LayoutState(canvas_width=30.0, canvas_height=20.0)

        diff = diff_layout_states(before, after)

        assert len(diff["canvas_changed"]) == 1
        assert diff["canvas_changed"][0]["after"] == (30.0, 20.0)

    def test_diff_empty_for_identical_states(self) -> None:
        """相同状态 → 空差异。"""
        state = LayoutState(
            module_positions={"buck1": (0.0, 0.0)},
            label_offsets={"L1": (0.0, 0.0)},
        )
        same = deepcopy(state)

        diff = diff_layout_states(state, same)

        assert len(diff["moved_modules"]) == 0
        assert len(diff["moved_labels"]) == 0
        assert len(diff["canvas_changed"]) == 0
        assert len(diff["spacing_changed"]) == 0

    def test_diff_records_label_move(self) -> None:
        """标签偏移变化被记录。"""
        before = LayoutState(label_offsets={"L1": (0.0, 0.0)})
        after = LayoutState(label_offsets={"L1": (1.0, -0.5)})

        diff = diff_layout_states(before, after)

        assert len(diff["moved_labels"]) == 1
        assert diff["moved_labels"][0]["label_id"] == "L1"
