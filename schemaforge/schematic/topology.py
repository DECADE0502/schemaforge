"""SchemaForge 拓扑布局策略

5种电路布局策略函数 + 1个通用泛型布局，通过装饰器注册到 TopologyRenderer。
每个函数从 DeviceModel 数据动态生成原理图SVG。
"""

from __future__ import annotations

import re
from typing import Any

import schemdraw
import schemdraw.elements as elm

from schemaforge.core.calculator import (
    calculate_divider,
    calculate_led_resistor,
    calculate_rc_filter,
)
from schemaforge.library.models import DeviceModel, ExternalComponent, TopologyConnection
from schemaforge.render.base import find_nearest_e24, format_value, output_path
from schemaforge.schematic.renderer import TopologyRenderer


def _safe_float(value: Any, default: float = 0.0) -> float:
    """安全解析数值，自动去除单位后缀。

    "20V" → 20.0, "3.3" → 3.3, 12.0 → 12.0, "" → default
    """
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return default
    match = re.search(r"[-+]?\d*\.?\d+", text)
    if match:
        try:
            return float(match.group())
        except ValueError:
            return default
    return default


# ============================================================
# LDO 线性稳压器布局
# ============================================================

@TopologyRenderer.register_layout("ldo")
def layout_ldo(
    device: DeviceModel,
    params: dict[str, Any],
    filename: str | None = None,
) -> str:
    """LDO线性稳压器布局

    布局：VIN -> [C_in] -> [IC] -> [C_out] -> VOUT
    从 DeviceModel 的 symbol 和 topology 动态读取引脚/外部元件。
    """
    v_in = _safe_float(params.get("v_in", 5.0), 5.0)
    v_out = str(params.get("v_out", "3.3"))
    ic_model = str(params.get("ic_model", device.part_number))

    # 从拓扑定义读取外部元件默认值
    topology = device.topology
    c_in = str(params.get("c_in", "10uF"))
    c_out = str(params.get("c_out", "22uF"))
    if topology:
        for comp in topology.external_components:
            if comp.role == "input_cap" and "c_in" not in params:
                c_in = comp.default_value
            elif comp.role == "output_cap" and "c_out" not in params:
                c_out = comp.default_value

    # 格式化电容值显示
    c_in_display = c_in.replace("u", "\u03bc")
    c_out_display = c_out.replace("u", "\u03bc")
    power_led = str(params.get("power_led", "")).lower() == "true"
    led_color = str(params.get("led_color", "green"))
    led_resistor = str(params.get("led_resistor", "1kΩ"))

    ic_label = f"{ic_model}-{v_out}" if v_out else ic_model

    if filename is None:
        filename = f"topo_ldo_{v_in}V_to_{v_out}V.svg"
    filepath = output_path(filename)

    with schemdraw.Drawing(file=filepath, show=False) as d:
        d.config(fontsize=11, unit=3)

        # === 输入端 ===
        elm.Dot(open=True).label(f'VIN\n{v_in}V', 'left')
        elm.Line().right(1)

        # 输入电容分支点
        elm.Dot()
        d.push()

        # 输入电容向下到地
        elm.Capacitor(polar=True).down().label(f'C1\n{c_in_display}', 'right')
        elm.Ground()

        d.pop()

        # === LDO IC ===
        elm.Line().right(1)

        # 用 DeviceModel 的 symbol 构建IC，若无则用默认3引脚
        if device.symbol:
            u1 = TopologyRenderer.build_ic_element(device.symbol, ic_label)
            u1.anchor('inL1')
        else:
            u1 = elm.Ic(
                pins=[
                    elm.IcPin(name='VIN', side='left', pin='1'),
                    elm.IcPin(name='GND', side='bottom', pin='3'),
                    elm.IcPin(name='VOUT', side='right', pin='2'),
                ],
                size=(2, 1.5),
            ).label(ic_label, 'top').anchor('inL1')

        # === GND连接 ===
        d.push()
        elm.Line().at(u1.inB1).down(1)  # type: ignore[call-arg]
        elm.Ground()
        d.pop()

        # === 输出端 ===
        elm.Line().at(u1.inR1).right(1)  # type: ignore[call-arg]

        # 输出电容分支点
        elm.Dot()
        output_tap = d.here
        d.push()

        # 输出电容向下到地
        elm.Capacitor(polar=True).down().label(f'C2\n{c_out_display}', 'right')
        elm.Ground()

        d.pop()

        if power_led:
            d.push()
            elm.Resistor().down().at(output_tap).label(
                f'RLED\n{led_resistor}', 'right'
            )
            elm.LED().down().label(f'DLED\n{led_color}', 'right')
            elm.Ground()
            d.pop()

        # 输出端标记
        elm.Line().right(1)
        elm.Dot(open=True).label(f'VOUT\n{v_out}V', 'right')

    return filepath


