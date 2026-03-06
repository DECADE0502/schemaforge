"""SchemaForge 拓扑布局策略

5种电路布局策略函数，通过装饰器注册到 TopologyRenderer。
每个函数从 DeviceModel 数据动态生成原理图SVG。
"""

from __future__ import annotations

from typing import Any

import schemdraw
import schemdraw.elements as elm

from schemaforge.core.calculator import (
    calculate_divider,
    calculate_led_resistor,
    calculate_rc_filter,
)
from schemaforge.library.models import DeviceModel
from schemaforge.render.base import find_nearest_e24, format_value, output_path
from schemaforge.schematic.renderer import TopologyRenderer


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
    v_in = float(params.get("v_in", 5.0))
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
        d.push()

        # 输出电容向下到地
        elm.Capacitor(polar=True).down().label(f'C2\n{c_out_display}', 'right')
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
    v_in = float(params.get("v_in", 12.0))
    v_out = float(params.get("v_out", 3.3))
    ic_model = str(params.get("ic_model", device.part_number))
    c_in = str(params.get("c_in", "10uF")).replace("u", "\u03bc")
    c_out = str(params.get("c_out", "22uF")).replace("u", "\u03bc")
    l_value = str(params.get("l_value", "4.7uH")).replace("u", "\u03bc")
    c_boot = str(params.get("c_boot", "100nF"))

    # FB分压电阻（简化计算：假定Vref=0.8V）
    v_ref = float(params.get("v_ref", 0.8))
    r_fb_total = float(params.get("r_fb_total", 100.0))  # kOhm
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
        d.push()
        elm.Capacitor(polar=True).down().label(f'C2\n{c_out}', 'right')
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
    v_in = float(params.get("v_in", 5.0))
    v_out = float(params.get("v_out", 2.5))
    r_total = float(params.get("r_total", 20.0))

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
    v_supply = float(params.get("v_supply", 3.3))
    led_color = str(params.get("led_color", "green"))
    led_current = float(params.get("led_current", 10.0))

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
    f_cutoff = float(params.get("f_cutoff", 1000.0))
    r_value = float(params.get("r_value", 10.0))

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
