"""系统级 SPICE 网表导出 (T078-T079)。

从 SystemDesignIR 和全局 ComponentInstance 列表生成 SPICE 网表。

约束遵循:
- C73 SPICE 节点名与系统 net 一一对应
- C74 Buck VOUT 和 LDO VIN 若同网，SPICE 必须同节点
- C75 没有模型的器件必须明确标注
- C77 SPICE 导出不能依赖渲染坐标
- C78 导出器必须从系统实例表工作
- C79 全局导出必须晚于连接解析完成
- C80 缺件时 SPICE 必须能部分导出但要显式标红
"""

from __future__ import annotations

import logging
from typing import Any

from schemaforge.system.instances import ComponentInstance
from schemaforge.system.models import SystemDesignIR

logger = logging.getLogger(__name__)


# ============================================================
# T079: 系统网络到 SPICE 节点映射
# ============================================================


def map_system_nets_to_spice_nodes(ir: SystemDesignIR) -> dict[str, str]:
    """将 SystemNet.net_name 映射到 SPICE 节点名。

    规则:
    - GND -> '0' (SPICE 地节点)
    - 其他网络保持 net_name 不变（SPICE 支持字符串节点名）

    C73: SPICE 节点名与系统 net 一一对应
    C74: 共享网络使用同一节点名

    Args:
        ir: 系统设计 IR

    Returns:
        net_name -> SPICE node name 映射
    """
    mapping: dict[str, str] = {}

    for net_id, net in ir.nets.items():
        name = net.net_name
        if name.upper() == "GND":
            mapping[name] = "0"
        else:
            # 清理名称：SPICE 节点名不能含空格
            spice_name = name.replace(" ", "_")
            mapping[name] = spice_name

    # 确保 GND 始终存在
    if "GND" not in mapping:
        mapping["GND"] = "0"

    return mapping


# ============================================================
# T078: SPICE 网表生成
# ============================================================


def _get_spice_model_template(instance: Any) -> str:
    """从 ModuleInstance.device 获取 spice_model 模板字符串。"""
    if instance is None:
        return ""
    device = getattr(instance, "device", None)
    if device is None:
        return ""
    return getattr(device, "spice_model", "") or ""


def _format_passive_spice(
    inst: ComponentInstance,
    node_a: str,
    node_b: str,
) -> str:
    """生成被动元件的 SPICE 语句。

    - R: R{ref} node_a node_b value
    - C: C{ref} node_a node_b value
    - L: L{ref} node_a node_b value
    """
    ref = inst.ref
    value = _normalize_spice_value(inst.value)
    prefix = ref[0] if ref else "X"

    if prefix in ("R", "C", "L"):
        return f"{ref} {node_a} {node_b} {value}"

    # Diode
    if prefix == "D":
        model_name = inst.value if inst.value else "D_GENERIC"
        return f"{ref} {node_a} {node_b} {model_name}"

    return f"* {ref} {node_a} {node_b} {value}  ; unknown type"


def _normalize_spice_value(value: str) -> str:
    """将工程值转为 SPICE 兼容格式。

    例如: "10uF" -> "10u", "4.7kΩ" -> "4.7k", "22uH" -> "22u"
    """
    if not value:
        return "0"

    # 移除单位后缀
    result = value
    for suffix in ("F", "H", "\u03a9", "ohm", "Ohm"):
        result = result.replace(suffix, "")

    # 清理空白
    result = result.strip()

    return result if result else value


def export_system_spice(
    ir: SystemDesignIR,
    instances: list[ComponentInstance],
) -> str:
    """生成完整 SPICE 网表。

    结构:
    1. 标题行
    2. 节点映射注释
    3. IC 子电路调用（使用 spice_model 模板）
    4. 被动元件
    5. .end

    Args:
        ir: 系统设计 IR
        instances: 已分配 ref 的全局 ComponentInstance 列表

    Returns:
        SPICE 网表字符串
    """
    lines: list[str] = []

    # 标题
    lines.append("* SchemaForge System SPICE Netlist")
    lines.append("*")

    # 节点映射（C73）
    node_map = map_system_nets_to_spice_nodes(ir)
    lines.append("* Node mapping:")
    for net_name, spice_node in sorted(node_map.items()):
        lines.append(f"*   {net_name} -> {spice_node}")
    lines.append("*")

    # 分类实例
    ic_instances: list[ComponentInstance] = []
    passive_instances: list[ComponentInstance] = []

    for inst in instances:
        if inst.ref.startswith("U"):
            ic_instances.append(inst)
        else:
            passive_instances.append(inst)

    # IC 子电路调用
    if ic_instances:
        lines.append("* === IC Subcircuits ===")
        for inst in sorted(ic_instances, key=lambda x: x.ref):
            module = ir.get_module(inst.module_id)
            spice_template = _get_spice_model_template(module)

            if spice_template:
                # 替换模板中的占位符
                spice_line = _expand_spice_template(
                    spice_template, inst, module, node_map,
                )
                lines.append(spice_line)
            else:
                # C75: 没有模型的器件明确标注
                lines.append(
                    f"* WARNING: No SPICE model for {inst.ref} "
                    f"({inst.value}) in module {inst.module_id}",
                )
        lines.append("*")

    # 被动元件
    if passive_instances:
        lines.append("* === Passive Components ===")
        for inst in sorted(passive_instances, key=lambda x: x.ref):
            module = ir.get_module(inst.module_id)
            node_a, node_b = _resolve_passive_nodes(inst, module, node_map, ir)
            lines.append(_format_passive_spice(inst, node_a, node_b))
        lines.append("*")

    # 未解析警告（C80）
    unresolved = ir.get_unresolved_modules()
    if unresolved:
        lines.append("* === Unresolved Modules (partial export) ===")
        for m in unresolved:
            lines.append(
                f"* MISSING: {m.module_id} ({m.missing_part_number or m.status.value})",
            )
        lines.append("*")

    # 结束
    lines.append(".end")

    return "\n".join(lines)


