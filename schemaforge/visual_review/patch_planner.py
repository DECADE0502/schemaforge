"""视觉补丁规划器（V051-V060）。

将 AI 审稿报告中的 VisualIssue 转换为可执行的 LayoutPatchAction。
所有逻辑都是确定性的，不调用 AI。
禁止动作（FORBIDDEN_ACTIONS）会被拒绝并记录到 rejected_actions。
"""

from __future__ import annotations

from schemaforge.system.models import RenderMetadata, SystemDesignIR
from schemaforge.visual_review.models import (
    FORBIDDEN_ACTIONS,
    LayoutPatchAction,
    LayoutPatchPlan,
    PatchActionType,
    VisualIssue,
    VisualReviewReport,
)


# ============================================================
# 关键字 → PatchActionType 映射
# ============================================================

_FIX_KEYWORD_MAP: dict[str, PatchActionType] = {
    "increase_spacing": PatchActionType.INCREASE_MODULE_SPACING,
    "spacing": PatchActionType.INCREASE_MODULE_SPACING,
    "spread": PatchActionType.INCREASE_MODULE_SPACING,
    "move_module": PatchActionType.MOVE_MODULE,
    "move module": PatchActionType.MOVE_MODULE,
    "reposition": PatchActionType.MOVE_MODULE,
    "move_label": PatchActionType.MOVE_LABEL,
    "move label": PatchActionType.MOVE_LABEL,
    "label": PatchActionType.MOVE_LABEL,
    "expand_canvas": PatchActionType.EXPAND_CANVAS,
    "expand canvas": PatchActionType.EXPAND_CANVAS,
    "enlarge": PatchActionType.EXPAND_CANVAS,
    "reroute": PatchActionType.REROUTE_CONNECTION,
    "reroute_connection": PatchActionType.REROUTE_CONNECTION,
    "add_net_label": PatchActionType.ADD_NET_LABEL,
    "adjust_font": PatchActionType.ADJUST_FONT_SIZE,
    "font_size": PatchActionType.ADJUST_FONT_SIZE,
}


def _classify_fix(suggested_fix: str) -> PatchActionType | None:
    """从 suggested_fix 文本推断 PatchActionType。"""
    lower = suggested_fix.lower().strip()

    # 先检查是否包含禁止动作关键字
    for forbidden in FORBIDDEN_ACTIONS:
        if forbidden in lower:
            return None

    # 精确匹配
    try:
        return PatchActionType(lower)
    except ValueError:
        pass

    # 关键字匹配
    for keyword, action_type in _FIX_KEYWORD_MAP.items():
        if keyword in lower:
            return action_type

    # 按 category 推断
    return None


def _is_forbidden(suggested_fix: str) -> bool:
    """检查修复建议是否包含禁止动作。"""
    lower = suggested_fix.lower().strip()
    for forbidden in FORBIDDEN_ACTIONS:
        if forbidden in lower:
            return True
    return False


# ============================================================
# 单动作规划器
# ============================================================


def _plan_increase_spacing(
    issue: VisualIssue,
    metadata: RenderMetadata,
) -> LayoutPatchAction | None:
    """规划增大模块间距动作。"""
    targets = issue.affected_elements
    if len(targets) < 2:
        return None

    # 找到两个模块的中心点，计算扩散方向
    mod_a = targets[0]
    mod_b = targets[1]
    bbox_a = metadata.module_bboxes.get(mod_a)
    bbox_b = metadata.module_bboxes.get(mod_b)
    if not bbox_a or not bbox_b:
        return None

    cx_a = bbox_a[0] + bbox_a[2] / 2
    cy_a = bbox_a[1] + bbox_a[3] / 2
    cx_b = bbox_b[0] + bbox_b[2] / 2
    cy_b = bbox_b[1] + bbox_b[3] / 2

    dx = cx_b - cx_a
    dy = cy_b - cy_a
    dist = (dx**2 + dy**2) ** 0.5
    if dist < 0.01:
        # 完全重叠，向右推
        dx, dy = 2.0, 0.0
    else:
        # 标准化后乘以推移量
        push = 1.5
        dx = (dx / dist) * push
        dy = (dy / dist) * push

    return LayoutPatchAction(
        action_type=PatchActionType.INCREASE_MODULE_SPACING,
        target=mod_b,
        parameters={"dx": round(dx, 2), "dy": round(dy, 2)},
        reason=f"增大 {mod_a} 与 {mod_b} 间距",
        source_issue_id=issue.issue_id,
    )


