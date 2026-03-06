"""SchemaForge 设计中间表示（Design IR）

整个设计系统的**唯一中间真值层**。

所有上游模块（planner, clarifier, retrieval, adapter, rationality, review）
将结果写入 IR；所有下游模块（renderer, exporter, patch_engine, GUI）
从 IR 读取。

设计目标:
- 人类可读（JSON 序列化友好）
- 程序可校验（Pydantic 强类型）
- 可持久化（model_dump_json / model_validate_json）
- 可差异比较（diff 两个 IR 快照）
- 可局部修改（PatchOp 操作）
- 可回放生成过程（保留 assumptions + evidence + patch_history）

核心模型层次::

    DesignIR
    ├── intent: DesignIntent          # 用户需求 + 缺失约束 + 假设
    ├── modules: list[ModuleIR]       # 模块级设计结果
    │   ├── intent: ModuleIntent      #   模块需求
    │   ├── selection: DeviceSelection#   器件选择（含候选）
    │   ├── parameters: DerivedParameters  # 计算参数
    │   └── review: ModuleReview      #   模块级审查
    ├── topology: TopologyIR          # 全局拓扑连接
    ├── review: DesignReview          # 全局审查报告
    ├── outputs: DesignOutputs        # 渲染/导出结果路径
    └── history: list[PatchRecord]    # 修改历史
"""

from __future__ import annotations

import copy
import uuid
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ============================================================
# 枚举
# ============================================================


class ConstraintPriority(str, Enum):
    """约束优先级"""

    REQUIRED = "required"  # 必须满足，缺失则阻断
    PREFERRED = "preferred"  # 用户偏好，可默认
    OPTIONAL = "optional"  # 可选，有则更好


class ReviewSeverity(str, Enum):
    """审查问题严重级别"""

    BLOCKING = "blocking"  # 阻断出图
    WARNING = "warning"  # 警告，可继续
    RECOMMENDATION = "recommendation"  # 改进建议
    LAYOUT_NOTE = "layout_note"  # 布局注意事项
    BRINGUP_NOTE = "bringup_note"  # 调试注意事项


class IssueCategory(str, Enum):
    """问题分类"""

    ELECTRICAL = "electrical"  # 电气问题
    THERMAL = "thermal"  # 热设计
    STABILITY = "stability"  # 稳定性
    PROTECTION = "protection"  # 保护电路
    COMPLETENESS = "completeness"  # 完整性
    COMPATIBILITY = "compatibility"  # 兼容性


# ============================================================
# DesignIntent — 用户需求解析
# ============================================================


class Constraint(BaseModel):
    """单条设计约束"""

    name: str  # 约束名 ("v_in", "i_out_max", ...)
    value: str = ""  # 约束值 ("5V", "1A", ...)
    priority: ConstraintPriority = ConstraintPriority.REQUIRED
    source: str = "user"  # "user" | "inferred" | "default"
    confidence: float = 1.0  # 0-1


class Assumption(BaseModel):
    """设计假设 — 系统自动填充的缺失约束"""

    field: str  # 对应字段 ("v_in_range", "i_load", ...)
    assumed_value: str  # 假设值
    reason: str = ""  # 为什么这样假设
    risk: str = ""  # 如果假设错了会怎样
    confidence: float = 0.5


class UnresolvedQuestion(BaseModel):
    """未解决的问题 — 需要用户澄清"""

    question_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    field: str  # 关联字段
    question: str  # 问题文本（中文）
    why_needed: str = ""  # 为什么需要这个信息
    default_if_skipped: str = ""  # 如果用户跳过，用什么默认值
    priority: ConstraintPriority = ConstraintPriority.REQUIRED
    answer: str = ""  # 用户回答（空 = 未回答）
    answered: bool = False


