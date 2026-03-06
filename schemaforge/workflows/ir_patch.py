"""IRPatchEngine — IR 级修改操作执行器

对 DesignIR 执行结构化的修改操作序列，支持：
- update_constraint: 更新或新增约束
- replace_device: 替换模块器件
- add_module: 新增模块
- remove_module: 删除模块
- update_parameter: 更新模块参数
- change_preference: 更新设计偏好/假设

与 PatchEngine（操作 design_spec 字典）不同，IRPatchEngine 直接操作
类型安全的 DesignIR 对象，保留完整历史记录。

用法::

    engine = IRPatchEngine()
    ops = [
        IRPatchOp(op_type="update_constraint", field="v_in", value="12V"),
        IRPatchOp(op_type="replace_device", target_module="main_regulator",
                  value="AMS1117-3.3"),
    ]
    result = engine.apply(ir, ops)
    if result.success:
        updated_ir = result.modified_ir
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass  # 运行时使用延迟导入，避免循环依赖


# ============================================================
# 支持的操作类型
# ============================================================

SUPPORTED_OP_TYPES = frozenset(
    {
        "update_constraint",
        "replace_device",
        "add_module",
        "remove_module",
        "update_parameter",
        "change_preference",
    }
)


# ============================================================
# 数据模型
# ============================================================


@dataclass
class IRPatchOp:
    """IR 级修改操作"""

    op_type: str
    """操作类型：update_constraint / replace_device / add_module /
    remove_module / update_parameter / change_preference"""

    target_module: str = ""
    """目标模块 role（空=全局操作）"""

    field: str = ""
    """字段名（约束名、参数名、偏好名等）"""

    value: Any = None
    """新值"""

    reason: str = ""
    """修改原因（可选，记入日志）"""


@dataclass
class IRPatchResult:
    """IR 修改结果"""

    success: bool = False
    """是否有至少一个操作成功执行"""

    modified_ir: Any | None = None
    """修改后的 IR（深拷贝，None 表示全部失败）"""

    patch_record: Any | None = None
    """对应的 PatchRecord，已追加到 modified_ir.history"""

    applied_ops: list[IRPatchOp] = field(default_factory=list)
    """成功执行的操作列表"""

    rejected_ops: list[tuple[IRPatchOp, str]] = field(default_factory=list)
    """被拒绝的操作列表，每项为 (IRPatchOp, 拒绝原因)"""

    affected_modules: list[str] = field(default_factory=list)
    """受影响的模块 role 列表"""

    warnings: list[str] = field(default_factory=list)
    """执行过程中的警告信息"""


# ============================================================
# IRPatchEngine
# ============================================================


class IRPatchEngine:
    """IR 级修改操作执行器

    对 DesignIR 对象执行结构化修改。不修改原始 IR，返回深拷贝结果。
    每个操作独立执行：某操作失败不影响其他操作。
    """

    # ----------------------------------------------------------
    # 公开 API
    # ----------------------------------------------------------

    def apply(self, ir: Any, ops: list[IRPatchOp]) -> IRPatchResult:
        """应用修改操作到 IR（不修改原始 IR，返回深拷贝）

        Args:
            ir:  原始 DesignIR（不会被修改）
            ops: 要执行的 IRPatchOp 列表

        Returns:
            IRPatchResult，含修改后的 IR 深拷贝
        """
        from schemaforge.design.ir import DesignIR, ModuleIR, ModuleIntent, PatchRecord  # noqa: F401

        # 1. 深拷贝 IR，不修改原始
        modified = copy.deepcopy(ir)

        result = IRPatchResult(modified_ir=modified)

        # 2. 保存修改前快照
        snap_before = modified.snapshot("patch_before")
        snapshot_before_id = snap_before.snapshot_id

        # 3. 逐一执行操作
        for op in ops:
            ok, reason, affected = self._execute_op(modified, op)
            if ok:
                result.applied_ops.append(op)
                for role in affected:
                    if role and role not in result.affected_modules:
                        result.affected_modules.append(role)
            else:
                result.rejected_ops.append((op, reason))

        # 4. 如果有成功操作，提升版本并创建历史记录
        if result.applied_ops:
            modified.bump_version()
            snap_after = modified.snapshot("patch_after")

            # 5. 创建 PatchRecord
            patch_record = PatchRecord(
                user_request="; ".join(
                    op.reason for op in result.applied_ops if op.reason
                ),
                ops=[
                    {
                        "op_type": op.op_type,
                        "target_module": op.target_module,
                        "field": op.field,
                        "value": op.value,
                        "reason": op.reason,
                    }
                    for op in result.applied_ops
                ],
                affected_modules=result.affected_modules,
                snapshot_before_id=snapshot_before_id,
                snapshot_after_id=snap_after.snapshot_id,
                success=True,
            )
            modified.history.append(patch_record)
            result.patch_record = patch_record
            result.success = True
        else:
            # 全部失败，success=False，但仍返回副本（未修改）
            result.success = False

        return result

    def preview(self, ir: Any, ops: list[IRPatchOp]) -> list[str]:
        """预览修改影响（返回人类可读的变更描述列表）

        Args:
            ir:  DesignIR
            ops: 要预览的 IRPatchOp 列表

        Returns:
            变更描述字符串列表
        """
        descriptions: list[str] = []

        for op in ops:
            desc = self._describe_op(ir, op)
            descriptions.append(desc)

        return descriptions

    def validate(self, ir: Any, ops: list[IRPatchOp]) -> list[str]:
        """验证操作是否可执行（返回错误信息列表，空=可执行）

        Args:
            ir:  DesignIR
            ops: 要验证的 IRPatchOp 列表

        Returns:
            错误信息列表，空列表表示全部合法
        """
        errors: list[str] = []

        for i, op in enumerate(ops):
            # 验证操作类型
            if op.op_type not in SUPPORTED_OP_TYPES:
                errors.append(
                    f"op[{i}]: 不支持的操作类型 '{op.op_type}'，"
                    f"支持的类型：{sorted(SUPPORTED_OP_TYPES)}"
                )
                continue

            # 各操作类型的专项验证
            op_errors = self._validate_op(ir, op, i)
            errors.extend(op_errors)

        return errors

    # ----------------------------------------------------------
    # 内部：操作执行
    # ----------------------------------------------------------

    def _execute_op(self, ir: Any, op: IRPatchOp) -> tuple[bool, str, list[str]]:
        """执行单个操作

        Returns:
            (成功, 失败原因, 受影响模块roles列表)
        """
        if op.op_type not in SUPPORTED_OP_TYPES:
            return False, f"不支持的操作类型 '{op.op_type}'", []

        dispatch = {
            "update_constraint": self._op_update_constraint,
            "replace_device": self._op_replace_device,
            "add_module": self._op_add_module,
            "remove_module": self._op_remove_module,
            "update_parameter": self._op_update_parameter,
            "change_preference": self._op_change_preference,
        }

        handler = dispatch[op.op_type]
        return handler(ir, op)

    def _op_update_constraint(
        self, ir: Any, op: IRPatchOp
    ) -> tuple[bool, str, list[str]]:
        """更新或新增约束"""
        from schemaforge.design.ir import Constraint, ConstraintPriority

        if not op.field:
            return False, "update_constraint 操作需要指定 field（约束名）", []

        # 查找现有约束
        existing = None
        for c in ir.intent.known_constraints:
            if c.name == op.field:
                existing = c
                break

        if existing is not None:
            # 更新现有约束
            existing.value = str(op.value) if op.value is not None else ""
        else:
            # 新增约束
            new_constraint = Constraint(
                name=op.field,
                value=str(op.value) if op.value is not None else "",
                priority=ConstraintPriority.REQUIRED,
                source="user",
            )
            ir.intent.known_constraints.append(new_constraint)

        # 找到依赖此约束的模块
        affected: list[str] = []
        for m in ir.modules:
            if op.field in m.intent.target_specs:
                affected.append(m.intent.role)

        return True, "", affected

    def _op_replace_device(self, ir: Any, op: IRPatchOp) -> tuple[bool, str, list[str]]:
        """替换模块器件选择"""
        from schemaforge.design.ir import CandidateDevice, DeviceSelection

        if not op.target_module:
            return False, "replace_device 操作需要指定 target_module", []

        module = ir.get_module(op.target_module)
        if module is None:
            return (
                False,
                f"模块 '{op.target_module}' 不存在于 IR 中",
                [],
            )

        new_part_number = str(op.value) if op.value is not None else ""

        # 重置模块器件选择
        module.selection = DeviceSelection(
            selected=CandidateDevice(
                part_number=new_part_number,
                manufacturer="",
                score=0.0,
                match_reasons=["用户手动指定"],
            ),
            candidates=[],
            selection_reason=f"用户手动替换为 {new_part_number}",
            alternatives_note="",
        )

        # 重置参数和审查（需要重新验证）
        from schemaforge.design.ir import DerivedParameters, ModuleReview

        module.parameters = DerivedParameters()
        module.review = ModuleReview()

        return (
            True,
            "",
            [op.target_module],
        )

    def _op_add_module(self, ir: Any, op: IRPatchOp) -> tuple[bool, str, list[str]]:
        """新增模块"""
        from schemaforge.design.ir import ModuleIR, ModuleIntent

        if not isinstance(op.value, dict):
            return False, "add_module 操作的 value 必须是字典", []

        value_dict: dict[str, Any] = op.value
        role = value_dict.get("role", "")
        if not role:
            return False, "add_module 操作的 value 中必须包含 'role' 字段", []

        category = value_dict.get("category", "")

        # 检查重复 role
        if ir.get_module(role) is not None:
            return False, f"模块 role '{role}' 已存在，无法重复添加", []

        # 构建 ModuleIntent
        intent = ModuleIntent(
            role=role,
            category=category,
            description=value_dict.get("description", ""),
            target_specs=value_dict.get("target_specs", {}),
            depends_on=value_dict.get("depends_on", []),
        )

        new_module = ModuleIR(intent=intent)
        ir.modules.append(new_module)

        return True, "", [role]

    def _op_remove_module(self, ir: Any, op: IRPatchOp) -> tuple[bool, str, list[str]]:
        """删除模块"""
        if not op.target_module:
            return False, "remove_module 操作需要指定 target_module", []

        module = ir.get_module(op.target_module)
        if module is None:
            return (
                False,
                f"模块 '{op.target_module}' 不存在于 IR 中",
                [],
            )

        # 检查是否有其他模块依赖此模块
        dependents = []
        for m in ir.modules:
            if m.intent.role != op.target_module:
                if op.target_module in m.intent.depends_on:
                    dependents.append(m.intent.role)

        if dependents:
            return (
                False,
                f"无法删除模块 '{op.target_module}'，以下模块依赖它：{dependents}",
                [],
            )

        # 执行删除
        ir.modules = [m for m in ir.modules if m.intent.role != op.target_module]

        return True, "", [op.target_module]

    def _op_update_parameter(
        self, ir: Any, op: IRPatchOp
    ) -> tuple[bool, str, list[str]]:
        """更新模块参数"""
        from schemaforge.design.ir import ModuleReview

        if not op.target_module:
            return False, "update_parameter 操作需要指定 target_module", []

        if not op.field:
            return False, "update_parameter 操作需要指定 field（参数名）", []

        module = ir.get_module(op.target_module)
        if module is None:
            return (
                False,
                f"模块 '{op.target_module}' 不存在于 IR 中",
                [],
            )

        value_str = str(op.value) if op.value is not None else ""

        # 尝试更新 input_params，如果字段不存在也新增
        if op.field in module.parameters.input_params:
            module.parameters.input_params[op.field] = value_str
        elif op.field in module.parameters.render_params:
            module.parameters.render_params[op.field] = value_str
        else:
            # 默认写入 input_params
            module.parameters.input_params[op.field] = value_str

        # 标记模块需要重新验证（清空审查结果）
        module.review = ModuleReview()

        return True, "", [op.target_module]

    def _op_change_preference(
        self, ir: Any, op: IRPatchOp
    ) -> tuple[bool, str, list[str]]:
        """更新设计偏好/假设"""
        from schemaforge.design.ir import Assumption

        if not op.field:
            return False, "change_preference 操作需要指定 field（偏好/假设字段名）", []

        value_str = str(op.value) if op.value is not None else ""

        # 查找现有假设
        existing = None
        for a in ir.intent.assumptions:
            if a.field == op.field:
                existing = a
                break

        if existing is not None:
            existing.assumed_value = value_str
            if op.reason:
                existing.reason = op.reason
        else:
            # 新增假设
            new_assumption = Assumption(
                field=op.field,
                assumed_value=value_str,
                reason=op.reason or f"用户更新偏好：{op.field}",
                confidence=1.0,
            )
            ir.intent.assumptions.append(new_assumption)

        return True, "", []

    # ----------------------------------------------------------
    # 内部：操作验证
    # ----------------------------------------------------------

    def _validate_op(self, ir: Any, op: IRPatchOp, index: int) -> list[str]:
        """验证单个操作，返回错误列表"""
        errors: list[str] = []
        prefix = f"op[{index}]"

        if op.op_type == "update_constraint":
            if not op.field:
                errors.append(f"{prefix}: update_constraint 需要 field 字段（约束名）")

        elif op.op_type == "replace_device":
            if not op.target_module:
                errors.append(f"{prefix}: replace_device 需要 target_module")
            elif ir.get_module(op.target_module) is None:
                errors.append(f"{prefix}: 模块 '{op.target_module}' 不存在于 IR 中")

        elif op.op_type == "add_module":
            if not isinstance(op.value, dict):
                errors.append(f"{prefix}: add_module 的 value 必须是字典")
            else:
                if not op.value.get("role"):
                    errors.append(f"{prefix}: add_module 的 value 必须含 'role'")
                elif ir.get_module(op.value["role"]) is not None:
                    errors.append(f"{prefix}: 模块 role '{op.value['role']}' 已存在")

        elif op.op_type == "remove_module":
            if not op.target_module:
                errors.append(f"{prefix}: remove_module 需要 target_module")
            elif ir.get_module(op.target_module) is None:
                errors.append(f"{prefix}: 模块 '{op.target_module}' 不存在于 IR 中")
            else:
                dependents = [
                    m.intent.role
                    for m in ir.modules
                    if m.intent.role != op.target_module
                    and op.target_module in m.intent.depends_on
                ]
                if dependents:
                    errors.append(
                        f"{prefix}: 无法删除 '{op.target_module}'，"
                        f"被以下模块依赖：{dependents}"
                    )

        elif op.op_type == "update_parameter":
            if not op.target_module:
                errors.append(f"{prefix}: update_parameter 需要 target_module")
            elif ir.get_module(op.target_module) is None:
                errors.append(f"{prefix}: 模块 '{op.target_module}' 不存在于 IR 中")
            if not op.field:
                errors.append(f"{prefix}: update_parameter 需要 field（参数名）")

        elif op.op_type == "change_preference":
            if not op.field:
                errors.append(f"{prefix}: change_preference 需要 field（偏好字段名）")

        return errors

    # ----------------------------------------------------------
    # 内部：操作描述（preview 用）
    # ----------------------------------------------------------

    def _describe_op(self, ir: Any, op: IRPatchOp) -> str:
        """生成操作的人类可读描述"""
        if op.op_type == "update_constraint":
            # 判断是更新还是新增
            existing = next(
                (c for c in ir.intent.known_constraints if c.name == op.field),
                None,
            )
            if existing:
                return (
                    f"[update_constraint] 将约束 '{op.field}' 的值从 "
                    f"'{existing.value}' 更新为 '{op.value}'"
                )
            else:
                return f"[update_constraint] 新增约束 '{op.field}' = '{op.value}'"

        elif op.op_type == "replace_device":
            module = ir.get_module(op.target_module)
            if module and module.selection.selected:
                old_pn = module.selection.selected.part_number
                return (
                    f"[replace_device] 将模块 '{op.target_module}' 的器件从 "
                    f"'{old_pn}' 替换为 '{op.value}'"
                )
            return (
                f"[replace_device] 将模块 '{op.target_module}' 的器件替换为 "
                f"'{op.value}'"
            )

        elif op.op_type == "add_module":
            if isinstance(op.value, dict):
                role = op.value.get("role", "?")
                category = op.value.get("category", "")
                return f"[add_module] 新增模块 role='{role}'" + (
                    f"，类别={category}" if category else ""
                )
            return f"[add_module] 新增模块（{op.value}）"

        elif op.op_type == "remove_module":
            return f"[remove_module] 删除模块 '{op.target_module}'"

        elif op.op_type == "update_parameter":
            module = ir.get_module(op.target_module)
            if module:
                old_val = module.parameters.input_params.get(
                    op.field,
                    module.parameters.render_params.get(op.field, "<未设置>"),
                )
                return (
                    f"[update_parameter] 将模块 '{op.target_module}' 的参数 "
                    f"'{op.field}' 从 '{old_val}' 更新为 '{op.value}'"
                )
            return (
                f"[update_parameter] 模块 '{op.target_module}' 不存在，"
                f"无法预览参数 '{op.field}'"
            )

        elif op.op_type == "change_preference":
            existing = next(
                (a for a in ir.intent.assumptions if a.field == op.field),
                None,
            )
            if existing:
                return (
                    f"[change_preference] 将偏好 '{op.field}' 从 "
                    f"'{existing.assumed_value}' 更新为 '{op.value}'"
                )
            return f"[change_preference] 新增偏好/假设 '{op.field}' = '{op.value}'"

        else:
            return f"[{op.op_type}] 未知操作类型，无法预览"
