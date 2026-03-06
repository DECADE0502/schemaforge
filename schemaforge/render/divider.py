"""SchemaForge 电压分压器渲染

固定布局：
  VIN ── [R1] ──┬── [R2] ── GND
                │
               VOUT（采样点）
"""

from __future__ import annotations

from typing import Any

import schemdraw
import schemdraw.elements as elm

from schemaforge.render.base import find_nearest_e24, format_value, output_path


def render_divider(
    v_in: float = 5.0,
    v_out: float = 2.5,
    r_total: float = 20.0,
    filename: str | None = None,
) -> str:
    """渲染电压分压器为SVG

    Args:
        v_in: 输入电压（V）
        v_out: 输出电压（V）
        r_total: 总阻值预算（kΩ）
        filename: 输出文件名，None则自动生成

    Returns:
        SVG文件路径
    """
    # 计算电阻值
    ratio = v_out / v_in
    r2_raw = r_total * 1000 * ratio       # Ω
    r1_raw = r_total * 1000 - r2_raw      # Ω

    # E24标准阻值
    r1_val = find_nearest_e24(r1_raw)
    r2_val = find_nearest_e24(r2_raw)

    # 实际输出电压
    v_out_actual = v_in * r2_val / (r1_val + r2_val)

    r1_str = format_value(r1_val, "Ω")
    r2_str = format_value(r2_val, "Ω")

    if filename is None:
        filename = f"divider_{v_in}V_to_{v_out}V.svg"
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
        elm.Dot(open=True).label(f'VOUT\n≈{v_out_actual:.2f}V', 'right')

    return filepath


def render_divider_from_params(params: dict[str, Any]) -> str:
    """从参数字典渲染分压器"""
    return render_divider(
        v_in=float(params.get("v_in", 5.0)),
        v_out=float(params.get("v_out", 2.5)),
        r_total=float(params.get("r_total", 20.0)),
    )


if __name__ == "__main__":
    path = render_divider()
    print(f"分压器SVG已生成: {path}")
