"""SchemaForge LDO线性稳压电路渲染

固定布局：
     C_in          U1           C_out
VIN──┤├──┬──[AMS1117]──┬──┤├──VOUT
         │    GND       │
         └────┴─────────┘
             GND
"""

from __future__ import annotations

from typing import Any

import schemdraw
import schemdraw.elements as elm

from schemaforge.render.base import output_path


def render_ldo(
    v_in: float = 5.0,
    v_out: str = "3.3",
    ic_model: str = "AMS1117",
    c_in: str = "10μF",
    c_out: str = "22μF",
    filename: str | None = None,
) -> str:
    """渲染稳压器电路为SVG

    Args:
        v_in: 输入电压（V）
        v_out: 输出电压（V），字符串（如"3.3"）
        ic_model: IC型号（如AMS1117, TPS54202, LM7805等）
        c_in: 输入电容值
        c_out: 输出电容值
        filename: 输出文件名

    Returns:
        SVG文件路径
    """
    ic_label = f"{ic_model}-{v_out}" if v_out else ic_model

    if filename is None:
        filename = f"ldo_{v_in}V_to_{v_out}V.svg"
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
        elm.Capacitor(polar=True).down().label(f'C1\n{c_in}', 'right')
        elm.Ground()

        d.pop()

        # === LDO IC（用简化表示：方框） ===
        elm.Line().right(1)
        # 使用 Ic 元件画 AMS1117
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
        elm.Capacitor(polar=True).down().label(f'C2\n{c_out}', 'right')
        elm.Ground()

        d.pop()

        # 输出端标记
        elm.Line().right(1)
        elm.Dot(open=True).label(f'VOUT\n{v_out}V', 'right')

    return filepath


def render_ldo_from_params(params: dict[str, Any]) -> str:
    """从参数字典渲染稳压器"""
    return render_ldo(
        v_in=float(params.get("v_in", 5.0)),
        v_out=str(params.get("v_out", "3.3")),
        ic_model=str(params.get("ic_model", "AMS1117")),
        c_in=str(params.get("c_in", "10μF")),
        c_out=str(params.get("c_out", "22μF")),
    )


if __name__ == "__main__":
    path = render_ldo()
    print(f"LDO SVG已生成: {path}")