class DesignIntent(BaseModel):
    """设计意图 — 从用户需求解析的结构化表示"""

    raw_input: str = ""  # 原始用户输入
    summary: str = ""  # 规范化需求摘要（中文）
    design_type: str = ""  # 设计类型 ("power", "signal", "mixed")
    known_constraints: list[Constraint] = Field(default_factory=list)
    missing_constraints: list[str] = Field(default_factory=list)
    assumptions: list[Assumption] = Field(default_factory=list)
    unresolved_questions: list[UnresolvedQuestion] = Field(default_factory=list)
    confidence: float = 0.5  # 需求理解置信度 (0-1)

    @property
    def can_proceed(self) -> bool:
        """是否可以继续设计（无 REQUIRED 级未解决问题）"""
        return not any(
            q.priority == ConstraintPriority.REQUIRED and not q.answered
            for q in self.unresolved_questions
        )

    @property
    def all_resolved(self) -> bool:
        """所有问题是否已解决"""
        return all(q.answered for q in self.unresolved_questions)


# ============================================================
# ModuleIntent — 模块需求
# ============================================================


class ModuleIntent(BaseModel):
    """模块设计意图"""

    role: str  # "main_regulator", "power_led", ...
    category: str = ""  # "ldo", "buck", "led", ...
    description: str = ""  # 功能描述（中文）
    target_specs: dict[str, str] = Field(default_factory=dict)
    # 关键参数目标 {"v_in": "5", "v_out": "3.3", ...}
    depends_on: list[str] = Field(default_factory=list)
    # 依赖模块的 role 列表


# ============================================================
# DeviceSelection — 器件选择
# ============================================================


class CandidateDevice(BaseModel):
    """候选器件"""

    part_number: str
    manufacturer: str = ""
    score: float = 0.0  # 综合匹配得分 (0-1)
    match_reasons: list[str] = Field(default_factory=list)
    tradeoff_notes: str = ""  # 方案 tradeoff 说明


class DeviceSelection(BaseModel):
    """器件选择结果"""

    selected: CandidateDevice | None = None  # 当前选中
    candidates: list[CandidateDevice] = Field(default_factory=list)
    selection_reason: str = ""  # 为什么选这个
    alternatives_note: str = ""  # 替代方案说明


# ============================================================
# DerivedParameters — 计算参数
# ============================================================


class CalculatedValue(BaseModel):
    """单个计算结果"""

    name: str  # 参数名 ("r_limit", "c_out", ...)
    value: str  # 计算值 ("120Ω", "22μF", ...)
    formula: str = ""  # 使用公式
    assumptions: list[str] = Field(default_factory=list)
    unit: str = ""
    source: str = "calculated"  # "calculated" | "default" | "user"


class DerivedParameters(BaseModel):
    """模块的派生参数集"""

    input_params: dict[str, str] = Field(default_factory=dict)
    # 输入参数（来自用户+假设）
    calculated: list[CalculatedValue] = Field(default_factory=list)
    # 计算得到的参数
    render_params: dict[str, Any] = Field(default_factory=dict)
    # 传给渲染器的最终参数集


# ============================================================
# Review — 审查报告
# ============================================================


class ReviewIssue(BaseModel):
    """审查发现的问题"""

    issue_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    severity: ReviewSeverity
    category: IssueCategory = IssueCategory.ELECTRICAL
    rule_id: str = ""  # 规则标识
    message: str  # 问题描述（中文）
    suggestion: str = ""  # 修复建议
    evidence: str = ""  # 依据
    module_role: str = ""  # 关联模块（空=全局）


class ModuleReview(BaseModel):
    """模块级审查报告"""

    issues: list[ReviewIssue] = Field(default_factory=list)
    passed: bool = True

    @property
    def has_blocking(self) -> bool:
        return any(i.severity == ReviewSeverity.BLOCKING for i in self.issues)


class DesignReview(BaseModel):
    """全局设计审查报告"""

    issues: list[ReviewIssue] = Field(default_factory=list)
    overall_passed: bool = True
    reviewed_at: str = ""  # ISO 8601

    @property
    def blocking_issues(self) -> list[ReviewIssue]:
        return [i for i in self.issues if i.severity == ReviewSeverity.BLOCKING]

    @property
    def warnings(self) -> list[ReviewIssue]:
        return [i for i in self.issues if i.severity == ReviewSeverity.WARNING]

    @property
    def recommendations(self) -> list[ReviewIssue]:
        return [i for i in self.issues if i.severity == ReviewSeverity.RECOMMENDATION]


