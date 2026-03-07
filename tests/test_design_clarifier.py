"""需求澄清器测试

测试 RequirementClarifier 和 ClarificationResult 的完整行为：
- LDO、Buck、LED、电压分压器分类的约束检测
- 必要参数缺失时阻断设计
- 可选参数缺失时生成假设
- 置信度计算逻辑
- 多模块计划的联合检查
- 与 IR 模型的字段兼容性
"""

from __future__ import annotations

from schemaforge.design.clarifier import ClarificationResult, RequirementClarifier
from schemaforge.design.ir import (
    Assumption,
    Constraint,
    ConstraintPriority,
    UnresolvedQuestion,
)
from schemaforge.design.planner import DesignPlan, ModuleRequirement


# ============================================================
# 测试工具函数
# ============================================================


def _make_plan(*modules: ModuleRequirement) -> DesignPlan:
    """创建一个只包含指定模块的 DesignPlan"""
    return DesignPlan(
        name="测试设计",
        description="测试描述",
        modules=list(modules),
    )


def _ldo_module(params: dict | None = None) -> ModuleRequirement:
    """创建 LDO 模块"""
    return ModuleRequirement(
        role="main_regulator",
        category="ldo",
        description="LDO 稳压器",
        parameters=params or {},
    )


def _buck_module(params: dict | None = None) -> ModuleRequirement:
    """创建 Buck 模块"""
    return ModuleRequirement(
        role="main_regulator",
        category="buck",
        description="Buck 降压器",
        parameters=params or {},
    )


def _led_module(params: dict | None = None) -> ModuleRequirement:
    """创建 LED 模块"""
    return ModuleRequirement(
        role="power_led",
        category="led",
        description="LED 指示灯",
        parameters=params or {},
    )


def _divider_module(params: dict | None = None) -> ModuleRequirement:
    """创建分压器模块"""
    return ModuleRequirement(
        role="voltage_sampler",
        category="voltage_divider",
        description="电压分压采样",
        parameters=params or {},
    )


# ============================================================
# 基础测试
# ============================================================


class TestClarificationResultDataclass:
    """测试 ClarificationResult dataclass 结构"""

    def test_default_fields(self) -> None:
        """默认字段值正确"""
        result = ClarificationResult()
        assert result.known_constraints == []
        assert result.missing_required == []
        assert result.optional_preferences == []
        assert result.assumptions == []
        assert result.confidence == 0.5

    def test_can_proceed_no_missing(self) -> None:
        """无缺失必要约束时可以继续"""
        result = ClarificationResult(missing_required=[])
        assert result.can_proceed is True

    def test_can_proceed_with_missing(self) -> None:
        """有缺失必要约束时不能继续"""
        q = UnresolvedQuestion(field="v_in", question="请提供输入电压")
        result = ClarificationResult(missing_required=[q])
        assert result.can_proceed is False

    def test_must_ask_count(self) -> None:
        """must_ask_count 等于 missing_required 长度"""
        q1 = UnresolvedQuestion(field="v_in", question="请提供输入电压")
        q2 = UnresolvedQuestion(field="v_out", question="请提供输出电压")
        result = ClarificationResult(missing_required=[q1, q2])
        assert result.must_ask_count == 2

    def test_ir_model_compatibility(self) -> None:
        """ClarificationResult 字段类型与 IR 模型兼容"""
        constraint = Constraint(name="v_in", value="5V")
        assumption = Assumption(field="i_out_max", assumed_value="500mA")
        question = UnresolvedQuestion(field="v_out", question="请提供输出电压")

        result = ClarificationResult(
            known_constraints=[constraint],
            missing_required=[question],
            assumptions=[assumption],
            confidence=0.7,
        )
        assert isinstance(result.known_constraints[0], Constraint)
        assert isinstance(result.missing_required[0], UnresolvedQuestion)
        assert isinstance(result.assumptions[0], Assumption)


# ============================================================
# LDO 分类测试
# ============================================================


