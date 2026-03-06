"""SchemaForge 导出器

生成BOM清单和SPICE网表。
"""

from __future__ import annotations


from schemaforge.core.models import CircuitInstance
from schemaforge.render.base import output_path


# LCSC器件编号映射
LCSC_MAP: dict[str, dict[str, str]] = {
    "AMS1117": {"part": "C347222", "package": "SOT-223", "desc": "LDO线性稳压器"},
    "电容_10μF": {"part": "C15849", "package": "0805", "desc": "电解电容"},
    "电容_22μF": {"part": "C159801", "package": "0805", "desc": "电解电容"},
    "电阻": {"part": "C25879", "package": "0402", "desc": "贴片电阻"},
    "LED_red": {"part": "C84256", "package": "0805", "desc": "LED(红)"},
    "LED_green": {"part": "C2297", "package": "0805", "desc": "LED(绿)"},
    "LED_blue": {"part": "C72041", "package": "0805", "desc": "LED(蓝)"},
    "LED_white": {"part": "C2290", "package": "0805", "desc": "LED(白)"},
    "电容": {"part": "C14663", "package": "0805", "desc": "贴片电容"},
}


def generate_bom(
    circuit: CircuitInstance,
    filename: str | None = None,
) -> str:
    """生成BOM清单（Markdown表格）

    Args:
        circuit: 电路实例
        filename: 输出文件名

    Returns:
        BOM Markdown文本
    """
    params = circuit.input_parameters

    lines: list[str] = []
    lines.append(f"# BOM清单 — {circuit.name}")
    lines.append("")
    lines.append("| # | 参考标号 | 器件名称 | 值 | 封装 | LCSC编号 | 数量 |")
    lines.append("|---|----------|----------|----|------|----------|------|")

    idx = 1
    for comp in circuit.components:
        # 查找器件信息
        name = comp.component_type
        value = comp.parameters.get("value", "-")
        package = "0805"
        lcsc = ""
        desc = name

        # 尝试匹配LCSC
        model_param = comp.parameters.get("model", "")
        if comp.ref.startswith("U") and model_param:
            # IC 器件：用 component_type 查 LCSC（基型匹配）
            base_type = comp.component_type.split("-")[0]  # AMS1117-3.3 → AMS1117
            info = LCSC_MAP.get(base_type, {})
            package = info.get("package", "SOT-223")
            lcsc = info.get("part", "")
            desc = model_param  # 如 "AMS1117-3.3"
        elif "LED" in name or "LED" in comp.ref:
            color = params.get("led_color", "green")
            info = LCSC_MAP.get(f"LED_{color}", {})
            package = info.get("package", "0805")
            lcsc = info.get("part", "")
            color_cn = {"red": "红", "green": "绿", "blue": "蓝", "white": "白"}.get(color, color)
            desc = f"LED({color_cn})"
        elif "电阻" in name or comp.ref.startswith("R"):
            info = LCSC_MAP.get("电阻", {})
            package = info.get("package", "0402")
            lcsc = info.get("part", "")
            desc = "贴片电阻"
        elif "电容" in name or comp.ref.startswith("C"):
            info = LCSC_MAP.get("电容", {})
            package = info.get("package", "0805")
            lcsc = info.get("part", "")
            desc = "贴片电容"

        lines.append(f"| {idx} | {comp.ref} | {desc} | {value} | {package} | {lcsc} | 1 |")
        idx += 1

    lines.append("")
    lines.append(f"共 {idx - 1} 种器件")

    bom_text = "\n".join(lines)

    # 写入文件
    if filename is None:
        safe_name = circuit.name.replace(" ", "_").replace("/", "_")
        filename = f"{safe_name}_bom.md"
    filepath = output_path(filename)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(bom_text)

    return bom_text