# ============================================================
# TopologyIR — 拓扑中间表示
# ============================================================


class NetIR(BaseModel):
    """网络连接"""

    name: str  # 网络名 ("VIN", "VOUT", "GND", ...)
    connections: list[str] = Field(default_factory=list)
    # 引脚连接 ("U1.VIN", "C1.1", ...)
    is_power: bool = False
    is_ground: bool = False
    source: str = "library"  # "library" | "ai_draft" | "user"


class TopologyIR(BaseModel):
    """全局拓扑信息"""

    nets: list[NetIR] = Field(default_factory=list)
    inter_module_connections: list[dict[str, str]] = Field(default_factory=list)
    # 模块间连接 [{"from_module": ..., "from_net": ..., "to_module": ..., "to_net": ...}]
    design_spec: dict[str, Any] = Field(default_factory=dict)
    # 兼容现有 AdaptationResult.to_design_spec() 格式


# ============================================================
# ModuleIR — 单个模块的完整 IR
# ============================================================


class ModuleIR(BaseModel):
    """单个模块的完整设计中间表示"""

    intent: ModuleIntent
    selection: DeviceSelection = Field(default_factory=DeviceSelection)
    parameters: DerivedParameters = Field(default_factory=DerivedParameters)
    review: ModuleReview = Field(default_factory=ModuleReview)
    svg_path: str = ""  # 渲染输出路径
    bom_text: str = ""
    spice_text: str = ""
    error: str = ""  # 错误信息（空=正常）


# ============================================================
# DesignOutputs — 渲染/导出结果
# ============================================================


class DesignOutputs(BaseModel):
    """设计输出物"""

    svg_paths: list[str] = Field(default_factory=list)
    bom_text: str = ""
    spice_text: str = ""
    design_spec_json: str = ""  # 完整 design_spec 的 JSON 字符串


# ============================================================
# PatchRecord — 修改历史
# ============================================================


class PatchRecord(BaseModel):
    """单次修改记录"""

    patch_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    timestamp: str = Field(
        default_factory=lambda: datetime.now().isoformat(),
    )
    user_request: str = ""  # 用户修改需求原文
    ops: list[dict[str, Any]] = Field(default_factory=list)
    # PatchOp 列表的 dict 表示
    affected_modules: list[str] = Field(default_factory=list)
    # 受影响模块的 role 列表
    snapshot_before_id: str = ""  # 修改前快照 ID
    snapshot_after_id: str = ""  # 修改后快照 ID
    success: bool = True
    error: str = ""


# ============================================================
# DesignIR — 顶层 IR
# ============================================================