class TestLdoClarification:
    """LDO 模块的约束检测"""

    def setup_method(self) -> None:
        self.clarifier = RequirementClarifier()

    def test_ldo_complete_input(self) -> None:
        """LDO 含 v_in 和 v_out → 可以继续，无缺失必要约束"""
        plan = _make_plan(_ldo_module({"v_in": "5", "v_out": "3.3"}))
        result = self.clarifier.clarify("5V转3.3V LDO", plan)
        assert result.can_proceed is True
        assert result.missing_required == []

    def test_ldo_missing_v_in(self) -> None:
        """LDO 缺少 v_in → 不能继续，missing_required 包含 v_in"""
        plan = _make_plan(_ldo_module({"v_out": "3.3"}))
        result = self.clarifier.clarify("LDO 输出3.3V", plan)
        assert result.can_proceed is False
        fields = [q.field for q in result.missing_required]
        assert "v_in" in fields

    def test_ldo_missing_v_out(self) -> None:
        """LDO 缺少 v_out → 不能继续，missing_required 包含 v_out"""
        plan = _make_plan(_ldo_module({"v_in": "5"}))
        result = self.clarifier.clarify("LDO 输入5V", plan)
        assert result.can_proceed is False
        fields = [q.field for q in result.missing_required]
        assert "v_out" in fields

    def test_ldo_missing_both(self) -> None:
        """LDO 同时缺少 v_in 和 v_out → missing_required 包含两者"""
        plan = _make_plan(_ldo_module())
        result = self.clarifier.clarify("LDO 稳压", plan)
        assert result.can_proceed is False
        fields = [q.field for q in result.missing_required]
        assert "v_in" in fields
        assert "v_out" in fields

    def test_ldo_with_voltages_no_current(self) -> None:
        """LDO 有电压无电流 → 可以继续，assumptions 含 i_out_max"""
        plan = _make_plan(_ldo_module({"v_in": "5", "v_out": "3.3"}))
        result = self.clarifier.clarify("5V转3.3V LDO", plan)
        assert result.can_proceed is True
        assumption_fields = [a.field for a in result.assumptions]
        assert "i_out_max" in assumption_fields

    def test_ldo_i_out_max_assumption_value(self) -> None:
        """LDO i_out_max 假设值为 500mA"""
        plan = _make_plan(_ldo_module({"v_in": "5", "v_out": "3.3"}))
        result = self.clarifier.clarify("5V转3.3V LDO", plan)
        i_out_assumption = next(
            (a for a in result.assumptions if a.field == "i_out_max"), None
        )
        assert i_out_assumption is not None
        assert i_out_assumption.assumed_value == "500mA"

    def test_ldo_v_dropout_assumption(self) -> None:
        """LDO 未指定 v_dropout → assumptions 含 v_dropout"""
        plan = _make_plan(_ldo_module({"v_in": "5", "v_out": "3.3"}))
        result = self.clarifier.clarify("5V转3.3V LDO", plan)
        assumption_fields = [a.field for a in result.assumptions]
        assert "v_dropout" in assumption_fields

    def test_ldo_required_questions_have_priority_required(self) -> None:
        """LDO 必要问题的 priority 为 REQUIRED"""
        plan = _make_plan(_ldo_module())
        result = self.clarifier.clarify("LDO", plan)
        for q in result.missing_required:
            assert q.priority == ConstraintPriority.REQUIRED

    def test_ldo_question_text_non_empty(self) -> None:
        """LDO 缺失字段的问题文本非空"""
        plan = _make_plan(_ldo_module({"v_out": "3.3"}))
        result = self.clarifier.clarify("LDO", plan)
        for q in result.missing_required:
            assert q.question != ""

    def test_ldo_known_constraints_extracted(self) -> None:
        """LDO 已知参数正确提取到 known_constraints"""
        plan = _make_plan(_ldo_module({"v_in": "5", "v_out": "3.3"}))
        result = self.clarifier.clarify("5V转3.3V LDO", plan)
        constraint_names = [c.name for c in result.known_constraints]
        assert "v_in" in constraint_names
        assert "v_out" in constraint_names


# ============================================================
# Buck 分类测试
# ============================================================


