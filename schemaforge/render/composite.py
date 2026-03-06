"""SchemaForge 组合电路渲染

LDO + LED指示灯的组合电路渲染。

固定布局：
     C_in          U1           C_out
VIN──┤├──┬──[AMS1117]──┬──┤├──┬──VOUT
         │    GND       │      │
         └────┴─────────┘      ├── [R_led] ── [LED] ── GND
                               │
                              GND
"""

from __future__ import annotations

from typing import Any

import schemdraw
import schemdraw.elements as elm

from schemaforge.core.calculator import calculate_led_resistor
from schemaforge.render.base import output_path


def render_composite(
    v_in: float = 5.0,
    v_out: str = "3.3",
    c_in: str = "10μF",
    c_out: str = "22μF",
    led_color: str = "green",
    led_current: float = 10.0,
    filename: str | None = None,
) -> str:
    """渲染LDO+LED组合电路为SVG

    Args:
        v_in: 输入电压（V）
        v_out: 输出电压（V）
        c_in: 输入电容值
        c_out: 输出电容值
        led_color: LED颜色
        led_current: LED电流（mA）
        filename: 输出文件名

    Returns:
        SVG文件路径
    """
    ic_model = f"AMS1117-{v_out}"
    calc = calculate_led_resistor(float(v_out), led_color, led_current)
    r_str = calc.get("r_str", "120Ω")
    color_cn = {"red": "红", "green": "绿", "blue": "蓝", "white": "白"}.get(led_color, led_color)

    if filename is None:
        filename = f"composite_ldo_{v_out}V_led_{led_color}.svg"
    filepath = output_path(filename)

    with schemdraw.Drawing(file=filepath, show=False) as d:
        d.config(fontsize=10, unit=3)

        # === 输入端 ===
        elm.Dot(open=True).label(f'VIN\n{v_in}V', 'left')
        elm.Line().right(1)

        # 输入电容分支点
        elm.Dot()
        d.push()

        # 输入电容向下到地
        elm.Capacitor(polar=True).down().label(f'C1\n{c_in}', 'right')
        elm.Ground()

        d.pop()

        # === LDO IC ===
        elm.Line().right(1)
        u1 = elm.Ic(
            pins=[
                elm.IcPin(name='VIN', side='left', pin='1'),
                elm.IcPin(name='GND', side='bottom', pin='3'),
                elm.IcPin(name='VOUT', side='right', pin='2'),
            ],
            size=(2, 1.5),
        ).label(ic_model, 'top').anchor('inL1')

        # GND连接
        d.push()
        elm.Line().at(u1.inB1).down(1)  # type: ignore[call-arg]
        elm.Ground()
        d.pop()

        # === 输出段 ===
        elm.Line().at(u1.inR1).right(1)  # type: ignore[call-arg]

        # 输出电容分支点
        elm.Dot()
        d.push()

        # 输出电容向下到地
        elm.Capacitor(polar=True).down().label(f'C2\n{c_out}', 'right')
        elm.Ground()

        d.pop()

        # VOUT主线继续
        elm.Line().right(1)
        elm.Dot()
        d.push()

        # === LED支路（向下） ===
        elm.Resistor().down().label(f'R1\n{r_str}', 'right')
        elm.LED().down().label(f'D1\nLED({color_cn})', 'right')
        elm.Ground()

        d.pop()

        # VOUT输出端
        elm.Line().right(1.5)
        elm.Dot(open=True).label(f'VOUT\n{v_out}V', 'right')

    return filepath


def render_composite_from_params(params: dict[str, Any]) -> str:
    """从参数字典渲染组合电路"""
    return render_composite(
        v_in=float(params.get("v_in", 5.0)),
        v_out=str(params.get("v_out", "3.3")),
        c_in=str(params.get("c_in", "10μF")),
        c_out=str(params.get("c_out", "22μF")),
        led_color=str(params.get("led_color", "green")),
        led_current=float(params.get("led_current", 10.0)),
    )


if __name__ == "__main__":
    path = render_composite()
    print(f"组合电路SVG已生成: {path}")
