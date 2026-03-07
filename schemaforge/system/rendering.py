"""系统级渲染：将 SystemDesignIR 渲染为单一 SVG 原理图。

所有模块绘制在同一个 schemdraw.Drawing 中，然后输出为一个 SVG 文件。

约束遵循:
- C61 渲染必须基于系统 IR，而不是基于字符串模板
- C62 同一系统只能输出一张主 SVG
- C63 模块布局必须稳定，局部修改不能整图乱跳
- C64 全局参考编号必须一致
- C65 net label 必须与系统 net 保持一致
- C66 GND 视觉表示必须统一
- C67 缺失模块必须可视化为占位块
- C68 unresolved 连接必须有可视提示
- C69 控制支路应区别于主电源链布局
- C70 原理图中每条连接都必须能在 IR 中找到来源
"""

from __future__ import annotations

import logging

import schemdraw
import schemdraw.elements as elm

from schemaforge.render.base import output_path
from schemaforge.system.models import (
    ModuleInstance,
    ModuleStatus,
    NetType,
    ResolvedConnection,
    SystemDesignIR,
    SystemNet,
)

logger = logging.getLogger(__name__)

# ============================================================
# 布局常量
# ============================================================

_MODULE_X_SPACING = 20.0   # 模块间水平间距（增大避免重叠）
_MODULE_Y_OFFSET = 0.0     # 电源链 Y 基线
_CONTROL_Y_OFFSET = -16.0  # 控制支路 Y 偏移（在电源链下方，加大间距）
_CONTROL_X_SPACING = 10.0  # 控制支路模块间距

# 电源链类别（参与主链条横向排列）
_POWER_CATEGORIES: set[str] = {"buck", "ldo", "boost", "flyback", "sepic"}


# ============================================================
# T085: 全局参考编号分配器 (C64)
# ============================================================


def _build_global_ref_map(
    ir: SystemDesignIR,
) -> dict[str, dict[str, str]]:
    """为所有模块的外围元件分配全局唯一参考编号。

    Returns:
        module_id -> {role: global_ref}  例如 {"buck1": {"input_cap": "C1"}}
    """
    counters: dict[str, int] = {}  # prefix -> next number
    ref_map: dict[str, dict[str, str]] = {}

    for module_id, instance in ir.module_instances.items():
        module_refs: dict[str, str] = {}
        for comp in instance.external_components:
            role = comp.get("role", "")
            comp_type = comp.get("type", "")
            ref_prefix = comp.get("ref_prefix", "")

            if not ref_prefix:
                if comp_type in ("capacitor",) or "cap" in role:
                    ref_prefix = "C"
                elif comp_type in ("resistor",) or "resistor" in role or "pullup" in role:
                    ref_prefix = "R"
                elif comp_type in ("inductor",):
                    ref_prefix = "L"
                elif comp_type in ("resistor_divider",) or "divider" in role:
                    ref_prefix = "R"
                else:
                    ref_prefix = "X"

            idx = counters.get(ref_prefix, 1)
            counters[ref_prefix] = idx + 1
            ref_name = f"{ref_prefix}{idx}"
            module_refs[role] = ref_name

        ref_map[module_id] = module_refs

    return ref_map


# ============================================================
# T082: Module layout functions
# ============================================================


def _draw_ic_block(
    d: schemdraw.Drawing,
    instance: ModuleInstance,
    origin: tuple[float, float],
    label: str,
    pins: list[dict[str, str]],
    size: tuple[float, float] = (4, 3),
) -> elm.Ic:
    """绘制一个 IC 方块到 Drawing 中。

    Args:
        d: 目标 Drawing
        instance: 模块实例
        origin: 放置锚点 (x, y)
        label: IC 标签
        pins: IC 引脚定义列表，每项 {"name", "side", "slot"}
        size: IC 尺寸

    Returns:
        绘制完成的 Ic 元件（含锚点信息）
    """
    ic_pins = []
    for p in pins:
        ic_pins.append(
            elm.IcPin(
                name=p["name"],
                side=p["side"],
                slot=p.get("slot", ""),
            ),
        )

    ic = elm.Ic(
        pins=ic_pins,
        size=size,
    ).at(origin).anchor("inL1").label(label, "top")

    return ic


