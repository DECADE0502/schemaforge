"""SchemaForge RC低通滤波器渲染

固定布局：
  IN ── [R1] ──┬── OUT
               │
              [C1]
               │
              GND
"""

from __future__ import annotations

from typing import Any

import schemdraw
import schemdraw.elements as elm

from schemaforge.core.calculator import calculate_rc_filter
from schemaforge.render.base import output_path


def render_rc_filter(
    f_cutoff: float = 1000.0,
    r_value: float = 10.0,
    filename: str | None = None,
) -> str:
    """渲染RC低通滤波器为SVG

    Args:
        f_cutoff: 截止频率（Hz）
        r_value: 电阻值（kΩ）
        filename: 输出文件名

    Returns:
        SVG文件路径
    """
    calc = calculate_rc_filter(f_cutoff, r_value)
    r_str = calc["r_str"]
    c_str = calc["c_str"]

    if filename is None:
        filename = f"rc_lowpass_{f_cutoff}Hz.svg"
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


def render_rc_filter_from_params(params: dict[str, Any]) -> str:
    """从参数字典渲染RC滤波器"""
    return render_rc_filter(
        f_cutoff=float(params.get("f_cutoff", 1000.0)),
        r_value=float(params.get("r_value", 10.0)),
    )


if __name__ == "__main__":
    path = render_rc_filter()
    print(f"RC滤波器SVG已生成: {path}")