# ============================================================
# Buck 降压转换器布局
# ============================================================

@TopologyRenderer.register_layout("buck")
def layout_buck(
    device: DeviceModel,
    params: dict[str, Any],
    filename: str | None = None,
) -> str:
    """Buck降压转换器布局

    布局：VIN -> [C_in] -> [IC] -> L1 -> [C_out] -> VOUT
    附加：BOOT电容、FB分压电阻网络
    """
    v_in = _safe_float(params.get("v_in", 12.0), 12.0)
    v_out = _safe_float(params.get("v_out", 3.3), 3.3)
    ic_model = str(params.get("ic_model", device.part_number))
    c_in = str(params.get("c_in", "10uF")).replace("u", "\u03bc")
    c_out = str(params.get("c_out", "22uF")).replace("u", "\u03bc")
    l_value = str(params.get("l_value", "4.7uH")).replace("u", "\u03bc")
    c_boot = str(params.get("c_boot", "100nF"))
    power_led = str(params.get("power_led", "")).lower() == "true"
    led_color = str(params.get("led_color", "green"))
    led_resistor = str(params.get("led_resistor", "1kΩ"))

    # FB分压电阻（简化计算：假定Vref=0.8V）
    r1_given = params.get("r_fb_upper")
    r2_given = params.get("r_fb_lower")
    if r1_given and r2_given:
        r1_str = str(r1_given)
        r2_str = str(r2_given)
    else:
        v_ref = _safe_float(params.get("v_ref", 0.8), 0.8)
        r_fb_total = _safe_float(params.get("r_fb_total", 100.0), 100.0)  # kOhm
        ratio = v_ref / v_out
        r2_raw = r_fb_total * 1000 * ratio
        r1_raw = r_fb_total * 1000 - r2_raw
        r1_val = find_nearest_e24(r1_raw)
        r2_val = find_nearest_e24(r2_raw)
        r1_str = format_value(r1_val, "\u03a9")
        r2_str = format_value(r2_val, "\u03a9")

    if filename is None:
        filename = f"topo_buck_{v_in}V_to_{v_out}V.svg"
    filepath = output_path(filename)

    with schemdraw.Drawing(file=filepath, show=False) as d:
        d.config(fontsize=10, unit=3)

        # === 输入端 ===
        elm.Dot(open=True).label(f'VIN\n{v_in}V', 'left')
        elm.Line().right(1)

        # 输入电容分支
        elm.Dot()
        d.push()
        elm.Capacitor(polar=True).down().label(f'C1\n{c_in}', 'right')
        elm.Ground()
        d.pop()

        # === Buck IC（6引脚） ===
        elm.Line().right(1)

        if device.symbol:
            u1 = TopologyRenderer.build_ic_element(device.symbol, ic_model)
            u1.anchor('inL1')
        else:
            u1 = elm.Ic(
                pins=[
                    elm.IcPin(name='VIN', side='left', pin='1', slot='1/3'),
                    elm.IcPin(name='EN', side='left', pin='2', slot='2/3'),
                    elm.IcPin(name='GND', side='bottom', pin='3', slot='1/1'),
                    elm.IcPin(name='SW', side='right', pin='4', slot='1/3'),
                    elm.IcPin(name='FB', side='right', pin='5', slot='2/3'),
                    elm.IcPin(name='BOOT', side='top', pin='6', slot='1/1'),
                ],
                size=(4, 3),
            ).label(ic_model, 'top').anchor('inL1')

        # GND连接
        d.push()
        elm.Line().at(u1.inB1).down(1)  # type: ignore[call-arg]
        elm.Ground()
        d.pop()

        # EN连接到VIN（使能）
        d.push()
        elm.Line().at(u1.inL2).left(0.5)  # type: ignore[call-arg]
        elm.Dot()
        d.pop()

        # === SW -> 电感 -> VOUT ===
        elm.Line().at(u1.inR1).right(0.5)  # type: ignore[call-arg]

        # BOOT电容（SW到BOOT）
        sw_point = d.here
        d.push()
        elm.Capacitor().up().at(sw_point).label(f'C3\n{c_boot}', 'right')
        elm.Line().left().to(u1.inT1)  # type: ignore[call-arg]
        d.pop()

        # 电感
        elm.Inductor2().right().at(sw_point).label(f'L1\n{l_value}', 'top')

        # 输出电容分支
        elm.Dot()
        output_tap = d.here
        d.push()
        elm.Capacitor(polar=True).down().label(f'C2\n{c_out}', 'right')
        elm.Ground()
        d.pop()

        if power_led:
            d.push()
            elm.Resistor().down().at(output_tap).label(
                f'RLED\n{led_resistor}', 'right'
            )
            elm.LED().down().label(f'DLED\n{led_color}', 'right')
            elm.Ground()
            d.pop()

        # VOUT输出
        vout_point = d.here
        elm.Line().right(1)
        elm.Dot(open=True).label(f'VOUT\n{v_out}V', 'right')

        # === FB分压网络 ===
        d.push()
        elm.Line().at(u1.inR2).right(0.5)  # type: ignore[call-arg]
        fb_point = d.here
        elm.Dot()

        # R1: VOUT -> FB
        elm.Resistor().up().at(fb_point).label(f'R1\n{r1_str}', 'right')
        elm.Line().right().to(vout_point)

        # R2: FB -> GND
        elm.Resistor().down().at(fb_point).label(f'R2\n{r2_str}', 'right')
        elm.Ground()
        d.pop()

    return filepath