def draw_power_module(
    d: schemdraw.Drawing,
    instance: ModuleInstance,
    origin: tuple[float, float],
    ref_map: dict[str, str],
) -> dict[str, tuple[float, float]]:
    """Draw a power module (Buck/LDO/Boost) into the Drawing.

    Returns anchor points: {"VIN": (x,y), "VOUT": (x,y), "GND": (x,y)}
    """
    category = instance.resolved_category.lower()
    module_id = instance.module_id
    part = ""
    if instance.device is not None:
        part = getattr(instance.device, "part_number", module_id)
    label = part or module_id

    anchors: dict[str, tuple[float, float]] = {}

    if category == "buck":
        anchors = _draw_buck_module(d, instance, origin, ref_map, label)
    elif category == "ldo":
        anchors = _draw_ldo_module(d, instance, origin, ref_map, label)
    elif category in ("boost", "flyback", "sepic"):
        # 与 buck 共享基本布局，只是标签不同
        anchors = _draw_buck_module(d, instance, origin, ref_map, label)
    else:
        anchors = _draw_generic_power_module(d, instance, origin, ref_map, label)

    return anchors


def _draw_buck_module(
    d: schemdraw.Drawing,
    instance: ModuleInstance,
    origin: tuple[float, float],
    ref_map: dict[str, str],
    label: str,
) -> dict[str, tuple[float, float]]:
    """绘制 Buck 转换器模块。"""
    ox, oy = origin

    # 输入电容
    c_in_ref = ref_map.get("input_cap", "C?")
    vin_pos = (ox, oy)

    elm.Dot(open=True).at(vin_pos).label("VIN", "left")
    elm.Line().at(vin_pos).right(1)
    cap_top = (ox + 1, oy)
    elm.Dot().at(cap_top)

    d.push()
    elm.Capacitor(polar=True).at(cap_top).down().label(
        f"{c_in_ref}\n10\u03bcF", "right",
    )
    elm.Ground()
    d.pop()

    # IC
    ic = elm.Ic(
        pins=[
            elm.IcPin(name="VIN", side="left", slot="1/2"),
            elm.IcPin(name="EN", side="left", slot="2/2"),
            elm.IcPin(name="GND", side="bottom", slot="1/1"),
            elm.IcPin(name="SW", side="right", slot="1/2"),
            elm.IcPin(name="FB", side="right", slot="2/2"),
        ],
        size=(4, 3),
    ).at((ox + 2, oy)).anchor("inL1").label(label, "top")

    # GND
    d.push()
    elm.Line().at(ic.inB1).down(1)  # type: ignore[call-arg]
    gnd_ic = d.here
    elm.Ground()
    d.pop()

    # SW -> 电感 -> VOUT
    elm.Line().at(ic.inR1).right(0.5)  # type: ignore[call-arg]
    ind_start = d.here

    l_ref = ref_map.get("inductor", "L?")
    elm.Inductor2().at(ind_start).right().label(f"{l_ref}\n4.7\u03bcH", "top")

    # 输出电容
    c_out_ref = ref_map.get("output_cap", "C?")
    elm.Dot()
    d.push()
    elm.Capacitor(polar=True).down().label(f"{c_out_ref}\n22\u03bcF", "right")
    elm.Ground()
    d.pop()

    # VOUT 标记
    v_out = instance.parameters.get("v_out", "?")
    elm.Line().right(1)
    vout_end = d.here
    elm.Dot(open=True).label(f"VOUT\n{v_out}V", "right")

    anchors = {
        "VIN": (ox, oy),
        "VOUT": (vout_end.x, vout_end.y),
        "GND": (gnd_ic.x, gnd_ic.y),
    }
    return anchors