# ============================================================
# 内部辅助
# ============================================================


def _expand_spice_template(
    template: str,
    inst: ComponentInstance,
    module: Any,
    node_map: dict[str, str],
) -> str:
    """展开 SPICE 模型模板中的占位符。

    模板格式: "XU{ref} {VIN} {GND} {EN} {BST} {SW} {FB} TPS5430"
    占位符 {PIN_NAME} 替换为对应网络的 SPICE 节点名。
    """
    result = template

    # 替换 {ref}
    result = result.replace("{ref}", inst.ref.lstrip("U"))

    if module is None:
        return result

    # 从模块的 resolved_ports 和网络信息构建引脚到节点映射
    pin_to_node: dict[str, str] = {}

    # 遍历模块端口，查找对应的网络
    for port_key, port in module.resolved_ports.items():
        pin_name = port.pin_name
        # 在 IR 的网络中查找包含此端口的网络
        node = _find_node_for_pin(port.module_id, pin_name, node_map, module)
        pin_to_node[pin_name] = node

    # 替换模板中的引脚占位符
    for pin_name, node in pin_to_node.items():
        result = result.replace(f"{{{pin_name}}}", node)

    return result


def _find_node_for_pin(
    module_id: str,
    pin_name: str,
    node_map: dict[str, str],
    module: Any,
) -> str:
    """查找引脚对应的 SPICE 节点名。"""
    # GND 引脚直接映射
    if pin_name.upper() in ("GND", "VSS"):
        return "0"

    # 检查 node_map 中是否有以引脚名或模块名命名的网络
    for net_name, spice_node in node_map.items():
        if pin_name.upper() in net_name.upper():
            return spice_node

    # 回退：使用 module_id_pin_name 作为节点名
    return f"{module_id}_{pin_name}"


def _resolve_passive_nodes(
    inst: ComponentInstance,
    module: Any,
    node_map: dict[str, str],
    ir: SystemDesignIR,
) -> tuple[str, str]:
    """为被动元件推断两端节点。

    基于元件的 role 和所属模块来推断连接关系。
    """
    role = inst.role.lower()
    module_id = inst.module_id

    # 查找模块对应的网络
    module_nets: list[str] = []
    if module:
        for net in ir.get_nets_for_module(module_id):
            module_nets.append(net.net_name)

    # 根据角色推断节点
    if "input_cap" in role:
        node_a = _find_power_input_net(module_id, node_map, ir)
        node_b = "0"  # GND
    elif "output_cap" in role:
        node_a = _find_power_output_net(module_id, node_map, ir)
        node_b = "0"  # GND
    elif "boot" in role:
        node_a = f"{module_id}_BST"
        node_b = f"{module_id}_SW"
    elif "fb_upper" in role:
        node_a = _find_power_output_net(module_id, node_map, ir)
        node_b = f"{module_id}_FB"
    elif "fb_lower" in role:
        node_a = f"{module_id}_FB"
        node_b = "0"  # GND
    elif "inductor" in role:
        node_a = f"{module_id}_SW"
        node_b = _find_power_output_net(module_id, node_map, ir)
    elif "diode" in role:
        node_a = "0"  # anode to GND
        node_b = f"{module_id}_SW"  # cathode to SW
    elif "decoupling" in role or "bulk" in role:
        node_a = _find_power_input_net(module_id, node_map, ir)
        node_b = "0"  # GND
    elif "led_resistor" in role or "led_limit" in role:
        node_a = f"{module_id}_ANODE"
        node_b = f"{module_id}_LED"
    else:
        # 通用回退
        node_a = f"{module_id}_A"
        node_b = "0"

    return (node_a, node_b)


def _find_power_input_net(
    module_id: str,
    node_map: dict[str, str],
    ir: SystemDesignIR,
) -> str:
    """查找模块电源输入所在的网络节点名。"""
    for net in ir.get_nets_for_module(module_id):
        for member in net.members:
            if member.module_id == module_id and member.port_role == "power_in":
                return node_map.get(net.net_name, net.net_name)

    return f"{module_id}_VIN"


def _find_power_output_net(
    module_id: str,
    node_map: dict[str, str],
    ir: SystemDesignIR,
) -> str:
    """查找模块电源输出所在的网络节点名。"""
    for net in ir.get_nets_for_module(module_id):
        for member in net.members:
            if member.module_id == module_id and member.port_role == "power_out":
                return node_map.get(net.net_name, net.net_name)

    return f"{module_id}_VOUT"
