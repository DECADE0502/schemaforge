"""SchemaForge LED指示灯电路渲染

固定布局：
  VCC ── [R_limit] ── [LED] ── GND
"""

from __future__ import annotations

from typing import Any

import schemdraw
import schemdraw.elements as elm

from schemaforge.core.calculator import calculate_led_resistor
from schemaforge.render.base import output_path


def render_led(
    v_supply: float = 3.3,
    led_color: str = "green",
    led_current: float = 10.0,
    filename: str | None = None,
) -> str:
    """渲染LED指示灯电路为SVG

    Args:
        v_supply: 电源电压（V）
        led_color: LED颜色
        led_current: LED电流（mA）
        filename: 输出文件名

    Returns:
        SVG文件路径
    """
    calc = calculate_led_resistor(v_supply, led_color, led_current)
    r_str = calc["r_str"]

    # 颜色中文映射
    color_cn = {"red": "红", "green": "绿", "blue": "蓝", "white": "白"}.get(led_color, led_color)

    if filename is None:
        filename = f"led_{led_color}_{v_supply}V.svg"
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


def render_led_from_params(params: dict[str, Any]) -> str:
    """从参数字典渲染LED"""
    return render_led(
        v_supply=float(params.get("v_supply", 3.3)),
        led_color=str(params.get("led_color", "green")),
        led_current=float(params.get("led_current", 10.0)),
    )


if __name__ == "__main__":
    path = render_led()
    print(f"LED SVG已生成: {path}")