def _draw_ldo_module(
    d: schemdraw.Drawing,
    instance: ModuleInstance,
    origin: tuple[float, float],
    ref_map: dict[str, str],
    label: str,
) -> dict[str, tuple[float, float]]:
    """绘制 LDO 模块。"""
    ox, oy = origin

    # 输入
    vin_pos = (ox, oy)
    elm.Dot(open=True).at(vin_pos).label("VIN", "left")
    elm.Line().at(vin_pos).right(1)
    cap_top = (ox + 1, oy)
    elm.Dot().at(cap_top)

    c_in_ref = ref_map.get("input_cap", "C?")
    d.push()
    elm.Capacitor(polar=True).at(cap_top).down().label(
        f"{c_in_ref}\n10\u03bcF", "right",
    )
    elm.Ground()
    d.pop()

    # IC (3-pin LDO)
    ic = elm.Ic(
        pins=[
            elm.IcPin(name="VIN", side="left", slot="1/1"),
            elm.IcPin(name="GND", side="bottom", slot="1/1"),
            elm.IcPin(name="VOUT", side="right", slot="1/1"),
        ],
        size=(3, 2),
    ).at((ox + 2, oy)).anchor("inL1").label(label, "top")

    # GND
    d.push()
    elm.Line().at(ic.inB1).down(1)  # type: ignore[call-arg]
    gnd_ic = d.here
    elm.Ground()
    d.pop()

    # 输出电容
    elm.Line().at(ic.inR1).right(1)  # type: ignore[call-arg]
    c_out_ref = ref_map.get("output_cap", "C?")
    elm.Dot()
    d.push()
    elm.Capacitor(polar=True).down().label(f"{c_out_ref}\n22\u03bcF", "right")
    elm.Ground()
    d.pop()

    # VOUT
    v_out = instance.parameters.get("v_out", "?")
    elm.Line().right(1)
    vout_end = d.here
    elm.Dot(open=True).label(f"VOUT\n{v_out}V", "right")

    anchors = {
        "VIN": (ox, oy),
        "VOUT": (vout_end.x, vout_end.y),
        "GND": (gnd_ic.x, gnd_ic.y),
    }
    return anchors


def _draw_generic_power_module(
    d: schemdraw.Drawing,
    instance: ModuleInstance,
    origin: tuple[float, float],
    ref_map: dict[str, str],
    label: str,
) -> dict[str, tuple[float, float]]:
    """绘制通用电源模块（不是 buck/ldo/boost 的情况）。"""
    ox, oy = origin

    vin_pos = (ox, oy)
    elm.Dot(open=True).at(vin_pos).label("VIN", "left")
    elm.Line().at(vin_pos).right(1)

    ic = elm.Ic(
        pins=[
            elm.IcPin(name="IN", side="left", slot="1/1"),
            elm.IcPin(name="GND", side="bottom", slot="1/1"),
            elm.IcPin(name="OUT", side="right", slot="1/1"),
        ],
        size=(3, 2),
    ).at((ox + 1, oy)).anchor("inL1").label(label, "top")

    d.push()
    elm.Line().at(ic.inB1).down(1)  # type: ignore[call-arg]
    gnd_ic = d.here
    elm.Ground()
    d.pop()

    elm.Line().at(ic.inR1).right(1)  # type: ignore[call-arg]
    vout_end = d.here
    v_out = instance.parameters.get("v_out", "?")
    elm.Dot(open=True).label(f"OUT\n{v_out}V", "right")

    return {
        "VIN": (ox, oy),
        "VOUT": (vout_end.x, vout_end.y),
        "GND": (gnd_ic.x, gnd_ic.y),
    }


# ============================================================
# T082: 控制模块绘制
# ============================================================


def draw_control_module(
    d: schemdraw.Drawing,
    instance: ModuleInstance,
    origin: tuple[float, float],
    ref_map: dict[str, str],
) -> dict[str, tuple[float, float]]:
    """Draw a control module (MCU/LED/other) into the Drawing.

    Returns anchor points: {"VDD": (x,y), "GND": (x,y), "PA0": (x,y), ...}
    """
    category = instance.resolved_category.lower()
    module_id = instance.module_id
    part = ""
    if instance.device is not None:
        part = getattr(instance.device, "part_number", module_id)
    label = part or module_id

    if category == "mcu":
        return _draw_mcu_module(d, instance, origin, ref_map, label)
    elif category == "led":
        return _draw_led_module(d, instance, origin, ref_map, label)
    else:
        return _draw_generic_control_module(d, instance, origin, ref_map, label)


