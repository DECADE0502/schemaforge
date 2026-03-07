"""全局器件实例收集与参考编号分配 (T071-T074)。

从 SystemDesignIR 的所有模块中提取外围元件，分配全局唯一参考编号
（C1, R1, U1 等），并支持多轮修改时编号稳定性。

约束遵循:
- C71 BOM 编号全局唯一
- C72 BOM 编号在多轮修改下尽量稳定
- C78 导出器必须从系统实例表工作
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from schemaforge.system.models import ModuleInstance, SystemDesignIR

logger = logging.getLogger(__name__)


# ============================================================
# T071: ComponentInstance 数据模型
# ============================================================


@dataclass
class ComponentInstance:
    """全局器件实例。

    每个外围元件或主器件在系统中的唯一表示。
    """

    ref: str           # "C1", "R3", "U1"
    module_id: str     # 所属模块 ID
    role: str          # "input_cap", "fb_upper", "main_ic"
    value: str         # "10uF", "18kΩ", "TPS5430"
    package: str       # "0805", "SOIC-8"
    description: str   # "输入电容"


# ============================================================
# 参考编号前缀优先级（C73 注：IC 优先编号）
# ============================================================

_PREFIX_ORDER = ["U", "C", "R", "L", "D"]


def _prefix_sort_key(prefix: str) -> int:
    """返回前缀排序键。_PREFIX_ORDER 中的优先，其余按字母序。"""
    try:
        return _PREFIX_ORDER.index(prefix)
    except ValueError:
        return len(_PREFIX_ORDER) + ord(prefix[0]) if prefix else 999


# ============================================================
# T072: 从 IR 收集所有组件实例
# ============================================================


def _get_device_package(instance: ModuleInstance) -> str:
    """从 DeviceModel 中提取 package，未找到返回空字符串。"""
    device = instance.device
    if device is None:
        return ""
    return getattr(device, "package", "") or ""


def _get_device_part_number(instance: ModuleInstance) -> str:
    """从 DeviceModel 中提取 part_number。"""
    device = instance.device
    if device is None:
        return ""
    return getattr(device, "part_number", "") or ""


def create_component_instances(ir: SystemDesignIR) -> list[ComponentInstance]:
    """收集所有模块的组件到一个扁平列表。

    每个模块的主器件（如果有 device）作为 main_ic 收集，
    每个外围元件按 external_components 收集。

    Args:
        ir: 系统设计 IR

    Returns:
        未分配参考编号的 ComponentInstance 列表（ref 为空）
    """
    instances: list[ComponentInstance] = []

    for module_id, module in ir.module_instances.items():
        # 主器件
        part_number = _get_device_part_number(module)
        if part_number:
            instances.append(ComponentInstance(
                ref="",  # 待分配
                module_id=module_id,
                role="main_ic",
                value=part_number,
                package=_get_device_package(module),
                description=module.role,
            ))

        # 外围元件
        for comp in module.external_components:
            ref_prefix = comp.get("ref_prefix", "")
            role = comp.get("role", "unknown")
            value = comp.get("value", "")
            # 封装默认按前缀推断
            package = comp.get("package", _default_package(ref_prefix))
            description = comp.get("evidence", comp.get("formula", ""))

            instances.append(ComponentInstance(
                ref="",  # 待分配
                module_id=module_id,
                role=role,
                value=value,
                package=package,
                description=description,
            ))

    return instances


def _default_package(ref_prefix: str) -> str:
    """根据前缀返回默认封装。"""
    defaults: dict[str, str] = {
        "C": "0805",
        "R": "0402",
        "L": "1210",
        "D": "SOD-123",
    }
    return defaults.get(ref_prefix, "")


def _ref_prefix_for_instance(inst: ComponentInstance) -> str:
    """推断 ComponentInstance 的参考编号前缀。"""
    if inst.role == "main_ic":
        return "U"

    # 从 role 或 value 推断前缀
    role_lower = inst.role.lower()

    if "cap" in role_lower or "decoupling" in role_lower or "boot" in role_lower:
        return "C"
    if "resistor" in role_lower or "fb_upper" in role_lower or "fb_lower" in role_lower:
        return "R"
    if "inductor" in role_lower:
        return "L"
    if "diode" in role_lower:
        return "D"
    if "led" in role_lower:
        return "D"

    # 回退：从 value 推断
    val = inst.value.upper()
    if val.endswith("UF") or val.endswith("NF") or val.endswith("PF"):
        return "C"
    if "K" in val and "\u03a9" in val:
        return "R"
    if "\u03a9" in val:
        return "R"
    if val.endswith("UH") or val.endswith("MH"):
        return "L"

    return "X"  # 未知类型


# ============================================================
# T073: 全局参考编号分配
# ============================================================


def allocate_global_references(
    instances: list[ComponentInstance],
) -> list[ComponentInstance]:
    """分配全局参考编号 C1-Cn, R1-Rn, L1-Ln, U1-Un, D1-Dn。

    编号顺序：IC (U) 优先，然后 C, R, L, D，其余按字母序。
    同一前缀内按模块 ID 排序以保持确定性（C71）。

    Args:
        instances: 未分配 ref 的 ComponentInstance 列表

    Returns:
        已分配 ref 的列表（原地修改并返回）
    """
    # 按前缀分组
    groups: dict[str, list[ComponentInstance]] = {}
    for inst in instances:
        prefix = _ref_prefix_for_instance(inst)
        groups.setdefault(prefix, []).append(inst)

    # 按优先级排序前缀
    sorted_prefixes = sorted(groups.keys(), key=_prefix_sort_key)

    for prefix in sorted_prefixes:
        group = groups[prefix]
        # 组内按 module_id 排序保持确定性
        group.sort(key=lambda x: (x.module_id, x.role))
        for idx, inst in enumerate(group, start=1):
            inst.ref = f"{prefix}{idx}"

    return instances


# ============================================================
# T074: 修订后编号稳定
# ============================================================


def stabilize_references_after_revision(
    old_instances: list[ComponentInstance],
    new_instances: list[ComponentInstance],
) -> list[ComponentInstance]:
    """尝试为未变更的组件保留旧参考编号（C72）。

    匹配策略：相同 module_id + role + value 视为同一组件。
    未匹配到旧编号的组件获得新编号（从旧最大值+1 开始）。

    Args:
        old_instances: 上一轮已分配 ref 的实例列表
        new_instances: 本轮未分配 ref 的实例列表

    Returns:
        已分配 ref 的 new_instances（原地修改并返回）
    """
    # 构建旧实例查找表：(module_id, role, value) -> ref
    old_lookup: dict[tuple[str, str, str], str] = {}
    for inst in old_instances:
        key = (inst.module_id, inst.role, inst.value)
        old_lookup[key] = inst.ref

    # 统计每个前缀的已用最大编号
    used_refs: set[str] = set()
    max_num: dict[str, int] = {}

    for inst in old_instances:
        used_refs.add(inst.ref)
        prefix = _ref_prefix_for_instance(inst)
        # 从 ref 中提取数字
        num_str = inst.ref.lstrip("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
        if num_str.isdigit():
            num = int(num_str)
            max_num[prefix] = max(max_num.get(prefix, 0), num)

    # 第一遍：匹配旧编号
    unmatched: list[ComponentInstance] = []
    for inst in new_instances:
        key = (inst.module_id, inst.role, inst.value)
        if key in old_lookup:
            inst.ref = old_lookup[key]
            used_refs.add(inst.ref)
        else:
            unmatched.append(inst)

    # 第二遍：为未匹配的分配新编号
    for inst in unmatched:
        prefix = _ref_prefix_for_instance(inst)
        next_num = max_num.get(prefix, 0) + 1
        while f"{prefix}{next_num}" in used_refs:
            next_num += 1
        inst.ref = f"{prefix}{next_num}"
        used_refs.add(inst.ref)
        max_num[prefix] = next_num

    return new_instances