# ============================================================
# 电压分压器布局
# ============================================================

@TopologyRenderer.register_layout("voltage_divider")
def layout_voltage_divider(
    device: DeviceModel,
    params: dict[str, Any],
    filename: str | None = None,
) -> str:
    """电压分压器布局

    布局：VIN -> [R1] -> tap -> [R2] -> GND, VOUT在tap处
    纯无源电路，无IC。
    """
    v_in = _safe_float(params.get("v_in", 5.0), 5.0)
    v_out = _safe_float(params.get("v_out", 2.5), 2.5)
    r_total = _safe_float(params.get("r_total", 20.0), 20.0)

    # 使用已有计算器
    calc = calculate_divider(v_in, v_out, r_total)
    r1_str = calc["r1_str"]
    r2_str = calc["r2_str"]
    v_out_actual = calc["v_out_actual"]

    if filename is None:
        filename = f"topo_divider_{v_in}V_to_{v_out}V.svg"
    filepath = output_path(filename)

    with schemdraw.Drawing(file=filepath, show=False) as d:
        d.config(fontsize=11, unit=3)

        # 输入端标记
        elm.Dot(open=True).label(f'VIN\n{v_in}V', 'left')

        # R1（水平向右）
        elm.Resistor().right().label(f'R1\n{r1_str}', 'top')

        # 分压点
        elm.Dot()
        d.push()

        # R2（向下到地）
        elm.Resistor().down().label(f'R2\n{r2_str}', 'right')
        elm.Ground()

        d.pop()

        # 输出端标记
        elm.Line().right(1.5)
        elm.Dot(open=True).label(f'VOUT\n\u2248{v_out_actual:.2f}V', 'right')

    return filepath


# ============================================================
# LED驱动器布局
# ============================================================

@TopologyRenderer.register_layout("led_driver")
def layout_led_driver(
    device: DeviceModel,
    params: dict[str, Any],
    filename: str | None = None,
) -> str:
    """LED驱动器布局

    布局：VCC -> [R] -> [LED] -> GND
    纯无源+LED电路，无IC。
    """
    v_supply = _safe_float(params.get("v_supply", 3.3), 3.3)
    led_color = str(params.get("led_color", "green"))
    led_current = _safe_float(params.get("led_current", 10.0), 10.0)

    # 使用已有计算器
    calc = calculate_led_resistor(v_supply, led_color, led_current)
    r_str = calc["r_str"]

    # 颜色中文映射
    color_cn = {
        "red": "\u7ea2",
        "green": "\u7eff",
        "blue": "\u84dd",
        "white": "\u767d",
    }.get(led_color, led_color)

    if filename is None:
        filename = f"topo_led_{led_color}_{v_supply}V.svg"
    filepath = output_path(filename)

    with schemdraw.Drawing(file=filepath, show=False) as d:
        d.config(fontsize=11, unit=3)

        # 电源端
        elm.Dot(open=True).label(f'VCC\n{v_supply}V', 'left')

        # 限流电阻
        elm.Resistor().right().label(f'R1\n{r_str}', 'top')

        # LED
        elm.LED().right().label(f'D1\nLED({color_cn})', 'bottom')

        # 接地
        elm.Ground()

    return filepath