def _draw_mcu_module(
    d: schemdraw.Drawing,
    instance: ModuleInstance,
    origin: tuple[float, float],
    ref_map: dict[str, str],
    label: str,
) -> dict[str, tuple[float, float]]:
    """绘制 MCU 模块。"""
    ox, oy = origin

    # 收集 GPIO 引脚
    gpio_pins = [
        name for name, port in instance.resolved_ports.items()
        if port.port_role == "gpio"
    ]

    pins = [
        elm.IcPin(name="VDD", side="left", slot="1/2"),
        elm.IcPin(name="GND", side="bottom", slot="1/1"),
    ]

    # 添加 GPIO 到右侧
    for i, gpio_name in enumerate(gpio_pins[:4]):  # 最多显示 4 个 GPIO
        pins.append(
            elm.IcPin(
                name=gpio_name,
                side="right",
                slot=f"{i + 1}/{min(len(gpio_pins), 4)}",
            ),
        )

    # EN 引脚（如果有）
    if any(p.port_role == "enable" for p in instance.resolved_ports.values()):
        pins.append(elm.IcPin(name="EN", side="left", slot="2/2"))

    ic = elm.Ic(
        pins=pins,
        size=(4, 3),
    ).at(origin).anchor("inL1").label(label, "top")

    # GND
    d.push()
    elm.Line().at(ic.inB1).down(1)  # type: ignore[call-arg]
    gnd_pos = d.here
    elm.Ground()
    d.pop()

    anchors: dict[str, tuple[float, float]] = {
        "VDD": (ic.inL1.x, ic.inL1.y),  # type: ignore[union-attr]
        "GND": (gnd_pos.x, gnd_pos.y),
    }

    # GPIO 锚点
    for i, gpio_name in enumerate(gpio_pins[:4]):
        anchor_name = f"inR{i + 1}"
        try:
            pos = getattr(ic, anchor_name)
            anchors[gpio_name] = (pos.x, pos.y)
        except AttributeError:
            pass

    return anchors


def _draw_led_module(
    d: schemdraw.Drawing,
    instance: ModuleInstance,
    origin: tuple[float, float],
    ref_map: dict[str, str],
    label: str,
) -> dict[str, tuple[float, float]]:
    """绘制 LED 指示灯模块。"""
    ox, oy = origin

    # LED: 限流电阻 → LED → GND (垂直向下)
    r_ref = ref_map.get("led_limit", "R?")
    led_color = instance.parameters.get("led_color", "green")

    anode_pos = (ox, oy)
    elm.Dot().at(anode_pos)
    elm.Resistor().at(anode_pos).down().label(f"{r_ref}", "right")
    elm.LED().down().label(f"LED\n{led_color}", "right")
    gnd_pos = d.here
    elm.Ground()

    return {
        "ANODE": (ox, oy),
        "GND": (gnd_pos.x, gnd_pos.y),
    }


def _draw_generic_control_module(
    d: schemdraw.Drawing,
    instance: ModuleInstance,
    origin: tuple[float, float],
    ref_map: dict[str, str],
    label: str,
) -> dict[str, tuple[float, float]]:
    """绘制通用控制模块。"""
    ox, oy = origin

    ic = elm.Ic(
        pins=[
            elm.IcPin(name="IN", side="left", slot="1/1"),
            elm.IcPin(name="GND", side="bottom", slot="1/1"),
            elm.IcPin(name="OUT", side="right", slot="1/1"),
        ],
        size=(3, 2),
    ).at(origin).anchor("inL1").label(label, "top")

    d.push()
    elm.Line().at(ic.inB1).down(1)  # type: ignore[call-arg]
    gnd_pos = d.here
    elm.Ground()
    d.pop()

    return {
        "IN": (ic.inL1.x, ic.inL1.y),  # type: ignore[union-attr]
        "OUT": (ic.inR1.x, ic.inR1.y),  # type: ignore[union-attr]
        "GND": (gnd_pos.x, gnd_pos.y),
    }


# ============================================================
# T082: 占位块（C67 缺失模块可视化）
# ============================================================


