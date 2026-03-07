"""FormulaEvaluator 测试"""

from __future__ import annotations

import pytest

from schemaforge.design.formula_eval import (
    FormulaEvaluator,
    _parse_engineering_constant,
    _safe_eval,
    format_component_value,
    normalize_formula_expression,
    round_to_series,
)
from schemaforge.library.models import (
    DesignRecipe,
    RecipeComponent,
    RecipeEvidence,
    RecipeFormula,
)


# ============================================================
# _safe_eval
# ============================================================


class TestSafeEval:
    """安全求值沙箱测试"""

    def test_basic_arithmetic(self) -> None:
        assert _safe_eval("2 + 3", {}) == 5.0
        assert _safe_eval("10 / 4", {}) == 2.5
        assert _safe_eval("2 ** 3", {}) == 8.0

    def test_variables(self) -> None:
        assert _safe_eval("v_in - v_out", {"v_in": 12.0, "v_out": 3.3}) == pytest.approx(8.7)

    def test_math_functions(self) -> None:
        assert _safe_eval("sqrt(16)", {}) == 4.0
        assert _safe_eval("max(3, 5)", {}) == 5.0
        assert _safe_eval("min(3, 5)", {}) == 3.0
        assert _safe_eval("abs(-7)", {}) == 7.0

    def test_complex_expression(self) -> None:
        ctx = {"v_out": 3.3, "duty": 0.275, "fsw": 500000.0, "delta_il": 0.3}
        result = _safe_eval("v_out * (1 - duty) / (fsw * delta_il)", ctx)
        expected = 3.3 * (1 - 0.275) / (500000.0 * 0.3)
        assert result == pytest.approx(expected)

    def test_rejects_builtins(self) -> None:
        with pytest.raises(ValueError, match="求值失败"):
            _safe_eval("__import__('os')", {})

    def test_rejects_non_numeric(self) -> None:
        with pytest.raises(ValueError, match="非数值类型"):
            _safe_eval("'hello'", {})

    def test_missing_variable(self) -> None:
        with pytest.raises(ValueError, match="求值失败"):
            _safe_eval("undefined_var * 2", {})


# ============================================================
# normalize_formula_expression
# ============================================================


class TestNormalizeFormula:
    """公式预处理测试"""

    def test_assignment_constant(self) -> None:
        # 纯常量赋值 → None (由 value 字段提供)
        assert normalize_formula_expression("Rlower = 10kΩ") is None

    def test_inequality_constraint(self) -> None:
        # 不等式约束 → None
        assert normalize_formula_expression("≥ 10μF") is None
        assert normalize_formula_expression("Cin ≥ 10μF") is None

    def test_algebraic_expression(self) -> None:
        result = normalize_formula_expression("Vout * (1 - D) / (fsw * ΔIL)")
        assert result is not None
        assert "delta_IL" in result
        assert "*" in result

    def test_strips_lhs(self) -> None:
        result = normalize_formula_expression("D = Vout / Vin")
        assert result is not None
        assert result.strip() == "Vout / Vin"

    def test_unicode_multiply(self) -> None:
        result = normalize_formula_expression("A × B ÷ C")
        assert result is not None
        assert "×" not in result
        assert "*" in result
        assert "/" in result

    def test_empty_string(self) -> None:
        assert normalize_formula_expression("") is None

    def test_pure_constant(self) -> None:
        assert normalize_formula_expression("10uF") is None
        assert normalize_formula_expression("4.7kΩ") is None


# ============================================================
# _parse_engineering_constant
# ============================================================


class TestParseEngineeringConstant:
    """工程常量解析测试"""

    def test_microfarad(self) -> None:
        assert _parse_engineering_constant("10uF") == pytest.approx(10e-6)
        assert _parse_engineering_constant("10μF") == pytest.approx(10e-6)

    def test_nanofarad(self) -> None:
        assert _parse_engineering_constant("100nF") == pytest.approx(100e-9)

    def test_picofarad(self) -> None:
        assert _parse_engineering_constant("47pF") == pytest.approx(47e-12)

    def test_microhenry(self) -> None:
        assert _parse_engineering_constant("4.7uH") == pytest.approx(4.7e-6)

    def test_kilohm(self) -> None:
        assert _parse_engineering_constant("10kΩ") == pytest.approx(10000.0)

    def test_ohm(self) -> None:
        assert _parse_engineering_constant("100Ω") == pytest.approx(100.0)

    def test_milliamp(self) -> None:
        assert _parse_engineering_constant("500mA") == pytest.approx(0.5)

    def test_invalid(self) -> None:
        assert _parse_engineering_constant("hello") is None
        assert _parse_engineering_constant("") is None