# ============================================================
# RC低通滤波器布局
# ============================================================

@TopologyRenderer.register_layout("rc_filter")
def layout_rc_filter(
    device: DeviceModel,
    params: dict[str, Any],
    filename: str | None = None,
) -> str:
    """RC低通滤波器布局

    布局：IN -> [R] -> tap -> [C] -> GND, OUT在tap处
    纯无源电路，无IC。
    """
    f_cutoff = _safe_float(params.get("f_cutoff", 1000.0), 1000.0)
    r_value = _safe_float(params.get("r_value", 10.0), 10.0)

    # 使用已有计算器
    calc = calculate_rc_filter(f_cutoff, r_value)
    r_str = calc["r_str"]
    c_str = calc["c_str"]

    if filename is None:
        filename = f"topo_rc_lowpass_{f_cutoff}Hz.svg"
    filepath = output_path(filename)

    with schemdraw.Drawing(file=filepath, show=False) as d:
        d.config(fontsize=11, unit=3)

        # 输入端
        elm.Dot(open=True).label('IN', 'left')

        # 电阻
        elm.Resistor().right().label(f'R1\n{r_str}', 'top')

        # 节点
        elm.Dot()
        d.push()

        # 电容向下到地
        elm.Capacitor().down().label(f'C1\n{c_str}', 'right')
        elm.Ground()

        d.pop()

        # 输出端
        elm.Line().right(1.5)
        elm.Dot(open=True).label(f'OUT\nfc={f_cutoff}Hz', 'right')

    return filepath


# ============================================================
# schemdraw 元件名 → 类的映射
# ============================================================

_ELEMENT_MAP: dict[str, type] = {
    "Capacitor": elm.Capacitor,
    "Resistor": elm.Resistor,
    "Inductor": elm.Inductor,
    "Inductor2": elm.Inductor2,
    "LED": elm.LED,
    "Diode": elm.Diode,
}


def _resolve_element_class(name: str) -> type:
    """将 schemdraw_element 字符串映射为 schemdraw 元件类。"""
    cls = _ELEMENT_MAP.get(name)
    if cls is None:
        # 尝试从 elm 模块动态查找
        cls = getattr(elm, name, None)
    return cls or elm.Resistor  # 兜底用电阻符号


def _resolve_value(comp: ExternalComponent, params: dict[str, Any]) -> str:
    """从 params 解析 value_expression，回退到 default_value。

    value_expression 格式: "{c_in}" -> 查找 params["c_in"]
    """
    expr = comp.value_expression.strip()
    if expr.startswith("{") and expr.endswith("}"):
        key = expr[1:-1]
        if key in params:
            return str(params[key])
    # 回退到默认值
    return comp.default_value


def _classify_connections(
    connections: list[TopologyConnection],
) -> tuple[
    TopologyConnection | None,
    TopologyConnection | None,
    TopologyConnection | None,
    list[TopologyConnection],
]:
    """将 connections 分类为 VIN、VOUT、GND 和其他。

    Returns:
        (vin_conn, vout_conn, gnd_conn, others)
    """
    vin: TopologyConnection | None = None
    vout: TopologyConnection | None = None
    gnd: TopologyConnection | None = None
    others: list[TopologyConnection] = []

    for conn in connections:
        if conn.is_ground and gnd is None:
            gnd = conn
        elif conn.is_power and conn.device_pin and vin is None:
            # 有 device_pin 的 power net 优先视为 VIN
            vin = conn
        elif conn.is_power and vout is None:
            vout = conn
        else:
            others.append(conn)

    # 如果没找到带 device_pin 的 VIN，但有两个 power net，
    # 按出现顺序把第一个当 VIN
    if vin is None and vout is not None:
        # 扫描 others 看有没有 power net 被分到 others
        pass  # vout 已是唯一 power net，保持原样

    return vin, vout, gnd, others