def _draw_placeholder_module(
    d: schemdraw.Drawing,
    instance: ModuleInstance,
    origin: tuple[float, float],
) -> dict[str, tuple[float, float]]:
    """绘制占位方块（用于未解析/缺失的模块）。"""
    ox, oy = origin
    label = f"[{instance.module_id}]\n{instance.missing_part_number or '?'}"

    ic = elm.Ic(
        pins=[
            elm.IcPin(name="IN", side="left", slot="1/1"),
            elm.IcPin(name="GND", side="bottom", slot="1/1"),
            elm.IcPin(name="OUT", side="right", slot="1/1"),
        ],
        size=(3, 2),
    ).at(origin).anchor("inL1").label(label, "top")

    d.push()
    elm.Line().at(ic.inB1).down(1)  # type: ignore[call-arg]
    gnd_pos = d.here
    elm.Ground()
    d.pop()

    return {
        "VIN": (ic.inL1.x, ic.inL1.y),  # type: ignore[union-attr]
        "VOUT": (ic.inR1.x, ic.inR1.y),  # type: ignore[union-attr]
        "GND": (gnd_pos.x, gnd_pos.y),
    }


# ============================================================
# T083: Power chain layout
# ============================================================


def layout_power_chain(
    d: schemdraw.Drawing,
    ir: SystemDesignIR,
    ref_map: dict[str, dict[str, str]],
) -> dict[str, dict[str, tuple[float, float]]]:
    """Layout all power chain modules left-to-right.

    Returns: module_id -> {port_role: (x,y)}
    """
    all_anchors: dict[str, dict[str, tuple[float, float]]] = {}

    # 确定电源链中的模块（按 placement_hint 或类别）
    power_modules = _order_power_chain(ir)

    x_cursor = 0.0
    for instance in power_modules:
        module_id = instance.module_id
        module_refs = ref_map.get(module_id, {})

        if instance.status in (ModuleStatus.PENDING, ModuleStatus.NEEDS_ASSET, ModuleStatus.ERROR):
            # C67: 缺失模块用占位块
            anchors = _draw_placeholder_module(d, instance, (x_cursor, _MODULE_Y_OFFSET))
        else:
            anchors = draw_power_module(
                d, instance, (x_cursor, _MODULE_Y_OFFSET), module_refs,
            )

        all_anchors[module_id] = anchors
        x_cursor += _MODULE_X_SPACING

    return all_anchors


def _order_power_chain(ir: SystemDesignIR) -> list[ModuleInstance]:
    """按电源链拓扑排序模块。

    通过连接关系推导顺序：没有上游电源的模块排在前面。
    C63: 布局稳定 — 使用确定性排序。
    """
    power_modules = [
        inst for inst in ir.module_instances.values()
        if inst.resolved_category.lower() in _POWER_CATEGORIES
    ]

    if not power_modules:
        return []

    # 构建供电依赖图: dst_module -> src_module (电源链)
    fed_by: dict[str, str] = {}
    for conn in ir.connections:
        if conn.rule_id == "RULE_POWER_SUPPLY":
            src_mod = conn.src_port.module_id
            dst_mod = conn.dst_port.module_id
            fed_by[dst_mod] = src_mod

    # 拓扑排序：找到没有上游的模块作为起点
    power_ids = {m.module_id for m in power_modules}
    ordered: list[ModuleInstance] = []
    visited: set[str] = set()

    def _visit(mid: str) -> None:
        if mid in visited or mid not in power_ids:
            return
        # 先访问上游
        upstream = fed_by.get(mid)
        if upstream and upstream not in visited:
            _visit(upstream)
        visited.add(mid)
        inst = ir.module_instances.get(mid)
        if inst is not None:
            ordered.append(inst)

    # 确定性遍历：按 module_id 字典序
    for mid in sorted(power_ids):
        _visit(mid)

    return ordered


# ============================================================
# T084: Control side layout
# ============================================================