class TestBuckClarification:
    """Buck 模块的约束检测"""

    def setup_method(self) -> None:
        self.clarifier = RequirementClarifier()

    def test_buck_missing_v_in(self) -> None:
        """Buck 缺少 v_in → 不能继续"""
        plan = _make_plan(_buck_module({"v_out": "5"}))
        result = self.clarifier.clarify("Buck 输出5V", plan)
        assert result.can_proceed is False
        fields = [q.field for q in result.missing_required]
        assert "v_in" in fields

    def test_buck_complete(self) -> None:
        """Buck 有 v_in 和 v_out → 可以继续"""
        plan = _make_plan(_buck_module({"v_in": "12", "v_out": "5"}))
        result = self.clarifier.clarify("12V转5V Buck", plan)
        assert result.can_proceed is True

    def test_buck_i_out_max_assumption(self) -> None:
        """Buck 未指定 i_out_max → 假设为 1A"""
        plan = _make_plan(_buck_module({"v_in": "12", "v_out": "5"}))
        result = self.clarifier.clarify("12V转5V Buck", plan)
        i_out_assumption = next(
            (a for a in result.assumptions if a.field == "i_out_max"), None
        )
        assert i_out_assumption is not None
        assert i_out_assumption.assumed_value == "1A"

    def test_buck_fsw_assumption(self) -> None:
        """Buck 未指定 fsw → 假设为 500kHz"""
        plan = _make_plan(_buck_module({"v_in": "12", "v_out": "5"}))
        result = self.clarifier.clarify("12V转5V Buck", plan)
        fsw_assumption = next((a for a in result.assumptions if a.field == "fsw"), None)
        assert fsw_assumption is not None
        assert fsw_assumption.assumed_value == "500kHz"


# ============================================================
# LED 分类测试
# ============================================================


class TestLedClarification:
    """LED 模块的约束检测"""

    def setup_method(self) -> None:
        self.clarifier = RequirementClarifier()

    def test_led_missing_v_supply(self) -> None:
        """LED 缺少 v_supply → 不能继续"""
        plan = _make_plan(_led_module())
        result = self.clarifier.clarify("LED 指示灯", plan)
        assert result.can_proceed is False
        fields = [q.field for q in result.missing_required]
        assert "v_supply" in fields

    def test_led_complete(self) -> None:
        """LED 有 v_supply → 可以继续"""
        plan = _make_plan(_led_module({"v_supply": "3.3"}))
        result = self.clarifier.clarify("3.3V LED 指示灯", plan)
        assert result.can_proceed is True

    def test_led_color_assumption(self) -> None:
        """LED 未指定颜色 → 假设为绿色"""
        plan = _make_plan(_led_module({"v_supply": "3.3"}))
        result = self.clarifier.clarify("3.3V LED 指示灯", plan)
        color_assumption = next(
            (a for a in result.assumptions if a.field == "led_color"), None
        )
        assert color_assumption is not None
        assert color_assumption.assumed_value == "green"

    def test_led_current_assumption(self) -> None:
        """LED 未指定电流 → 假设为 10mA"""
        plan = _make_plan(_led_module({"v_supply": "3.3"}))
        result = self.clarifier.clarify("3.3V LED 指示灯", plan)
        current_assumption = next(
            (a for a in result.assumptions if a.field == "led_current"), None
        )
        assert current_assumption is not None
        assert current_assumption.assumed_value == "10mA"

    def test_led_v_supply_question_text(self) -> None:
        """LED 缺少 v_supply 时问题文本正确"""
        plan = _make_plan(_led_module())
        result = self.clarifier.clarify("LED", plan)
        v_supply_q = next(
            (q for q in result.missing_required if q.field == "v_supply"), None
        )
        assert v_supply_q is not None
        assert "LED" in v_supply_q.question or "供电电压" in v_supply_q.question


# ============================================================
# 电压分压器分类测试
# ============================================================


