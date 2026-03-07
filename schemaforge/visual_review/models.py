"""视觉审稿数据模型。

所有模型都是纯数据容器，不含业务逻辑。
AI 审稿只能输出 VisualIssue + LayoutPatchAction，不能改电气。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ============================================================
# 枚举
# ============================================================


class PatchActionType(str, Enum):
    """允许的布局修补动作（白名单）。"""

    INCREASE_MODULE_SPACING = "increase_module_spacing"
    MOVE_MODULE = "move_module"
    MOVE_LABEL = "move_label"
    REROUTE_CONNECTION = "reroute_connection"
    EXPAND_CANVAS = "expand_canvas"
    ADD_NET_LABEL = "add_net_label"
    ADJUST_FONT_SIZE = "adjust_font_size"


# 禁止动作黑名单（代码中硬编码拒绝）
FORBIDDEN_ACTIONS: frozenset[str] = frozenset({
    "add_component",
    "remove_component",
    "change_value",
    "change_connection",
    "modify_netlist",
    "change_pin",
    "swap_device",
})


class IssueSeverity(str, Enum):
    """视觉问题严重程度。"""

    CRITICAL = "critical"      # 必须修复（如元件被遮挡）
    WARNING = "warning"        # 建议修复（如标签重叠）
    INFO = "info"              # 美观建议（如间距不均匀）


class StopReason(str, Enum):
    """闭环停止原因。"""

    SCORE_REACHED = "score_reached"          # 分数达标
    MAX_ITERATIONS = "max_iterations"        # 达到最大轮数
    NO_IMPROVEMENT = "no_improvement"        # 连续两轮无改善
    ALL_ISSUES_RESOLVED = "all_resolved"     # 所有问题已修复
    USER_STOPPED = "user_stopped"            # 用户手动停止
    ERROR = "error"                          # 出错退出


# ============================================================
# V012: ReviewImageSet
# ============================================================


@dataclass
class ReviewImageSet:
    """审稿用截图集合。"""

    full_image_path: str = ""                 # 整图 PNG 路径
    full_image_hd_path: str = ""              # 高 DPI 整图 PNG 路径
    module_crops: dict[str, str] = field(default_factory=dict)
    # module_id → 裁剪图路径
    connection_crops: list[str] = field(default_factory=list)
    # 模块间连接区域裁剪图路径列表
    text_dense_crops: list[str] = field(default_factory=list)
    # 文字密集区域裁剪图路径列表
    dpi: int = 150


# ============================================================
# V013: ReviewManifest
# ============================================================


@dataclass
class ReviewManifest:
    """审稿素材清单，随截图一并提供给 AI。"""

    module_list: list[dict[str, str]] = field(default_factory=list)
    # [{"module_id": "buck1", "device": "TPS5430", "role": "降压", "position": "left"}]
    connection_list: list[dict[str, str]] = field(default_factory=list)
    # [{"from": "buck1.VOUT", "to": "ldo1.VIN", "net": "NET_5V"}]
    unresolved_items: list[str] = field(default_factory=list)
    total_components: int = 0
    total_nets: int = 0
    canvas_size: tuple[float, float] = (0.0, 0.0)

    def to_text(self) -> str:
        """生成给 AI 的文本摘要。"""
        lines = [f"模块数: {len(self.module_list)}, 元件数: {self.total_components}, 网络数: {self.total_nets}"]
        for m in self.module_list:
            lines.append(f"  {m.get('module_id', '?')}: {m.get('device', '?')} ({m.get('role', '?')})")
        if self.connection_list:
            lines.append("连接:")
            for c in self.connection_list:
                lines.append(f"  {c.get('from', '?')} → {c.get('to', '?')} [{c.get('net', '?')}]")
        if self.unresolved_items:
            lines.append(f"未解决: {', '.join(self.unresolved_items)}")
        return "\n".join(lines)


# ============================================================
# V014: VisualIssue
# ============================================================


@dataclass
class VisualIssue:
    """AI 或本地评分器发现的单个视觉问题。"""

    issue_id: str
    severity: IssueSeverity
    category: str                             # "overlap", "spacing", "label", "visibility", "routing"
    description: str                          # 人类可读描述
    affected_elements: list[str] = field(default_factory=list)
    # ["C1", "R2"] 或 ["buck1", "ldo1"]
    suggested_fix: str = ""                   # AI 的修复建议（仅供参考）
    location: tuple[float, float] = (0.0, 0.0)  # 问题位置 (x, y)
    source: str = "ai"                        # "ai" 或 "local"


# ============================================================
# V015: VisualReviewReport
# ============================================================


@dataclass
class VisualReviewReport:
    """AI 审稿报告。"""

    issues: list[VisualIssue] = field(default_factory=list)
    overall_score: float = 0.0                # 0-10 分
    summary: str = ""                         # AI 总体评价
    raw_ai_response: str = ""                 # AI 原始输出（留存 debug）

    @property
    def critical_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == IssueSeverity.CRITICAL)

    @property
    def warning_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == IssueSeverity.WARNING)

    @property
    def has_critical(self) -> bool:
        return self.critical_count > 0


# ============================================================
# V016: LayoutPatchAction
# ============================================================


@dataclass
class LayoutPatchAction:
    """单个布局修补动作。必须在白名单内。"""

    action_type: PatchActionType
    target: str = ""                          # 目标元素 ID（模块/标签/连线）
    parameters: dict[str, Any] = field(default_factory=dict)
    # action-specific 参数，如 {"dx": 2.0, "dy": 0} 或 {"spacing": 1.5}
    reason: str = ""                          # 为什么做这个修补
    source_issue_id: str = ""                 # 关联的 VisualIssue ID


# ============================================================
# V017: LayoutPatchPlan
# ============================================================


@dataclass
class LayoutPatchPlan:
    """一轮布局修补计划。"""

    actions: list[LayoutPatchAction] = field(default_factory=list)
    rejected_actions: list[dict[str, str]] = field(default_factory=list)
    # [{"action": "add_component", "reason": "在黑名单中"}]
    estimated_improvement: float = 0.0        # 预估改善分数

    @property
    def action_count(self) -> int:
        return len(self.actions)

    @property
    def has_actions(self) -> bool:
        return len(self.actions) > 0


# ============================================================
# V018: RenderScore
# ============================================================


@dataclass
class RenderScore:
    """渲染质量评分（本地硬指标 + AI 软指标）。"""

    # 本地硬指标 (0-1 each, 1 = perfect)
    all_modules_visible: float = 0.0
    no_label_overlap: float = 0.0
    no_label_overflow: float = 0.0
    no_module_overlap: float = 0.0
    min_spacing_ok: float = 0.0
    connections_visible: float = 0.0
    crossing_penalty: float = 0.0             # 0 = no crossings, negative = crossings

    # AI 软指标
    ai_score: float = 0.0                     # 0-10 from AI review
    ai_confidence: float = 0.0                # AI 置信度

    @property
    def local_score(self) -> float:
        """本地硬指标综合分 (0-10)。"""
        metrics = [
            self.all_modules_visible,
            self.no_label_overlap,
            self.no_label_overflow,
            self.no_module_overlap,
            self.min_spacing_ok,
            self.connections_visible,
        ]
        raw = sum(metrics) / max(len(metrics), 1)
        return round(raw * 10 + self.crossing_penalty, 2)

    @property
    def combined_score(self) -> float:
        """综合分：本地 70% + AI 30%（本地优先，约束 C: VC08）。"""
        return round(self.local_score * 0.7 + self.ai_score * 0.3, 2)

    def meets_threshold(self, threshold: float = 7.0) -> bool:
        """是否达到停止阈值。"""
        return self.combined_score >= threshold


# ============================================================
# V019: VisualReviewTrace
# ============================================================


@dataclass
class VisualReviewTraceEntry:
    """单轮审稿记录。"""

    iteration: int
    images: ReviewImageSet
    review_report: VisualReviewReport
    patch_plan: LayoutPatchPlan
    score_before: RenderScore
    score_after: RenderScore
    patches_applied: int = 0
    patches_rejected: int = 0


@dataclass
class VisualReviewTrace:
    """完整审稿闭环追踪记录。"""

    entries: list[VisualReviewTraceEntry] = field(default_factory=list)
    stop_reason: StopReason = StopReason.MAX_ITERATIONS
    total_iterations: int = 0
    initial_score: float = 0.0
    final_score: float = 0.0

    def score_improved(self) -> bool:
        return self.final_score > self.initial_score

    def improvement_delta(self) -> float:
        return round(self.final_score - self.initial_score, 2)

    def to_summary(self) -> dict[str, Any]:
        return {
            "total_iterations": self.total_iterations,
            "stop_reason": self.stop_reason.value,
            "initial_score": self.initial_score,
            "final_score": self.final_score,
            "improvement": self.improvement_delta(),
            "total_patches_applied": sum(e.patches_applied for e in self.entries),
            "total_patches_rejected": sum(e.patches_rejected for e in self.entries),
        }


# ============================================================
# 停止策略配置
# ============================================================


@dataclass
class VisualReviewConfig:
    """视觉审稿闭环配置。"""

    max_iterations: int = 5                   # 最大轮数 (VC06)
    score_threshold: float = 7.0              # 停止分数阈值 (VC11)
    no_improvement_limit: int = 2             # 连续无改善停止 (VC10)
    min_improvement: float = 0.3              # 最小改善幅度（低于此视为无改善）
    local_weight: float = 0.7                 # 本地分数权重 (VC08)
    ai_weight: float = 0.3                    # AI 分数权重
    image_dpi: int = 150                      # 截图 DPI
    hd_dpi: int = 300                         # 高清截图 DPI