def layout_control_side(
    d: schemdraw.Drawing,
    ir: SystemDesignIR,
    power_anchors: dict[str, dict[str, tuple[float, float]]],
    ref_map: dict[str, dict[str, str]],
) -> None:
    """Layout MCU, LED, and other control modules below/beside the power chain.

    C69: 控制支路区别于主电源链布局。
    """
    control_modules = [
        inst for inst in ir.module_instances.values()
        if inst.resolved_category.lower() not in _POWER_CATEGORIES
    ]

    if not control_modules:
        return

    # 控制模块放在电源链下方
    # 确定 X 起点：与最后一个电源模块对齐或从 0 开始
    x_start = 0.0
    if power_anchors:
        # 所有电源模块的 VOUT x 坐标的最大值
        max_vout_x = max(
            anchors.get("VOUT", (0, 0))[0]
            for anchors in power_anchors.values()
        )
        # 控制模块起点在电源链中间
        x_start = max_vout_x / 2 if max_vout_x > 0 else 0.0

    # 按 module_id 排序确保稳定布局 (C63)
    control_modules.sort(key=lambda m: m.module_id)

    x_cursor = x_start
    for instance in control_modules:
        module_id = instance.module_id
        module_refs = ref_map.get(module_id, {})
        origin = (x_cursor, _CONTROL_Y_OFFSET)

        if instance.status in (ModuleStatus.PENDING, ModuleStatus.NEEDS_ASSET, ModuleStatus.ERROR):
            anchors = _draw_placeholder_module(d, instance, origin)
        else:
            anchors = draw_control_module(d, instance, origin, module_refs)

        power_anchors[module_id] = anchors
        x_cursor += _CONTROL_X_SPACING


# ============================================================
# T086: Inter-module wires
# ============================================================


def draw_intermodule_wires(
    d: schemdraw.Drawing,
    connections: list[ResolvedConnection],
    all_anchors: dict[str, dict[str, tuple[float, float]]],
) -> None:
    """Draw wires between modules using resolved connections and anchor points.

    C70: 每条连接都在 IR 中有来源。
    C68: unresolved 连接的可视提示由调用方处理。
    """
    for conn in connections:
        src_module = conn.src_port.module_id
        dst_module = conn.dst_port.module_id

        src_anchors = all_anchors.get(src_module, {})
        dst_anchors = all_anchors.get(dst_module, {})

        if not src_anchors or not dst_anchors:
            continue

        # 找到最佳匹配的锚点
        src_pos = _find_best_anchor(src_anchors, conn.src_port.pin_name, "VOUT")
        dst_pos = _find_best_anchor(dst_anchors, conn.dst_port.pin_name, "VIN")

        if src_pos is None or dst_pos is None:
            continue

        # 使用 L 形走线连接两点
        _draw_wire(d, src_pos, dst_pos)


def _find_best_anchor(
    anchors: dict[str, tuple[float, float]],
    pin_name: str,
    fallback: str,
) -> tuple[float, float] | None:
    """从锚点字典中找到最佳匹配。"""
    # 精确匹配
    if pin_name in anchors:
        return anchors[pin_name]

    # 回退
    if fallback in anchors:
        return anchors[fallback]

    # 返回任意一个
    if anchors:
        return next(iter(anchors.values()))

    return None


def _draw_wire(
    d: schemdraw.Drawing,
    p1: tuple[float, float],
    p2: tuple[float, float],
) -> None:
    """在两个点之间绘制连线。

    如果 Y 坐标相同则直线连接，否则使用 L 形走线。
    """
    x1, y1 = p1
    x2, y2 = p2

    if abs(y1 - y2) < 0.1:
        # 水平直线
        elm.Line().at(p1).to(p2)
    else:
        # L 形走线：先水平到中点 x，再垂直
        mid_x = (x1 + x2) / 2
        elm.Line().at(p1).to((mid_x, y1))
        elm.Line().at((mid_x, y1)).to((mid_x, y2))
        elm.Line().at((mid_x, y2)).to(p2)


# ============================================================
# T087: Net labels
# ============================================================