# ============================================================
# round_to_series
# ============================================================


class TestRoundToSeries:
    """标准系列圆整测试"""

    def test_cap_series(self) -> None:
        series = [1.0, 2.2, 4.7, 10.0, 22.0, 47.0, 100.0]
        # 15e-6 应该圆整到 10uF 或 22uF
        result = round_to_series(15e-6, series, 1e-6)
        assert result == pytest.approx(10e-6) or result == pytest.approx(22e-6)

    def test_zero_value(self) -> None:
        result = round_to_series(0, [1.0, 2.2, 4.7], 1e-6)
        assert result == 1e-6

    def test_exact_match(self) -> None:
        result = round_to_series(10e-6, [1.0, 2.2, 4.7, 10.0], 1e-6)
        assert result == pytest.approx(10e-6)


# ============================================================
# format_component_value
# ============================================================


class TestFormatComponentValue:
    """工程值格式化测试"""

    def test_capacitor_uf(self) -> None:
        assert format_component_value(10e-6, "capacitor") == "10uF"

    def test_capacitor_nf(self) -> None:
        assert format_component_value(100e-9, "capacitor") == "100nF"

    def test_inductor_uh(self) -> None:
        assert format_component_value(4.7e-6, "inductor") == "4.7uH"

    def test_resistor_kohm(self) -> None:
        assert format_component_value(10000, "resistor") == "10kΩ"

    def test_resistor_ohm(self) -> None:
        assert format_component_value(470, "resistor") == "470Ω"

    def test_resistor_mohm(self) -> None:
        assert format_component_value(1e6, "resistor") == "1MΩ"


# ============================================================
# FormulaEvaluator.evaluate_recipe — 完整 recipe 求解
# ============================================================