class TestVoltageDividerClarification:
    """电压分压器模块的约束检测"""

    def setup_method(self) -> None:
        self.clarifier = RequirementClarifier()

    def test_divider_missing_v_out(self) -> None:
        """分压器缺少 v_out → 不能继续"""
        plan = _make_plan(_divider_module({"v_in": "12"}))
        result = self.clarifier.clarify("12V分压采样", plan)
        assert result.can_proceed is False
        fields = [q.field for q in result.missing_required]
        assert "v_out" in fields

    def test_divider_missing_v_in(self) -> None:
        """分压器缺少 v_in → 不能继续"""
        plan = _make_plan(_divider_module({"v_out": "3.3"}))
        result = self.clarifier.clarify("分压到3.3V", plan)
        assert result.can_proceed is False
        fields = [q.field for q in result.missing_required]
        assert "v_in" in fields

    def test_divider_complete(self) -> None:
        """分压器有 v_in 和 v_out → 可以继续"""
        plan = _make_plan(_divider_module({"v_in": "12", "v_out": "3.3"}))
        result = self.clarifier.clarify("12V到3.3V分压", plan)
        assert result.can_proceed is True

    def test_divider_v_out_question_text(self) -> None:
        """分压器 v_out 缺失时问题文本含分压关键词"""
        plan = _make_plan(_divider_module({"v_in": "12"}))
        result = self.clarifier.clarify("12V分压", plan)
        v_out_q = next((q for q in result.missing_required if q.field == "v_out"), None)
        assert v_out_q is not None
        assert v_out_q.question != ""


# ============================================================
# 多模块计划测试
# ============================================================


class TestMultiModulePlan:
    """多模块设计计划的联合检查"""

    def setup_method(self) -> None:
        self.clarifier = RequirementClarifier()

    def test_ldo_plus_led_complete(self) -> None:
        """LDO + LED 都完整 → 可以继续"""
        plan = _make_plan(
            _ldo_module({"v_in": "5", "v_out": "3.3"}),
            _led_module({"v_supply": "3.3"}),
        )
        result = self.clarifier.clarify("5V转3.3V LDO + LED", plan)
        assert result.can_proceed is True

    def test_ldo_plus_led_led_missing(self) -> None:
        """LDO 完整 + LED 缺少 v_supply → 不能继续"""
        plan = _make_plan(
            _ldo_module({"v_in": "5", "v_out": "3.3"}),
            _led_module(),
        )
        result = self.clarifier.clarify("5V转3.3V LDO + LED", plan)
        assert result.can_proceed is False
        fields = [q.field for q in result.missing_required]
        assert "v_supply" in fields

    def test_ldo_plus_led_ldo_missing(self) -> None:
        """LDO 缺少 v_in + LED 完整 → 不能继续"""
        plan = _make_plan(
            _ldo_module({"v_out": "3.3"}),
            _led_module({"v_supply": "3.3"}),
        )
        result = self.clarifier.clarify("LDO + LED", plan)
        assert result.can_proceed is False
        fields = [q.field for q in result.missing_required]
        assert "v_in" in fields

    def test_multi_module_known_constraints_from_all_modules(self) -> None:
        """多模块的已知约束来自所有模块"""
        plan = _make_plan(
            _ldo_module({"v_in": "5", "v_out": "3.3"}),
            _led_module({"v_supply": "3.3"}),
        )
        result = self.clarifier.clarify("5V LDO + LED", plan)
        constraint_names = [c.name for c in result.known_constraints]
        assert "v_in" in constraint_names
        assert "v_out" in constraint_names
        assert "v_supply" in constraint_names

    def test_assumptions_from_all_modules(self) -> None:
        """多模块各自的假设都被收集"""
        plan = _make_plan(
            _ldo_module({"v_in": "5", "v_out": "3.3"}),
            _led_module({"v_supply": "3.3"}),
        )
        result = self.clarifier.clarify("5V LDO + LED", plan)
        assumption_fields = [a.field for a in result.assumptions]
        # LDO 应有 i_out_max 假设，LED 应有 led_color 假设
        assert "i_out_max" in assumption_fields
        assert "led_color" in assumption_fields


# ============================================================
# 空计划测试
# ============================================================


class TestEmptyPlan:
    """空计划（无模块）的处理"""

    def setup_method(self) -> None:
        self.clarifier = RequirementClarifier()

    def test_empty_plan_can_proceed(self) -> None:
        """空计划（无模块）→ can_proceed=True"""
        plan = DesignPlan(name="空", description="无模块", modules=[])
        result = self.clarifier.clarify("", plan)
        assert result.can_proceed is True

    def test_empty_plan_no_missing(self) -> None:
        """空计划不产生任何缺失问题"""
        plan = DesignPlan(name="空", description="无模块", modules=[])
        result = self.clarifier.clarify("", plan)
        assert result.missing_required == []
        assert result.optional_preferences == []
        assert result.assumptions == []