def _plan_move_module(
    issue: VisualIssue,
    metadata: RenderMetadata,
) -> LayoutPatchAction | None:
    """规划移动模块动作。"""
    if not issue.affected_elements:
        return None
    target = issue.affected_elements[0]
    if target not in metadata.module_bboxes:
        return None

    # 默认向右下方移动 2.0 单位（简单启发式）
    dx = 2.0
    dy = 2.0
    if issue.location != (0.0, 0.0):
        bbox = metadata.module_bboxes[target]
        cx = bbox[0] + bbox[2] / 2
        cy = bbox[1] + bbox[3] / 2
        # 远离问题位置
        vx = cx - issue.location[0]
        vy = cy - issue.location[1]
        dist = (vx**2 + vy**2) ** 0.5
        if dist > 0.01:
            dx = (vx / dist) * 2.0
            dy = (vy / dist) * 2.0

    return LayoutPatchAction(
        action_type=PatchActionType.MOVE_MODULE,
        target=target,
        parameters={"dx": round(dx, 2), "dy": round(dy, 2)},
        reason=f"移动模块 {target} 解决 {issue.category} 问题",
        source_issue_id=issue.issue_id,
    )


def _plan_move_label(
    issue: VisualIssue,
    metadata: RenderMetadata,
) -> LayoutPatchAction | None:
    """规划移动标签动作。"""
    if not issue.affected_elements:
        return None
    target = issue.affected_elements[0]
    if target not in metadata.label_bboxes:
        # 也接受 module_id 作为标签目标
        pass

    return LayoutPatchAction(
        action_type=PatchActionType.MOVE_LABEL,
        target=target,
        parameters={"dx": 1.0, "dy": -0.5},
        reason=f"移动标签 {target} 避免遮挡",
        source_issue_id=issue.issue_id,
    )


def _plan_expand_canvas(
    issue: VisualIssue,
    metadata: RenderMetadata,
) -> LayoutPatchAction | None:
    """规划扩大画布动作。"""
    cw, ch = metadata.canvas_size
    return LayoutPatchAction(
        action_type=PatchActionType.EXPAND_CANVAS,
        target="canvas",
        parameters={
            "new_width": round(cw * 1.3, 1),
            "new_height": round(ch * 1.3, 1),
        },
        reason="扩大画布以容纳所有元素",
        source_issue_id=issue.issue_id,
    )


# ============================================================
# 动作类型 → 规划函数分发
# ============================================================

_PLANNER_DISPATCH: dict[PatchActionType, object] = {
    PatchActionType.INCREASE_MODULE_SPACING: _plan_increase_spacing,
    PatchActionType.MOVE_MODULE: _plan_move_module,
    PatchActionType.MOVE_LABEL: _plan_move_label,
    PatchActionType.EXPAND_CANVAS: _plan_expand_canvas,
}


# ============================================================
# V051: 主规划入口
# ============================================================


def plan_visual_patches(
    report: VisualReviewReport,
    metadata: RenderMetadata,
    ir: SystemDesignIR,
) -> LayoutPatchPlan:
    """Convert AI review issues into executable layout patches.

    For each VisualIssue:
    1. Map suggested_fix to PatchActionType
    2. Check against whitelist (reject if not in PatchActionType enum)
    3. Calculate patch parameters (how much to move, which direction)
    4. Return LayoutPatchPlan with actions + rejected list
    """
    plan = LayoutPatchPlan()

    for issue in report.issues:
        fix_text = issue.suggested_fix

        # 检查禁止动作 (VC04, VC09)
        if _is_forbidden(fix_text):
            plan.rejected_actions.append({
                "action": fix_text,
                "reason": "在禁止动作黑名单中",
                "issue_id": issue.issue_id,
            })
            continue

        # 分类修复建议
        action_type = _classify_fix(fix_text)
        if action_type is None:
            # 无法识别的修复建议——根据 category 推断默认动作
            action_type = _infer_action_from_category(issue.category)
            if action_type is None:
                plan.rejected_actions.append({
                    "action": fix_text or "(empty)",
                    "reason": "无法映射到有效的 PatchActionType",
                    "issue_id": issue.issue_id,
                })
                continue

        # 调用对应的规划函数
        planner_fn = _PLANNER_DISPATCH.get(action_type)
        if planner_fn is None:
            # 已在白名单但无具体规划器（如 REROUTE_CONNECTION）
            plan.actions.append(LayoutPatchAction(
                action_type=action_type,
                target=issue.affected_elements[0] if issue.affected_elements else "",
                parameters={},
                reason=fix_text,
                source_issue_id=issue.issue_id,
            ))
            continue

        action = planner_fn(issue, metadata)
        if action is not None:
            plan.actions.append(action)

    return plan


def _infer_action_from_category(category: str) -> PatchActionType | None:
    """从 issue category 推断默认修复动作类型。"""
    mapping: dict[str, PatchActionType] = {
        "overlap": PatchActionType.INCREASE_MODULE_SPACING,
        "spacing": PatchActionType.INCREASE_MODULE_SPACING,
        "label": PatchActionType.MOVE_LABEL,
        "visibility": PatchActionType.MOVE_MODULE,
        "routing": PatchActionType.REROUTE_CONNECTION,
        "overflow": PatchActionType.EXPAND_CANVAS,
    }
    return mapping.get(category)
