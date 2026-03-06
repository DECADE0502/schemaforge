"""SchemaForge 渲染基类

提供所有模板渲染函数的公共工具方法。
"""

from __future__ import annotations

from pathlib import Path

# 默认输出目录
OUTPUT_DIR = Path(__file__).parent.parent / "output"


def ensure_output_dir() -> Path:
    """确保输出目录存在并返回路径"""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    return OUTPUT_DIR


def output_path(filename: str) -> str:
    """获取输出文件完整路径"""
    ensure_output_dir()
    return str(OUTPUT_DIR / filename)


def format_value(value: float, unit: str = "Ω") -> str:
    """将数值格式化为工程单位字符串

    Examples:
        format_value(10000, "Ω") -> "10kΩ"
        format_value(0.000001, "F") -> "1μF"
        format_value(120, "Ω") -> "120Ω"
    """
    if unit == "Ω":
        if value >= 1_000_000:
            return f"{value / 1_000_000:.1f}MΩ"
        elif value >= 1_000:
            return f"{value / 1_000:.1f}kΩ"
        else:
            return f"{value:.0f}Ω"
    elif unit == "F":
        if value >= 1e-3:
            return f"{value * 1e3:.0f}mF"
        elif value >= 1e-6:
            return f"{value * 1e6:.0f}μF"
        elif value >= 1e-9:
            return f"{value * 1e9:.1f}nF"
        else:
            return f"{value * 1e12:.0f}pF"
    else:
        return f"{value}{unit}"


def find_nearest_e24(value: float) -> float:
    """找到最接近的E24标准阻值

    E24系列: 1.0, 1.1, 1.2, 1.3, 1.5, 1.6, 1.8, 2.0, 2.2, 2.4, 2.7, 3.0,
             3.3, 3.6, 3.9, 4.3, 4.7, 5.1, 5.6, 6.2, 6.8, 7.5, 8.2, 9.1
    """
    e24_base = [
        1.0, 1.1, 1.2, 1.3, 1.5, 1.6, 1.8, 2.0, 2.2, 2.4, 2.7, 3.0,
        3.3, 3.6, 3.9, 4.3, 4.7, 5.1, 5.6, 6.2, 6.8, 7.5, 8.2, 9.1,
    ]

    if value <= 0:
        return 1.0

    # 找到数量级
    magnitude = 1.0
    temp = value
    while temp >= 10:
        temp /= 10
        magnitude *= 10
    while temp < 1:
        temp *= 10
        magnitude /= 10

    # 找最接近的E24值
    best = e24_base[0]
    best_diff = abs(temp - best)
    for e in e24_base:
        diff = abs(temp - e)
        if diff < best_diff:
            best = e
            best_diff = diff

    return best * magnitude