def generate_spice(
    circuit: CircuitInstance,
    filename: str | None = None,
) -> str:
    """生成SPICE网表

    Args:
        circuit: 电路实例
        filename: 输出文件名

    Returns:
        SPICE网表文本
    """
    params = circuit.input_parameters
    lines: list[str] = []

    lines.append("* SchemaForge Generated SPICE Netlist")
    lines.append(f"* Circuit: {circuit.name}")
    lines.append(f"* Template: {circuit.template_name}")
    lines.append("")

    # 根据模板类型生成不同的SPICE
    tname = circuit.template_name

    if tname == "voltage_divider":
        v_in = params.get("v_in", "5")
        lines.append(f"V1 VIN GND DC {v_in}")
        for comp in circuit.components:
            if comp.ref == "R1":
                lines.append(f"R1 VIN VMID {_spice_value(comp.parameters.get('value', '10k'))}")
            elif comp.ref == "R2":
                lines.append(f"R2 VMID GND {_spice_value(comp.parameters.get('value', '10k'))}")

    elif tname == "ldo_regulator":
        v_in = params.get("v_in", "5")
        v_out = params.get("v_out", "3.3")
        c_in = params.get("c_in", "10u")
        c_out = params.get("c_out", "22u")
        ic_model = params.get("ic_model", "AMS1117")
        lines.append(f"V1 VIN GND DC {v_in}")
        lines.append(f"XU1 VIN VOUT GND {ic_model}")
        lines.append(f"C1 VIN GND {_spice_cap(c_in)}")
        lines.append(f"C2 VOUT GND {_spice_cap(c_out)}")
        lines.append("")
        lines.append(f".model {ic_model} NMOS(Vto={v_out})")

    elif tname == "led_indicator":
        v_supply = params.get("v_supply", "3.3")
        color = params.get("led_color", "green")
        vf = {"red": 2.0, "green": 2.2, "blue": 3.0, "white": 3.0}.get(color, 2.0)
        lines.append(f"V1 VCC GND DC {v_supply}")
        for comp in circuit.components:
            if comp.ref.startswith("R"):
                lines.append(f"R1 VCC LED_A {_spice_value(comp.parameters.get('value', '120'))}")
            elif comp.ref.startswith("D"):
                lines.append(f"D1 LED_A GND LED_{color.upper()}")
        lines.append("")
        lines.append(f".model LED_{color.upper()} D(Is=1e-20 N=1.8 Vj={vf})")

    elif tname == "rc_lowpass":
        lines.append("V1 IN GND DC 1 AC 1")
        for comp in circuit.components:
            if comp.ref.startswith("R"):
                lines.append(f"R1 IN OUT {_spice_value(comp.parameters.get('value', '10k'))}")
            elif comp.ref.startswith("C"):
                lines.append(f"C1 OUT GND {_spice_value(comp.parameters.get('value', '15.9n'))}")
        lines.append("")
        lines.append(".ac dec 100 1 1Meg")

    lines.append("")
    lines.append(".end")

    spice_text = "\n".join(lines)

    # 写入文件
    if filename is None:
        safe_name = circuit.name.replace(" ", "_").replace("/", "_")
        filename = f"{safe_name}.spice"
    filepath = output_path(filename)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(spice_text)

    return spice_text


def _spice_value(value_str: str) -> str:
    """将器件值字符串转为SPICE兼容格式

    电阻: "110Ω" -> "110", "10kΩ" -> "10k", "4.7MΩ" -> "4.7Meg"
    电容: "10μF" -> "10u", "22μF" -> "22u", "100nF" -> "100n"
    通用: 去除中文单位，保留SPICE兼容后缀
    """
    s = value_str.strip()

    # 电容单位
    s = s.replace("μF", "u").replace("uF", "u")
    s = s.replace("nF", "n").replace("pF", "p")
    s = s.replace("mF", "m")

    # 电阻单位
    s = s.replace("MΩ", "Meg").replace("kΩ", "k")
    s = s.replace("Ω", "")  # 纯Ω直接去掉

    # 电感单位
    s = s.replace("μH", "u").replace("mH", "m")
    s = s.replace("nH", "n").replace("pH", "p")

    # 兜底：去掉任何残留的非ASCII非数字非SI后缀字符
    # SPICE允许的后缀: T, G, Meg, k, m, u, n, p, f
    return s


def _spice_cap(value_str: str) -> str:
    """向后兼容：电容值转SPICE格式"""
    return _spice_value(value_str)