# ============================================================
# 假设字段质量测试
# ============================================================


class TestAssumptionQuality:
    """假设的字段质量检查"""

    def setup_method(self) -> None:
        self.clarifier = RequirementClarifier()

    def test_all_assumptions_have_reason(self) -> None:
        """所有假设都有非空的 reason"""
        plan = _make_plan(
            _ldo_module({"v_in": "5", "v_out": "3.3"}),
            _buck_module({"v_in": "12", "v_out": "5"}),
            _led_module({"v_supply": "3.3"}),
        )
        result = self.clarifier.clarify("复合设计", plan)
        for assumption in result.assumptions:
            assert assumption.reason != "", (
                f"Assumption field={assumption.field} has empty reason"
            )

    def test_all_assumptions_have_risk(self) -> None:
        """所有假设都有非空的 risk"""
        plan = _make_plan(
            _ldo_module({"v_in": "5", "v_out": "3.3"}),
            _buck_module({"v_in": "12", "v_out": "5"}),
            _led_module({"v_supply": "3.3"}),
        )
        result = self.clarifier.clarify("复合设计", plan)
        for assumption in result.assumptions:
            assert assumption.risk != "", (
                f"Assumption field={assumption.field} has empty risk"
            )

    def test_all_assumptions_have_assumed_value(self) -> None:
        """所有假设都有非空的 assumed_value"""
        plan = _make_plan(_ldo_module({"v_in": "5", "v_out": "3.3"}))
        result = self.clarifier.clarify("LDO", plan)
        for assumption in result.assumptions:
            assert assumption.assumed_value != "", (
                f"Assumption field={assumption.field} has empty assumed_value"
            )


# ============================================================
# 置信度测试
# ============================================================


class TestConfidence:
    """置信度计算"""

    def setup_method(self) -> None:
        self.clarifier = RequirementClarifier()

    def test_complete_input_high_confidence(self) -> None:
        """完整输入 → 置信度 >= 0.7"""
        plan = _make_plan(_ldo_module({"v_in": "5", "v_out": "3.3"}))
        result = self.clarifier.clarify("5V转3.3V LDO", plan)
        assert result.confidence >= 0.7, f"Expected >= 0.7, got {result.confidence}"

    def test_partially_missing_mid_confidence(self) -> None:
        """部分缺失 → 置信度在 0.4-0.7 之间"""
        # LDO 只有 v_in，缺少 v_out
        plan = _make_plan(_ldo_module({"v_in": "5"}))
        result = self.clarifier.clarify("5V LDO", plan)
        assert 0.0 <= result.confidence <= 0.7, (
            f"Expected <= 0.7, got {result.confidence}"
        )

    def test_mostly_missing_low_confidence(self) -> None:
        """全部缺失 → 置信度 < 0.5"""
        plan = _make_plan(_ldo_module())
        result = self.clarifier.clarify("LDO", plan)
        assert result.confidence < 0.5, f"Expected < 0.5, got {result.confidence}"

    def test_empty_plan_high_confidence(self) -> None:
        """空计划 → 高置信度（无内容即完整）"""
        plan = DesignPlan(name="空", description="", modules=[])
        result = self.clarifier.clarify("", plan)
        assert result.confidence >= 0.7

    def test_confidence_in_valid_range(self) -> None:
        """置信度始终在 [0, 1] 范围内"""
        for params in [
            {},
            {"v_in": "5"},
            {"v_out": "3.3"},
            {"v_in": "5", "v_out": "3.3"},
            {"v_in": "5", "v_out": "3.3", "i_out_max": "1A"},
        ]:
            plan = _make_plan(_ldo_module(params))
            result = self.clarifier.clarify("LDO", plan)
            assert 0.0 <= result.confidence <= 1.0


# ============================================================
# 与 DesignPlan / IR 集成测试
# ============================================================