def draw_net_labels(
    d: schemdraw.Drawing,
    nets: dict[str, SystemNet],
    all_anchors: dict[str, dict[str, tuple[float, float]]],
) -> None:
    """为非 GND 网络绘制网络标签。

    C65: net label 与系统 net 保持一致。
    """
    for net_id, net in nets.items():
        if net.net_type == NetType.GROUND:
            continue  # GND 由 GND 符号表示

        if not net.members:
            continue

        # 在第一个成员端口的位置放置标签
        first_member = net.members[0]
        mod_anchors = all_anchors.get(first_member.module_id, {})
        pos = _find_best_anchor(mod_anchors, first_member.pin_name, "VOUT")
        if pos is None:
            continue

        # 在连接点旁边标注网络名
        label_pos = (pos[0], pos[1] + 0.8)
        elm.Label().at(label_pos).label(net.net_name, "top")


# ============================================================
# T088: GND symbols
# ============================================================


def draw_gnd_symbols(
    d: schemdraw.Drawing,
    all_anchors: dict[str, dict[str, tuple[float, float]]],
) -> None:
    """在每个模块的 GND 锚点绘制 GND 符号。

    C66: GND 视觉表示统一。
    注：模块绘制函数已经在 IC 底部画了 GND 符号，
    此函数用于补充没有在 draw 函数中处理的情况。
    """
    # 目前各 draw 函数已内置 GND 符号，此函数做额外校验
    # 如果某个模块有 GND 锚点但没有对应的 Ground 元素，
    # 可以在这里补充。当前实现中每个 draw 函数都已处理。
    pass


# ============================================================
# T068: Unresolved connection visual hints
# ============================================================


def _draw_unresolved_hints(
    d: schemdraw.Drawing,
    ir: SystemDesignIR,
    all_anchors: dict[str, dict[str, tuple[float, float]]],
) -> None:
    """为未解析连接绘制可视提示 (C68)。"""
    for item in ir.unresolved_items:
        if item.get("type") != "unresolved_connection":
            continue
        module_id = item.get("src_module", "")
        mod_anchors = all_anchors.get(module_id, {})
        if mod_anchors:
            # 在模块旁边添加警告标签
            any_pos = next(iter(mod_anchors.values()))
            label_pos = (any_pos[0], any_pos[1] + 1.5)
            elm.Label().at(label_pos).label(
                f"[!] {item.get('reason', 'unresolved')[:30]}", "top",
            )


# ============================================================
# T088 主入口: render_system_svg
# ============================================================


def render_system_svg(
    ir: SystemDesignIR,
    instances: list[ModuleInstance] | None = None,
    filename: str | None = None,
) -> str:
    """Render the complete system into a single SVG file.

    C61: 基于系统 IR 渲染
    C62: 输出单一 SVG

    Args:
        ir: 系统设计中间表示
        instances: 可选的实例列表覆盖（若为 None，使用 ir.module_instances）
        filename: 输出文件名（若为 None，自动生成）

    Returns:
        SVG 文件路径
    """
    if filename is None:
        import time
        filename = f"system_design_{int(time.time() * 1000) % 100000}.svg"
    filepath = output_path(filename)

    # 如果提供了 instances 列表，同步到 IR
    if instances is not None:
        for inst in instances:
            if inst.module_id not in ir.module_instances:
                ir.module_instances[inst.module_id] = inst

    # 全局参考编号 (C64)
    ref_map = _build_global_ref_map(ir)

    with schemdraw.Drawing(file=filepath, show=False) as d:
        d.config(fontsize=10, unit=3)

        # T083: 电源链布局
        all_anchors = layout_power_chain(d, ir, ref_map)

        # 空系统时添加占位元素防止 schemdraw 因空白 bbox 崩溃
        if not ir.module_instances:
            elm.Label().at((0, 0)).label("(empty system)", "top")

        # T084: 控制支路布局
        layout_control_side(d, ir, all_anchors, ref_map)

        # T086: 模块间连线
        draw_intermodule_wires(d, ir.connections, all_anchors)

        # T087: 网络标签
        draw_net_labels(d, ir.nets, all_anchors)

        # T088: GND 符号（补充）
        draw_gnd_symbols(d, all_anchors)

        # C68: 未解析连接可视提示
        _draw_unresolved_hints(d, ir, all_anchors)

    logger.info("System SVG rendered: %s", filepath)
    return filepath