class DesignIR(BaseModel):
    """设计中间表示 — 整个设计系统的唯一中间真值

    用法::

        ir = DesignIR(
            intent=DesignIntent(raw_input="5V转3.3V稳压电路，带LED指示灯"),
        )
        # ... planner 写入 modules ...
        # ... retrieval 写入 selection ...
        # ... rationality 写入 review ...
        # ... renderer 写入 outputs ...

        # 序列化
        json_str = ir.model_dump_json(indent=2)

        # 反序列化
        ir2 = DesignIR.model_validate_json(json_str)

        # 快照
        snapshot = ir.snapshot("v1 初始设计")
    """

    ir_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    version: int = 1  # IR 版本号，每次 patch +1
    created_at: str = Field(
        default_factory=lambda: datetime.now().isoformat(),
    )
    updated_at: str = Field(
        default_factory=lambda: datetime.now().isoformat(),
    )

    # 核心层
    intent: DesignIntent = Field(default_factory=DesignIntent)
    modules: list[ModuleIR] = Field(default_factory=list)
    topology: TopologyIR = Field(default_factory=TopologyIR)
    review: DesignReview = Field(default_factory=DesignReview)
    outputs: DesignOutputs = Field(default_factory=DesignOutputs)

    # 修改历史
    history: list[PatchRecord] = Field(default_factory=list)

    # 元数据
    stage: str = "init"  # 当前阶段
    success: bool = False
    error: str = ""

    # ----------------------------------------------------------
    # 便捷方法
    # ----------------------------------------------------------

    def get_module(self, role: str) -> ModuleIR | None:
        """按 role 查找模块"""
        for m in self.modules:
            if m.intent.role == role:
                return m
        return None

    def module_roles(self) -> list[str]:
        """所有模块的 role 列表"""
        return [m.intent.role for m in self.modules]

    def snapshot(self, label: str = "") -> IRSnapshot:
        """创建当前 IR 的快照（深拷贝）"""
        return IRSnapshot(
            snapshot_id=uuid.uuid4().hex[:12],
            label=label or f"v{self.version}",
            timestamp=datetime.now().isoformat(),
            ir_data=self.model_dump(),
            version=self.version,
            module_count=len(self.modules),
            stage=self.stage,
        )

    def bump_version(self) -> None:
        """版本号 +1，更新时间戳"""
        self.version += 1
        self.updated_at = datetime.now().isoformat()

    def to_summary(self) -> dict[str, Any]:
        """生成摘要（用于 GUI/CLI 展示）"""
        return {
            "ir_id": self.ir_id,
            "version": self.version,
            "stage": self.stage,
            "success": self.success,
            "intent_summary": self.intent.summary,
            "module_count": len(self.modules),
            "module_roles": self.module_roles(),
            "svg_count": len(self.outputs.svg_paths),
            "review_blocking": len(self.review.blocking_issues),
            "review_warnings": len(self.review.warnings),
            "patch_count": len(self.history),
            "error": self.error,
        }


# ============================================================
# IRSnapshot — IR 快照
# ============================================================


class IRSnapshot(BaseModel):
    """IR 快照 — 用于历史回滚和差异对比"""

    snapshot_id: str
    label: str = ""
    timestamp: str = ""
    ir_data: dict[str, Any] = Field(default_factory=dict)
    version: int = 0
    module_count: int = 0
    stage: str = ""

    def restore(self) -> DesignIR:
        """从快照恢复 IR（深拷贝）"""
        data = copy.deepcopy(self.ir_data)
        return DesignIR.model_validate(data)


# ============================================================
# IRHistory — 快照历史管理
# ============================================================


class IRHistory:
    """IR 快照历史管理器

    用法::

        history = IRHistory()
        sid = history.save(ir, "初始设计")
        # ... 修改 ir ...
        sid2 = history.save(ir, "修改输出电容")

        # 回滚
        old_ir = history.restore(sid)

        # 撤销到上一版
        prev_ir = history.undo()

        # 查看所有快照
        for info in history.snapshots:
            print(info.label, info.timestamp)
    """

    def __init__(self) -> None:
        self._snapshots: dict[str, IRSnapshot] = {}
        self._order: list[str] = []  # 按时间排序的 snapshot_id 列表

    @property
    def snapshots(self) -> list[IRSnapshot]:
        """所有快照（按时间排序）"""
        return [self._snapshots[sid] for sid in self._order if sid in self._snapshots]

    @property
    def count(self) -> int:
        return len(self._order)

    def save(self, ir: DesignIR, label: str = "") -> str:
        """保存快照，返回 snapshot_id"""
        snap = ir.snapshot(label)
        self._snapshots[snap.snapshot_id] = snap
        self._order.append(snap.snapshot_id)
        return snap.snapshot_id

    def restore(self, snapshot_id: str) -> DesignIR | None:
        """恢复到指定快照"""
        snap = self._snapshots.get(snapshot_id)
        if snap is None:
            return None
        return snap.restore()

    def undo(self) -> DesignIR | None:
        """撤销到上一个快照"""
        if len(self._order) < 2:
            return None
        # 倒数第二个快照
        prev_id = self._order[-2]
        return self.restore(prev_id)

    def latest(self) -> IRSnapshot | None:
        """最新快照"""
        if not self._order:
            return None
        return self._snapshots.get(self._order[-1])

    def get(self, snapshot_id: str) -> IRSnapshot | None:
        """获取指定快照"""
        return self._snapshots.get(snapshot_id)