def _build_component_map(
    components: list[ExternalComponent],
) -> dict[str, ExternalComponent]:
    """按 role 建立外部元件索引。"""
    return {comp.role: comp for comp in components}


# ============================================================
# 通用泛型布局（fallback）
# ============================================================


def layout_generic(
    device: DeviceModel,
    params: dict[str, Any],
    filename: str | None = None,
) -> str:
    """通用泛型布局 — 从 TopologyDef 数据驱动渲染任意电路。

    布局策略：
    1. 左侧输入 (VIN/IN/VCC)
    2. 输入侧被动元件（电容等，向下接地）
    3. 中央 IC（若有 device.symbol 或拓扑有 device_pin）
    4. 输出侧被动元件
    5. 右侧输出 (VOUT/OUT)
    6. 底部接地
    """
    topology = device.topology
    assert topology is not None  # 调用方已保证

    comp_map = _build_component_map(topology.external_components)
    vin_conn, vout_conn, gnd_conn, other_conns = _classify_connections(
        topology.connections,
    )

    # 判断是否需要画 IC（有任何 connection 引用了 device_pin 说明有主IC）
    has_ic = any(c.device_pin for c in topology.connections)

    # 收集引用到各 net 的外部元件 role（从 external_refs 解析 "role.pin"）
    def _roles_on_net(conn: TopologyConnection | None) -> list[str]:
        if conn is None:
            return []
        roles: list[str] = []
        for ref in conn.external_refs:
            role = ref.split(".")[0] if "." in ref else ref
            if role not in roles:
                roles.append(role)
        return roles

    input_roles = _roles_on_net(vin_conn)
    output_roles = _roles_on_net(vout_conn)
    gnd_roles = _roles_on_net(gnd_conn)

    # 确定输入/输出标签
    vin_label = (vin_conn.net_name if vin_conn else "IN")
    vout_label = (vout_conn.net_name if vout_conn else "OUT")
    v_in_val = _safe_float(params.get("v_in", params.get("v_supply", "")), 0)
    v_out_val = _safe_float(params.get("v_out", ""), 0)
    vin_text = f"{vin_label}\n{v_in_val}V" if v_in_val else vin_label
    vout_text = f"{vout_label}\n{v_out_val}V" if v_out_val else vout_label

    ic_model = str(params.get("ic_model", device.part_number))

    # 文件名
    if filename is None:
        filename = f"topo_generic_{topology.circuit_type}.svg"
    filepath = output_path(filename)

    # power_led 可选段（与现有布局行为一致）
    power_led = str(params.get("power_led", "")).lower() == "true"
    led_color = str(params.get("led_color", "green"))
    led_resistor = str(params.get("led_resistor", "1k\u03a9"))

    # --- 解析各元件的显示值 ---
    def _display_value(comp: ExternalComponent) -> str:
        raw = _resolve_value(comp, params)
        return raw.replace("u", "\u03bc") if raw else ""

    # 收集「输入侧向下接地」的元件 = 同时出现在 input net 和 gnd net 的 role
    input_to_gnd = [r for r in input_roles if r in gnd_roles]
    # 收集「输出侧向下接地」的元件
    output_to_gnd = [r for r in output_roles if r in gnd_roles]

    with schemdraw.Drawing(file=filepath, show=False) as d:
        d.config(fontsize=11, unit=3)

        # ── 输入端标记 ──
        elm.Dot(open=True).label(vin_text, "left")
        elm.Line().right(1)

        # ── 输入侧无源器件（向下到地） ──
        ref_counter = 1
        for role in input_to_gnd:
            comp = comp_map.get(role)
            if comp is None:
                continue
            elm.Dot()
            d.push()
            elem_cls = _resolve_element_class(comp.schemdraw_element)
            val = _display_value(comp)
            label_text = f"{comp.ref_prefix}{ref_counter}\n{val}" if val else f"{comp.ref_prefix}{ref_counter}"
            elem_cls().down().label(label_text, "right")
            elm.Ground()
            d.pop()
            ref_counter += 1

        # ── IC 中心 ──
        if has_ic:
            elm.Line().right(1)

            if device.symbol:
                u1 = TopologyRenderer.build_ic_element(device.symbol, ic_model)
                u1.anchor("inL1")
            else:
                # 从拓扑连接推断引脚：先收集 (name, side)，再统一计算 slot
                pin_specs: list[tuple[str, str]] = []  # (name, side)
                for conn in topology.connections:
                    if not conn.device_pin:
                        continue
                    if conn.is_ground:
                        pin_specs.append((conn.device_pin, "bottom"))
                    elif conn.is_power and conn == vin_conn:
                        pin_specs.append((conn.device_pin, "left"))
                    elif conn.is_power:
                        pin_specs.append((conn.device_pin, "right"))
                    elif conn.device_pin.upper() in ("SW", "OUT", "VOUT"):
                        pin_specs.append((conn.device_pin, "right"))
                    elif conn.device_pin.upper() in ("BOOT", "BST"):
                        pin_specs.append((conn.device_pin, "top"))
                    else:
                        pin_specs.append((conn.device_pin, "left"))

                if not pin_specs:
                    pin_specs = [("IN", "left"), ("OUT", "right")]

                # 保证右侧至少有一个引脚（用于输出连线）
                if not any(s == "right" for _, s in pin_specs):
                    pin_specs.append(("OUT", "right"))

                # 统计每侧的引脚数
                side_counts: dict[str, int] = {}
                for _, side in pin_specs:
                    side_counts[side] = side_counts.get(side, 0) + 1

                # 构建 IcPin（slot = idx/total）
                side_idx: dict[str, int] = {}
                ic_pins: list[elm.IcPin] = []
                for name, side in pin_specs:
                    side_idx[side] = side_idx.get(side, 0) + 1
                    ic_pins.append(
                        elm.IcPin(
                            name=name,
                            side=side,
                            slot=f"{side_idx[side]}/{side_counts[side]}",
                        )
                    )

                u1 = elm.Ic(
                    pins=ic_pins,
                    size=(4, 3),
                ).label(ic_model, "top").anchor("inL1")

            # IC GND 连接
            has_bottom = any(
                c.device_pin and c.is_ground for c in topology.connections
            )
            if has_bottom:
                d.push()
                elm.Line().at(u1.inB1).down(1)  # type: ignore[call-arg]
                elm.Ground()
                d.pop()

            # IC 输出引脚
            elm.Line().at(u1.inR1).right(1)  # type: ignore[call-arg]
        else:
            # 无IC：直接在输入侧无源器件之后继续水平连线
            # 对于水平串联的元件（出现在 input net 但不接地的 role）
            for role in input_roles:
                if role in input_to_gnd:
                    continue
                comp = comp_map.get(role)
                if comp is None:
                    continue
                elem_cls = _resolve_element_class(comp.schemdraw_element)
                val = _display_value(comp)
                label_text = f"{comp.ref_prefix}{ref_counter}\n{val}" if val else f"{comp.ref_prefix}{ref_counter}"
                elem_cls().right().label(label_text, "top")
                ref_counter += 1

            # 处理 "other" nets 中的串联元件
            for conn in other_conns:
                for ref in conn.external_refs:
                    role = ref.split(".")[0] if "." in ref else ref
                    comp = comp_map.get(role)
                    if comp is None:
                        continue
                    # 避免重复绘制已在 input/output 侧画过的
                    if role in input_to_gnd or role in output_to_gnd:
                        continue
                    if role in input_roles or role in output_roles:
                        continue
                    elem_cls = _resolve_element_class(comp.schemdraw_element)
                    val = _display_value(comp)
                    label_text = f"{comp.ref_prefix}{ref_counter}\n{val}" if val else f"{comp.ref_prefix}{ref_counter}"
                    elem_cls().right().label(label_text, "top")
                    ref_counter += 1

        # ── 输出侧无源器件（向下到地） ──
        for role in output_to_gnd:
            comp = comp_map.get(role)
            if comp is None:
                continue
            elm.Dot()
            d.push()
            elem_cls = _resolve_element_class(comp.schemdraw_element)
            val = _display_value(comp)
            label_text = f"{comp.ref_prefix}{ref_counter}\n{val}" if val else f"{comp.ref_prefix}{ref_counter}"
            elem_cls().down().label(label_text, "right")
            elm.Ground()
            d.pop()
            ref_counter += 1

        # ── Power LED（可选） ──
        if power_led:
            d.push()
            elm.Resistor().down().label(f"RLED\n{led_resistor}", "right")
            elm.LED().down().label(f"DLED\n{led_color}", "right")
            elm.Ground()
            d.pop()

        # ── 输出端标记 ──
        elm.Line().right(1)
        elm.Dot(open=True).label(vout_text, "right")

    return filepath
