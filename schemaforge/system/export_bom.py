"""系统级 BOM 导出 (T075-T077)。

从全局 ComponentInstance 列表生成 Markdown 和 CSV 格式的 BOM 表。

约束遵循:
- C71 BOM 编号全局唯一
- C76 BOM 导出不能依赖 SVG 文本反推
- C78 导出器必须从系统实例表工作
- C79 全局导出必须晚于连接解析完成
- C80 缺件时 BOM/SPICE 必须能部分导出但要显式标红
"""

from __future__ import annotations

import csv
import io
import logging

from schemaforge.system.instances import ComponentInstance
from schemaforge.system.models import SystemDesignIR

logger = logging.getLogger(__name__)


# ============================================================
# T075: Markdown BOM
# ============================================================


def export_system_bom_markdown(
    instances: list[ComponentInstance],
    ir: SystemDesignIR,
) -> str:
    """生成 Markdown 格式的 BOM 表。

    列: 位号, 名称, 数值, 封装, 所属模块, 备注

    Args:
        instances: 已分配 ref 的全局 ComponentInstance 列表
        ir: 系统设计 IR（用于提取模块角色等信息）

    Returns:
        Markdown 表格字符串
    """
    lines: list[str] = []

    # 表头
    lines.append("| 位号 | 名称 | 数值 | 封装 | 所属模块 | 备注 |")
    lines.append("|------|------|------|------|----------|------|")

    # 按 ref 排序输出
    sorted_instances = sorted(instances, key=_bom_sort_key)

    for inst in sorted_instances:
        module = ir.get_module(inst.module_id)
        module_label = module.role if module else inst.module_id

        # C80: 缺件标红
        ref_display = inst.ref
        value_display = inst.value
        if not inst.value:
            value_display = "**[缺失]**"

        lines.append(
            f"| {ref_display} "
            f"| {inst.role} "
            f"| {value_display} "
            f"| {inst.package} "
            f"| {module_label} "
            f"| {inst.description} |",
        )

    # 汇总
    lines.append("")
    lines.append(f"共 {len(instances)} 个器件")

    # 统计未解析模块
    unresolved = ir.get_unresolved_modules()
    if unresolved:
        lines.append("")
        lines.append(f"**警告**: {len(unresolved)} 个模块未解析，BOM 可能不完整")
        for m in unresolved:
            lines.append(f"- {m.module_id}: {m.missing_part_number or m.status.value}")

    return "\n".join(lines)


# ============================================================
# T076: CSV BOM
# ============================================================


def export_system_bom_csv(instances: list[ComponentInstance]) -> str:
    """生成 CSV 格式的 BOM。

    列: Ref,Value,Package,Module,Description

    Args:
        instances: 已分配 ref 的全局 ComponentInstance 列表

    Returns:
        CSV 字符串
    """
    output = io.StringIO()
    writer = csv.writer(output)

    # 表头
    writer.writerow(["Ref", "Value", "Package", "Module", "Description"])

    # 按 ref 排序
    sorted_instances = sorted(instances, key=_bom_sort_key)

    for inst in sorted_instances:
        writer.writerow([
            inst.ref,
            inst.value,
            inst.package,
            inst.module_id,
            inst.description,
        ])

    return output.getvalue()


# ============================================================
# 内部排序
# ============================================================


def _bom_sort_key(inst: ComponentInstance) -> tuple[int, str, int]:
    """BOM 排序键：按前缀优先级 -> 前缀字母 -> 编号数字。"""
    prefix = ""
    num = 0
    for i, ch in enumerate(inst.ref):
        if ch.isdigit():
            prefix = inst.ref[:i]
            num = int(inst.ref[i:])
            break
    else:
        prefix = inst.ref

    prefix_order = {"U": 0, "C": 1, "R": 2, "L": 3, "D": 4}
    order = prefix_order.get(prefix, 9)
    return (order, prefix, num)
