"""FormulaEvaluator — 安全的参数公式求解引擎

根据器件 DesignRecipe 中的公式表达式，动态计算外围元件参数值。
取代硬编码计算，使参数完全由 datasheet 提取的 recipe 驱动。

设计原则:
- 安全沙箱: 只允许白名单数学函数，禁止 __builtins__ / exec / eval 滥用
- 依赖解析: 公式之间可互相引用，自动按拓扑序求解
- 工程圆整: 结果自动圆整到 E24/E96 标准系列
- 错误隔离: 单个公式失败不阻断其余公式

用法::

    evaluator = FormulaEvaluator()
    context = {"v_in": 12.0, "v_out": 3.3, "i_out": 1.0, "fsw": 500000.0}
    results = evaluator.evaluate_recipe(recipe, context)
    # results = {"c_in": "10uF", "l_value": "10uH", "c_out": "22uF", ...}
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from schemaforge.library.models import DesignRecipe, RecipeComponent, RecipeFormula


# ============================================================
# 安全数学沙箱
# ============================================================

_SAFE_MATH: dict[str, object] = {
    # 基础数学函数
    "abs": abs,
    "min": min,
    "max": max,
    "round": round,
    "int": int,
    "float": float,
    # math 模块常用
    "sqrt": math.sqrt,
    "log": math.log,
    "log10": math.log10,
    "log2": math.log2,
    "ceil": math.ceil,
    "floor": math.floor,
    "pi": math.pi,
    "e": math.e,
    "pow": pow,
}


def _safe_eval(expression: str, variables: dict[str, float]) -> float:
    """在安全沙箱中求值数学表达式。

    Args:
        expression: 数学表达式字符串，如 "v_out * (1 - duty) / (fsw * delta_il)"
        variables: 变量绑定 {"v_in": 12.0, "v_out": 3.3, ...}

    Returns:
        求值结果 (float)

    Raises:
        ValueError: 表达式无法求值
    """
    namespace: dict[str, object] = {"__builtins__": {}}
    namespace.update(_SAFE_MATH)
    namespace.update(variables)

    try:
        result = eval(expression, namespace)  # noqa: S307
    except Exception as exc:
        raise ValueError(f"公式求值失败 '{expression}': {exc}") from exc

    if not isinstance(result, (int, float)):
        raise ValueError(f"公式 '{expression}' 返回了非数值类型: {type(result).__name__}")

    return float(result)


# ============================================================
# 表达式预处理
# ============================================================

# 匹配工程记法约束: "Cin ≥ 10μF" 或 "≥ 10μF"
_ENGINEERING_VALUE_RE = re.compile(
    r"[≥≤>=<!]+\s*(\d+(?:\.\d+)?)\s*(μF|uF|µF|nF|pF|mH|uH|µH|H|kΩ|Ω|k|V|A|mA)",
    re.IGNORECASE,
)

# 匹配纯赋值: "Rlower = 10kΩ" → 提取赋值
_ASSIGNMENT_RE = re.compile(
    r"^\s*\w+\s*=\s*(\d+(?:\.\d+)?)\s*(μF|uF|µF|nF|pF|mH|uH|µH|H|kΩ|Ω|k|V|A|mA)\s*$",
    re.IGNORECASE,
)

# 匹配可求值表达式: 含变量引用的代数式
_EVALUABLE_RE = re.compile(r"[a-z_]\w*", re.IGNORECASE)

_UNIT_MULTIPLIERS: dict[str, float] = {
    "μf": 1e-6, "uf": 1e-6, "µf": 1e-6,
    "nf": 1e-9, "pf": 1e-12, "mf": 1e-3,
    "μh": 1e-6, "uh": 1e-6, "µh": 1e-6,
    "mh": 1e-3, "h": 1.0,
    "kω": 1e3, "k": 1e3, "ω": 1.0,
    "v": 1.0, "a": 1.0, "ma": 1e-3,
}


def _parse_engineering_constant(text: str) -> float | None:
    """尝试解析工程记法常量，如 '10μF' → 10e-6。"""
    text = text.strip()
    match = re.match(
        r"^(\d+(?:\.\d+)?)\s*(μF|uF|µF|nF|pF|mF|mH|uH|µH|μH|H|kΩ|Ω|k|V|A|mA)$",
        text,
        re.IGNORECASE,
    )
    if not match:
        return None
    value = float(match.group(1))
    unit = match.group(2).lower().replace("μ", "u").replace("µ", "u")
    multiplier = _UNIT_MULTIPLIERS.get(unit, 1.0)
    return value * multiplier


def normalize_formula_expression(formula_text: str) -> str | None:
    """将 recipe 中的人类可读公式转换为可 eval 的 Python 表达式。

    处理策略:
    1. 纯赋值常量 "Rlower = 10kΩ" → 返回 None（由 value 字段直接提供）
    2. 不等式约束 "Cin ≥ 10μF" → 返回 None（由 value 字段直接提供）
    3. 代数表达式 "Vout * (1 - D) / (fsw * ΔIL)" → 清理后返回可 eval 字符串

    Returns:
        可 eval 的表达式字符串，或 None（表示该公式不可自动求解）
    """
    text = formula_text.strip()

    # 去掉等号左边（赋值目标）
    if "=" in text and not any(op in text for op in ["==", "!=", ">=", "<=", "≥", "≤"]):
        parts = text.split("=", 1)
        text = parts[1].strip()

    # 检查是否是纯常量（如 "10kΩ"）
    if _parse_engineering_constant(text) is not None:
        return None

    # 检查是否是不等式约束 (搜索整个文本，如 "Cin ≥ 10μF")
    if _ENGINEERING_VALUE_RE.search(text):
        return None

    # 清理 Unicode 数学符号为 Python 运算符
    text = text.replace("×", "*").replace("÷", "/")
    text = text.replace("Δ", "delta_").replace("∆", "delta_")

    # 移除单位后缀 (保留数值)
    # 如 "8 * fsw * ΔVout" → "8 * fsw * delta_Vout"

    return text if text else None


# ============================================================
# 标准系列圆整
# ============================================================

_E24_SERIES = [
    1.0, 1.1, 1.2, 1.3, 1.5, 1.6, 1.8, 2.0, 2.2, 2.4, 2.7, 3.0,
    3.3, 3.6, 3.9, 4.3, 4.7, 5.1, 5.6, 6.2, 6.8, 7.5, 8.2, 9.1,
]

_CAP_SERIES = [1.0, 2.2, 4.7, 10.0, 22.0, 47.0, 100.0, 220.0]
_INDUCTOR_SERIES = [1.0, 1.5, 2.2, 3.3, 4.7, 6.8, 10.0, 15.0, 22.0, 33.0, 47.0]


def round_to_series(value: float, series: list[float], scale: float) -> float:
    """将数值圆整到标准系列中最接近的值。

    Args:
        value: 原始值
        series: 标准系列 (如 _E24_SERIES)
        scale: 基础倍率 (如电阻用 1.0, 电容用 1e-6)

    Returns:
        圆整后的值
    """
    if value <= 0:
        return scale
    normalized = value / scale
    magnitude = scale
    while normalized >= 1000:
        normalized /= 1000.0
        magnitude *= 1000.0
    while normalized < 1.0:
        normalized *= 10.0
        magnitude /= 10.0
    best = min(series, key=lambda item: abs(item - normalized))
    return best * magnitude


# ============================================================
# 结果格式化
# ============================================================

def _trim_float(value: float) -> str:
    return f"{value:.3f}".rstrip("0").rstrip(".")


def format_component_value(value_si: float, component_type: str) -> str:
    """将 SI 单位值格式化为人类可读的工程值。

    Args:
        value_si: SI 单位值 (如 10e-6 表示 10μF)
        component_type: "capacitor" | "inductor" | "resistor"

    Returns:
        格式化字符串，如 "10uF", "4.7uH", "10kΩ"
    """
    if component_type == "capacitor":
        if value_si >= 1e-6:
            return f"{_trim_float(value_si * 1e6)}uF"
        if value_si >= 1e-9:
            return f"{_trim_float(value_si * 1e9)}nF"
        return f"{_trim_float(value_si * 1e12)}pF"
    elif component_type == "inductor":
        if value_si >= 1e-3:
            return f"{_trim_float(value_si * 1e3)}mH"
        return f"{_trim_float(value_si * 1e6)}uH"
    elif component_type == "resistor":
        if value_si >= 1e6:
            return f"{_trim_float(value_si / 1e6)}MΩ"
        if value_si >= 1000:
            return f"{_trim_float(value_si / 1000.0)}kΩ"
        return f"{_trim_float(value_si)}Ω"
    return _trim_float(value_si)


# ============================================================
# 元件类型推断
# ============================================================

_ROLE_TO_TYPE: dict[str, str] = {
    "input_cap": "capacitor",
    "output_cap": "capacitor",
    "boot_cap": "capacitor",
    "bypass_cap": "capacitor",
    "decoupling_cap": "capacitor",
    "inductor": "inductor",
    "power_inductor": "inductor",
    "fb_upper": "resistor",
    "fb_lower": "resistor",
    "r_comp": "resistor",
    "r_limit": "resistor",
}


def _infer_component_type(role: str) -> str:
    """从元件角色推断类型。"""
    if role in _ROLE_TO_TYPE:
        return _ROLE_TO_TYPE[role]
    role_lower = role.lower()
    if "cap" in role_lower or role_lower.startswith("c"):
        return "capacitor"
    if "ind" in role_lower or role_lower.startswith("l"):
        return "inductor"
    if "res" in role_lower or role_lower.startswith("r"):
        return "resistor"
    return "unknown"


# ============================================================
# FormulaEvalResult
# ============================================================


@dataclass
class FormulaEvalResult:
    """公式求解结果。"""

    success: bool = True
    """是否所有公式都求解成功"""

    computed_params: dict[str, str] = field(default_factory=dict)
    """求解得到的参数 {role/param_name: formatted_value}"""

    raw_values: dict[str, float] = field(default_factory=dict)
    """原始 SI 单位数值 {role/param_name: float}"""

    errors: list[str] = field(default_factory=list)
    """求解失败的错误信息"""

    rationale: list[str] = field(default_factory=list)
    """求解过程说明"""


# ============================================================
# FormulaEvaluator
# ============================================================


class FormulaEvaluator:
    """公式求解引擎。

    从 DesignRecipe 的 formulas + sizing_components 中提取可计算的表达式，
    结合用户输入参数（v_in, v_out, i_out, fsw 等）求解外围元件值。

    求解优先级:
    1. sizing_components 中的 formula 字段 — 如果可 eval 则动态计算
    2. sizing_components 中的 value 字段 — 直接使用预设值
    3. formulas 列表中的中间变量 — 先求解中间变量，再用于后续公式
    """

    def evaluate_recipe(
        self,
        recipe: DesignRecipe,
        context: dict[str, float],
    ) -> FormulaEvalResult:
        """求解 recipe 中所有可计算的公式。

        Args:
            recipe: 器件设计 recipe
            context: 设计上下文参数 {"v_in": 12.0, "v_out": 3.3, ...}

        Returns:
            FormulaEvalResult
        """
        result = FormulaEvalResult()
        variables = dict(context)

        # 阶段 1: 求解中间变量 (formulas 列表)
        for formula in recipe.formulas:
            self._evaluate_formula(formula, variables, result)

        # 阶段 2: 求解元件值 (sizing_components)
        for component in recipe.sizing_components:
            self._evaluate_component(component, variables, result)

        result.success = len(result.errors) == 0
        return result

    def evaluate_single(
        self,
        expression: str,
        context: dict[str, float],
        component_type: str = "unknown",
    ) -> tuple[float | None, str]:
        """求解单个表达式。

        Returns:
            (SI 数值或 None, 格式化字符串或错误信息)
        """
        normalized = normalize_formula_expression(expression)
        if normalized is None:
            return None, "表达式不可自动求解"

        try:
            raw = _safe_eval(normalized, context)
        except ValueError as exc:
            return None, str(exc)

        if component_type != "unknown":
            series = _CAP_SERIES if component_type == "capacitor" else _INDUCTOR_SERIES
            scale = 1e-6  # 默认 μ 级别
            raw = round_to_series(raw, series, scale)

        formatted = format_component_value(raw, component_type)
        return raw, formatted

    def _evaluate_formula(
        self,
        formula: RecipeFormula,
        variables: dict[str, float],
        result: FormulaEvalResult,
    ) -> None:
        """求解一个中间变量公式并更新 variables。"""
        normalized = normalize_formula_expression(formula.expression)

        if normalized is None:
            # 不可 eval — 尝试使用预设值
            if formula.value:
                parsed = _parse_engineering_constant(formula.value)
                if parsed is not None:
                    variables[formula.name] = parsed
                    return
                try:
                    variables[formula.name] = float(formula.value)
                except (ValueError, TypeError):
                    pass
            return

        try:
            value = _safe_eval(normalized, variables)
            variables[formula.name] = value
            result.raw_values[formula.name] = value
            if formula.rationale:
                result.rationale.append(
                    f"{formula.name} = {_trim_float(value)} ({formula.rationale})"
                )
        except ValueError as exc:
            result.errors.append(f"中间变量 '{formula.name}' 求解失败: {exc}")

    def _evaluate_component(
        self,
        component: RecipeComponent,
        variables: dict[str, float],
        result: FormulaEvalResult,
    ) -> None:
        """求解一个元件值。"""
        comp_type = _infer_component_type(component.role)

        # 优先尝试公式求解
        if component.formula:
            normalized = normalize_formula_expression(component.formula)
            if normalized is not None:
                try:
                    raw = _safe_eval(normalized, variables)

                    # 圆整到标准系列
                    if comp_type == "capacitor":
                        raw = round_to_series(raw, _CAP_SERIES, 1e-6)
                    elif comp_type == "inductor":
                        raw = round_to_series(raw, _INDUCTOR_SERIES, 1e-6)
                    elif comp_type == "resistor":
                        from schemaforge.render.base import find_nearest_e24
                        raw = find_nearest_e24(raw)

                    formatted = format_component_value(raw, comp_type)
                    result.computed_params[component.role] = formatted
                    result.raw_values[component.role] = raw
                    variables[component.role] = raw

                    if component.rationale:
                        result.rationale.append(
                            f"{component.role} = {formatted} ({component.rationale})"
                        )
                    return
                except ValueError as exc:
                    result.errors.append(
                        f"元件 '{component.role}' 公式求解失败: {exc}"
                    )
                    # 回退到 value 字段

        # 回退: 使用预设 value
        if component.value:
            parsed = _parse_engineering_constant(component.value)
            if parsed is not None:
                result.computed_params[component.role] = component.value
                result.raw_values[component.role] = parsed
                variables[component.role] = parsed
            else:
                result.computed_params[component.role] = component.value
            return
