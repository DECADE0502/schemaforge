"""视觉补丁执行器（V061-V070）。

将 LayoutPatchPlan 应用到 LayoutState。
所有操作仅修改布局状态，绝不修改 SystemDesignIR（VC05）。
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field

from schemaforge.system.models import RenderMetadata
from schemaforge.visual_review.models import (
    LayoutPatchPlan,
    PatchActionType,
)


# ============================================================
# V061: LayoutState
# ============================================================


@dataclass
class LayoutState:
    """Mutable layout state that patches modify. Does NOT touch electrical IR."""

    module_positions: dict[str, tuple[float, float]] = field(default_factory=dict)
    # module_id → (x, y)
    label_offsets: dict[str, tuple[float, float]] = field(default_factory=dict)
    # label_id → (dx, dy)
    canvas_width: float = 20.0
    canvas_height: float = 15.0
    module_spacing_scale: float = 1.0


# ============================================================
# V062: 从 RenderMetadata 初始化
# ============================================================


def create_layout_state_from_metadata(metadata: RenderMetadata) -> LayoutState:
    """Initialize layout state from render metadata."""
    state = LayoutState()

    # 从 module_bboxes 提取位置 (使用 bbox 的 x, y 作为位置)
    for mid, (x, y, _w, _h) in metadata.module_bboxes.items():
        state.module_positions[mid] = (x, y)

    # 从 label_bboxes 提取偏移（相对位置存储为 0,0 初始偏移）
    for lid in metadata.label_bboxes:
        state.label_offsets[lid] = (0.0, 0.0)

    state.canvas_width = metadata.canvas_size[0]
    state.canvas_height = metadata.canvas_size[1]

    return state


# ============================================================
# V063-V067: 单动作执行器
# ============================================================


def _apply_increase_spacing(state: LayoutState, target: str, params: dict) -> None:
    """增大模块间距：移动目标模块。"""
    if target not in state.module_positions:
        return
    x, y = state.module_positions[target]
    dx = float(params.get("dx", 0.0))
    dy = float(params.get("dy", 0.0))
    state.module_positions[target] = (round(x + dx, 4), round(y + dy, 4))
    state.module_spacing_scale = round(state.module_spacing_scale * 1.1, 4)


def _apply_move_module(state: LayoutState, target: str, params: dict) -> None:
    """移动模块到新位置。"""
    if target not in state.module_positions:
        return
    x, y = state.module_positions[target]
    dx = float(params.get("dx", 0.0))
    dy = float(params.get("dy", 0.0))
    state.module_positions[target] = (round(x + dx, 4), round(y + dy, 4))


def _apply_move_label(state: LayoutState, target: str, params: dict) -> None:
    """移动标签偏移。"""
    dx = float(params.get("dx", 0.0))
    dy = float(params.get("dy", 0.0))
    old_dx, old_dy = state.label_offsets.get(target, (0.0, 0.0))
    state.label_offsets[target] = (round(old_dx + dx, 4), round(old_dy + dy, 4))


def _apply_expand_canvas(state: LayoutState, _target: str, params: dict) -> None:
    """扩大画布。"""
    new_w = float(params.get("new_width", state.canvas_width))
    new_h = float(params.get("new_height", state.canvas_height))
    state.canvas_width = max(state.canvas_width, new_w)
    state.canvas_height = max(state.canvas_height, new_h)


_ACTION_DISPATCH = {
    PatchActionType.INCREASE_MODULE_SPACING: _apply_increase_spacing,
    PatchActionType.MOVE_MODULE: _apply_move_module,
    PatchActionType.MOVE_LABEL: _apply_move_label,
    PatchActionType.EXPAND_CANVAS: _apply_expand_canvas,
}


# ============================================================
# V068: 主执行入口
# ============================================================


def apply_visual_patches(
    layout_state: LayoutState,
    patch_plan: LayoutPatchPlan,
) -> LayoutState:
    """Apply patches to layout state. Does NOT modify SystemDesignIR (VC05).

    返回修改后的 LayoutState 副本。
    """
    new_state = deepcopy(layout_state)

    for action in patch_plan.actions:
        handler = _ACTION_DISPATCH.get(action.action_type)
        if handler is not None:
            handler(new_state, action.target, action.parameters)
        # 未注册的动作类型（如 REROUTE_CONNECTION）静默跳过

    return new_state


# ============================================================
# V070: 布局差异记录
# ============================================================


def diff_layout_states(before: LayoutState, after: LayoutState) -> dict:
    """Record what changed between two layout states."""
    diff: dict[str, list[dict]] = {
        "moved_modules": [],
        "moved_labels": [],
        "canvas_changed": [],
        "spacing_changed": [],
    }

    # 模块位置变化
    all_modules = set(before.module_positions) | set(after.module_positions)
    for mid in sorted(all_modules):
        pos_before = before.module_positions.get(mid)
        pos_after = after.module_positions.get(mid)
        if pos_before != pos_after:
            diff["moved_modules"].append({
                "module_id": mid,
                "before": pos_before,
                "after": pos_after,
            })

    # 标签偏移变化
    all_labels = set(before.label_offsets) | set(after.label_offsets)
    for lid in sorted(all_labels):
        off_before = before.label_offsets.get(lid)
        off_after = after.label_offsets.get(lid)
        if off_before != off_after:
            diff["moved_labels"].append({
                "label_id": lid,
                "before": off_before,
                "after": off_after,
            })

    # 画布变化
    if before.canvas_width != after.canvas_width or before.canvas_height != after.canvas_height:
        diff["canvas_changed"].append({
            "before": (before.canvas_width, before.canvas_height),
            "after": (after.canvas_width, after.canvas_height),
        })

    # 间距缩放变化
    if before.module_spacing_scale != after.module_spacing_scale:
        diff["spacing_changed"].append({
            "before": before.module_spacing_scale,
            "after": after.module_spacing_scale,
        })

    return diff
