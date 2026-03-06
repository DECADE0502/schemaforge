"""SchemaForge 参数计算器

提供电路参数计算功能：
- E24标准阻值匹配
- 分压计算
- LED限流电阻计算
- RC滤波器截止频率计算
- 电容值格式化
"""

from __future__ import annotations

import math
from typing import Any

from schemaforge.render.base import find_nearest_e24, format_value


# LED正向压降查表（V）
LED_FORWARD_VOLTAGE: dict[str, float] = {
    "red": 2.0,
    "green": 2.2,
    "blue": 3.0,
    "white": 3.0,
}


def calculate_divider(v_in: float, v_out: float, r_total: float = 20.0) -> dict[str, Any]:
    """计算分压器参数

    Args:
        v_in: 输入电压（V）
        v_out: 期望输出电压（V）
        r_total: 总阻值预算（kΩ）

    Returns:
        包含r1, r2, v_out_actual等计算结果的字典
    """
    ratio = v_out / v_in
    r2_raw = r_total * 1000 * ratio
    r1_raw = r_total * 1000 - r2_raw

    r1 = find_nearest_e24(r1_raw)
    r2 = find_nearest_e24(r2_raw)
    v_out_actual = v_in * r2 / (r1 + r2)

    return {
        "r1_raw": r1_raw,
        "r2_raw": r2_raw,
        "r1": r1,
        "r2": r2,
        "r1_str": format_value(r1, "Ω"),
        "r2_str": format_value(r2, "Ω"),
        "v_out_actual": round(v_out_actual, 4),
        "error_percent": round(abs(v_out_actual - v_out) / v_out * 100, 2),
    }


def calculate_led_resistor(
    v_supply: float,
    led_color: str = "green",
    led_current: float = 10.0,
) -> dict[str, Any]:
    """计算LED限流电阻

    Args:
        v_supply: 电源电压（V）
        led_color: LED颜色
        led_current: LED电流（mA）

    Returns:
        包含r_value, v_forward等计算结果的字典
    """
    v_forward = LED_FORWARD_VOLTAGE.get(led_color, 2.0)
    r_raw = (v_supply - v_forward) / (led_current / 1000)

    if r_raw <= 0:
        # 电源电压不足以驱动LED
        return {
            "error": f"电源电压{v_supply}V不足以驱动{led_color} LED（正向压降{v_forward}V）",
            "v_forward": v_forward,
            "r_raw": 0,
            "r_value": 0,
            "r_str": "N/A",
        }

    r_value = find_nearest_e24(r_raw)
    actual_current = (v_supply - v_forward) / r_value * 1000  # mA

    return {
        "v_forward": v_forward,
        "r_raw": r_raw,
        "r_value": r_value,
        "r_str": format_value(r_value, "Ω"),
        "actual_current_ma": round(actual_current, 2),
    }


def calculate_rc_filter(
    f_cutoff: float,
    r_value: float = 10.0,
) -> dict[str, Any]:
    """计算RC低通滤波器参数

    Args:
        f_cutoff: 截止频率（Hz）
        r_value: 电阻值（kΩ）

    Returns:
        包含c_value, f_actual等计算结果的字典
    """
    r_ohm = r_value * 1000
    c_raw = 1 / (2 * math.pi * f_cutoff * r_ohm)  # F

    # 电容不做E24匹配，直接用计算值
    c_str = format_value(c_raw, "F")

    # 反算实际截止频率
    f_actual = 1 / (2 * math.pi * r_ohm * c_raw)

    return {
        "r_ohm": r_ohm,
        "r_str": format_value(r_ohm, "Ω"),
        "c_raw": c_raw,
        "c_str": c_str,
        "f_actual": round(f_actual, 2),
    }


def evaluate_template_calculations(
    calculations: dict[str, str],
    parameters: dict[str, Any],
) -> dict[str, Any]:
    """执行模板的计算规则

    安全地评估模板中定义的Python表达式。

    Args:
        calculations: 计算规则字典（名称→表达式）
        parameters: 当前参数值

    Returns:
        计算结果字典
    """
    context: dict[str, Any] = {}
    context.update(parameters)
    context["math"] = math
    context["pi"] = math.pi

    results: dict[str, Any] = {}

    # 按依赖顺序执行（简单的多遍执行）
    remaining = dict(calculations)
    max_passes = 10
    for _ in range(max_passes):
        if not remaining:
            break
        resolved_this_pass: list[str] = []
        for name, expr in remaining.items():
            try:
                value = eval(expr, {"__builtins__": {}}, context)  # noqa: S307
                results[name] = value
                context[name] = value
                resolved_this_pass.append(name)
            except NameError:
                # 依赖尚未解析，下一遍再试
                continue
            except Exception as e:
                results[name] = f"ERROR: {e}"
                resolved_this_pass.append(name)
        for name in resolved_this_pass:
            remaining.pop(name, None)

    # 未能解析的
    for name in remaining:
        results[name] = f"UNRESOLVED: {remaining[name]}"

    return results