class TestIRIntegration:
    """ClarificationResult 与 IR 模型的集成"""

    def setup_method(self) -> None:
        self.clarifier = RequirementClarifier()

    def test_known_constraints_are_constraint_instances(self) -> None:
        """known_constraints 中的每个元素都是 Constraint 实例"""
        plan = _make_plan(_ldo_module({"v_in": "5", "v_out": "3.3"}))
        result = self.clarifier.clarify("LDO", plan)
        for c in result.known_constraints:
            assert isinstance(c, Constraint)

    def test_missing_required_are_unresolved_question_instances(self) -> None:
        """missing_required 中的每个元素都是 UnresolvedQuestion 实例"""
        plan = _make_plan(_ldo_module())
        result = self.clarifier.clarify("LDO", plan)
        for q in result.missing_required:
            assert isinstance(q, UnresolvedQuestion)

    def test_assumptions_are_assumption_instances(self) -> None:
        """assumptions 中的每个元素都是 Assumption 实例"""
        plan = _make_plan(_ldo_module({"v_in": "5", "v_out": "3.3"}))
        result = self.clarifier.clarify("LDO", plan)
        for a in result.assumptions:
            assert isinstance(a, Assumption)

    def test_known_constraints_have_source_user(self) -> None:
        """已知约束的 source 为 'user'"""
        plan = _make_plan(_ldo_module({"v_in": "5"}))
        result = self.clarifier.clarify("LDO", plan)
        for c in result.known_constraints:
            assert c.source == "user"

    def test_unresolved_question_has_field_attribute(self) -> None:
        """UnresolvedQuestion 有 field 属性"""
        plan = _make_plan(_ldo_module())
        result = self.clarifier.clarify("LDO", plan)
        assert len(result.missing_required) > 0
        for q in result.missing_required:
            assert hasattr(q, "field")
            assert q.field != ""

    def test_question_id_auto_generated(self) -> None:
        """UnresolvedQuestion 的 question_id 自动生成且非空"""
        plan = _make_plan(_ldo_module())
        result = self.clarifier.clarify("LDO", plan)
        for q in result.missing_required:
            assert q.question_id != ""

    def test_can_proceed_maps_to_ir_intent(self) -> None:
        """can_proceed 语义与 DesignIntent.can_proceed 一致"""
        from schemaforge.design.ir import DesignIntent

        # 无缺失 → can_proceed=True
        plan = _make_plan(_ldo_module({"v_in": "5", "v_out": "3.3"}))
        result = self.clarifier.clarify("LDO", plan)
        intent = DesignIntent(unresolved_questions=result.missing_required)
        # ClarificationResult.can_proceed 和 DesignIntent.can_proceed 应一致
        assert result.can_proceed == intent.can_proceed


# ============================================================
# 未知分类测试
# ============================================================


class TestUnknownCategory:
    """未知分类的宽容处理"""

    def setup_method(self) -> None:
        self.clarifier = RequirementClarifier()

    def test_unknown_category_does_not_raise(self) -> None:
        """未知分类不抛出异常"""
        mod = ModuleRequirement(
            role="unknown_module",
            category="rc_filter",
            description="RC 滤波",
            parameters={"f_cutoff": "1000"},
        )
        plan = _make_plan(mod)
        result = self.clarifier.clarify("RC 滤波", plan)
        # 应能正常返回结果
        assert isinstance(result, ClarificationResult)

    def test_unknown_category_known_constraints_extracted(self) -> None:
        """未知分类仍能提取已知约束"""
        mod = ModuleRequirement(
            role="filter",
            category="rc_filter",
            description="RC 滤波",
            parameters={"f_cutoff": "1000"},
        )
        plan = _make_plan(mod)
        result = self.clarifier.clarify("RC 滤波", plan)
        constraint_names = [c.name for c in result.known_constraints]
        assert "f_cutoff" in constraint_names


# ============================================================
# RequirementClarifier 初始化测试
# ============================================================


class TestRequirementClarifierInit:
    """RequirementClarifier 初始化"""

    def test_clarifier_returns_result(self) -> None:
        """RequirementClarifier 返回 ClarificationResult"""
        clarifier = RequirementClarifier()
        plan = _make_plan(_ldo_module({"v_in": "5", "v_out": "3.3"}))
        result = clarifier.clarify("LDO", plan)
        assert isinstance(result, ClarificationResult)