class TestFormulaEvaluator:
    """完整 recipe 求解测试"""

    def setup_method(self) -> None:
        self.evaluator = FormulaEvaluator()

    def test_buck_recipe_evaluation(self) -> None:
        """典型 Buck recipe 中的公式应该能被正确求解"""
        recipe = DesignRecipe(
            topology_family="buck",
            summary="TPS54202 Buck recipe",
            formulas=[
                RecipeFormula(
                    name="duty",
                    expression="D = v_out / v_in",
                    rationale="基本降压占空比",
                ),
                RecipeFormula(
                    name="delta_il",
                    expression="delta_il = i_out * 0.3",
                    rationale="30% 电流纹波",
                ),
            ],
            sizing_components=[
                RecipeComponent(
                    role="inductor",
                    formula="L = v_out * (1 - duty) / (fsw * delta_il)",
                    rationale="按纹波设计电感值",
                ),
                RecipeComponent(
                    role="output_cap",
                    formula="Cout = delta_il / (8 * fsw * 0.01 * v_out)",
                    rationale="按 1% 纹波设计输出电容",
                ),
                RecipeComponent(
                    role="input_cap",
                    value="10uF",
                    formula="Cin ≥ 10μF",
                    rationale="按推荐最小值",
                ),
                RecipeComponent(
                    role="fb_lower",
                    value="10kΩ",
                    formula="Rlower = 10kΩ",
                    rationale="固定下拉电阻",
                ),
            ],
            evidence=[
                RecipeEvidence(source_type="datasheet", summary="TPS54202 典型应用电路"),
            ],
        )

        context = {
            "v_in": 12.0,
            "v_out": 3.3,
            "i_out": 1.0,
            "fsw": 500000.0,
        }

        result = self.evaluator.evaluate_recipe(recipe, context)

        # 中间变量应被求解
        assert "duty" in result.raw_values
        assert result.raw_values["duty"] == pytest.approx(3.3 / 12.0)

        # 电感应被计算并格式化
        assert "inductor" in result.computed_params
        inductor_value = result.computed_params["inductor"]
        assert "uH" in inductor_value or "mH" in inductor_value

        # 输出电容应被计算
        assert "output_cap" in result.computed_params

        # 输入电容 fallback 到 value 字段
        assert result.computed_params["input_cap"] == "10uF"

        # 下拉电阻 fallback 到 value 字段
        assert result.computed_params["fb_lower"] == "10kΩ"

    def test_ldo_recipe_with_constants_only(self) -> None:
        """LDO recipe 中全是常量值时应直接使用 value 字段"""
        recipe = DesignRecipe(
            topology_family="ldo",
            summary="AMS1117 LDO recipe",
            sizing_components=[
                RecipeComponent(
                    role="input_cap",
                    value="10uF",
                    formula="Cin ≥ 10μF",
                    rationale="去耦电容",
                ),
                RecipeComponent(
                    role="output_cap",
                    value="22uF",
                    formula="Cout ≥ 22μF",
                    rationale="输出稳定性",
                ),
            ],
        )

        result = self.evaluator.evaluate_recipe(recipe, {"v_in": 5.0, "v_out": 3.3})

        assert result.computed_params["input_cap"] == "10uF"
        assert result.computed_params["output_cap"] == "22uF"
        assert len(result.errors) == 0

    def test_partial_failure_does_not_block_others(self) -> None:
        """单个公式失败不应阻断其他公式"""
        recipe = DesignRecipe(
            topology_family="buck",
            sizing_components=[
                RecipeComponent(
                    role="inductor",
                    formula="L = v_out * (1 - duty) / (fsw * delta_il)",
                    rationale="missing duty variable",
                ),
                RecipeComponent(
                    role="input_cap",
                    value="10uF",
                    rationale="常量",
                ),
            ],
        )

        result = self.evaluator.evaluate_recipe(recipe, {"v_in": 12.0, "v_out": 3.3, "fsw": 500000.0})

        # inductor 公式应失败（缺少 duty 和 delta_il 变量）
        assert len(result.errors) >= 1
        assert "inductor" in result.errors[0]

        # input_cap 应成功
        assert result.computed_params["input_cap"] == "10uF"

    def test_empty_recipe(self) -> None:
        """空 recipe 应返回空结果"""
        recipe = DesignRecipe()
        result = self.evaluator.evaluate_recipe(recipe, {})
        assert result.success is True
        assert len(result.computed_params) == 0

    def test_evaluate_single_expression(self) -> None:
        """单个表达式求解"""
        raw, formatted = self.evaluator.evaluate_single(
            "v_out / v_in",
            {"v_in": 12.0, "v_out": 3.3},
        )
        assert raw == pytest.approx(3.3 / 12.0)

    def test_evaluate_single_constant_returns_none(self) -> None:
        """常量表达式返回 None"""
        raw, msg = self.evaluator.evaluate_single("10uF", {})
        assert raw is None

    def test_formula_chain_evaluation(self) -> None:
        """公式之间的依赖链应正确求解"""
        recipe = DesignRecipe(
            formulas=[
                RecipeFormula(name="duty", expression="D = v_out / v_in"),
                RecipeFormula(name="delta_il", expression="delta_il = i_out * 0.3"),
                RecipeFormula(name="ripple_v", expression="ripple_v = v_out * 0.01"),
            ],
            sizing_components=[
                RecipeComponent(
                    role="output_cap",
                    formula="Cout = delta_il / (8 * fsw * ripple_v)",
                    rationale="纹波驱动",
                ),
            ],
        )

        context = {"v_in": 12.0, "v_out": 5.0, "i_out": 2.0, "fsw": 500000.0}
        result = self.evaluator.evaluate_recipe(recipe, context)

        # 所有中间变量都应被求解
        assert "duty" in result.raw_values
        assert "delta_il" in result.raw_values
        assert "ripple_v" in result.raw_values

        # 最终元件值应被计算
        assert "output_cap" in result.computed_params
        assert len(result.errors) == 0

    def test_resistor_feedback_divider(self) -> None:
        """反馈分压电阻计算"""
        recipe = DesignRecipe(
            formulas=[
                RecipeFormula(name="r_lower", expression="r_lower = 10000"),
            ],
            sizing_components=[
                RecipeComponent(
                    role="fb_upper",
                    formula="Rupper = r_lower * (v_out / v_ref - 1)",
                    rationale="反馈上拉",
                ),
                RecipeComponent(
                    role="fb_lower",
                    value="10kΩ",
                    rationale="固定下拉",
                ),
            ],
        )

        context = {"v_out": 3.3, "v_ref": 0.8}
        result = self.evaluator.evaluate_recipe(recipe, context)

        assert "fb_upper" in result.computed_params
        # 3.3/0.8 - 1 = 3.125 → 10000 * 3.125 = 31250 → E24 nearest
        assert "kΩ" in result.computed_params["fb_upper"]
        assert "fb_lower" in result.computed_params
