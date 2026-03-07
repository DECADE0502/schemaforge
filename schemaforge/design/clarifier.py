"""需求澄清器 (Requirement Clarifier)

检测缺失的设计约束，区分必要参数和可选参数，
生成带风险说明的结构化假设，并产出用户在设计前必须回答的问题。

两种模式：
- Mock 模式（默认）: 基于规则的约束检测，适用于离线测试
- 可扩展模式: 预留 AI 接口（未来扩展）

支持的模块分类:
- ldo: 线性稳压器
- buck: 开关降压器
- led: LED 指示灯
- voltage_divider: 电压分压器

用法::

    planner = DesignPlanner(use_mock=True)
    plan = planner.plan("5V转3.3V稳压电路，带LED指示灯")

    clarifier = RequirementClarifier(use_mock=True)
    result = clarifier.clarify("5V转3.3V稳压电路，带LED指示灯", plan)

    if result.can_proceed:
        print("需求完整，可以继续设计")
    else:
        for q in result.missing_required:
            print(f"必须回答: {q.question}")
"""

from __future__ import annotations

from dataclasses import dataclass, field

from schemaforge.design.ir import (
    Assumption,
    Constraint,
    ConstraintPriority,
    UnresolvedQuestion,
)
from schemaforge.design.planner import DesignPlan, ModuleRequirement


# ============================================================
# 澄清结果
# ============================================================


@dataclass
class ClarificationResult:
    """需求澄清结果

    包含已知约束、缺失必要约束的问题、可选偏好、系统假设、
    置信度以及是否可以继续设计的判断。
    """

    known_constraints: list[Constraint] = field(default_factory=list)
    """用户明确提供的约束"""

    missing_required: list[UnresolvedQuestion] = field(default_factory=list)
    """缺失的必要约束（阻断设计）"""

    optional_preferences: list[UnresolvedQuestion] = field(default_factory=list)
    """可选/偏好约束（可以使用默认值）"""

    assumptions: list[Assumption] = field(default_factory=list)
    """系统为缺失可选约束自动填充的假设"""

    confidence: float = 0.5
    """需求完整度置信度 (0-1)"""

    @property
    def can_proceed(self) -> bool:
        """是否可以继续设计（无未回答的必要问题）"""
        return len(self.missing_required) == 0

    @property
    def must_ask_count(self) -> int:
        """必须询问用户的问题数量"""
        return len(self.missing_required)


# ============================================================
# 分类规则定义
# ============================================================

# ldo: 必要字段
_LDO_REQUIRED = ("v_in", "v_out")
# ldo: 可选字段
_LDO_OPTIONAL = ("i_out_max", "v_dropout", "efficiency_preference")

# buck: 必要字段
_BUCK_REQUIRED = ("v_in", "v_out")
# buck: 可选字段
_BUCK_OPTIONAL = ("i_out_max", "fsw", "efficiency_target")

# led: 必要字段
_LED_REQUIRED = ("v_supply",)
# led: 可选字段
_LED_OPTIONAL = ("led_color", "led_current", "brightness_preference")

# voltage_divider: 必要字段
_DIVIDER_REQUIRED = ("v_in", "v_out")
# voltage_divider: 可选字段
_DIVIDER_OPTIONAL = ("divider_current", "precision")


def _make_ldo_missing_question(field_name: str) -> UnresolvedQuestion:
    """生成 LDO 缺失字段的问题"""
    if field_name == "v_in":
        return UnresolvedQuestion(
            field="v_in",
            question="请提供输入电压范围",
            why_needed="LDO 选型需要知道输入电压以确认压差要求",
            priority=ConstraintPriority.REQUIRED,
        )
    if field_name == "v_out":
        return UnresolvedQuestion(
            field="v_out",
            question="请提供目标输出电压",
            why_needed="LDO 选型和分压电阻计算依赖目标输出电压",
            priority=ConstraintPriority.REQUIRED,
        )
    raise ValueError(f"未知 LDO 必要字段: {field_name}")


def _make_ldo_assumption(field_name: str) -> Assumption | None:
    """生成 LDO 可选字段的假设"""
    if field_name == "i_out_max":
        return Assumption(
            field="i_out_max",
            assumed_value="500mA",
            reason="未指定负载电流，采用中等负载默认值",
            risk="若实际负载超过500mA，器件选型可能不足",
            confidence=0.5,
        )
    if field_name == "v_dropout":
        return Assumption(
            field="v_dropout",
            assumed_value="auto",
            reason="由器件datasheet决定",
            risk="若压差要求严格，需核对器件规格",
            confidence=0.7,
        )
    if field_name == "efficiency_preference":
        return Assumption(
            field="efficiency_preference",
            assumed_value="standard",
            reason="未指定效率偏好，采用标准设计",
            risk="高功耗场景下可能需要更低压差器件",
            confidence=0.6,
        )
    return None


def _make_buck_assumption(field_name: str) -> Assumption | None:
    """生成 Buck 可选字段的假设"""
    if field_name == "i_out_max":
        return Assumption(
            field="i_out_max",
            assumed_value="1A",
            reason="开关电源默认中等负载",
            risk="若实际负载超过1A，电感和MOS管选型可能不足",
            confidence=0.5,
        )
    if field_name == "fsw":
        return Assumption(
            field="fsw",
            assumed_value="500kHz",
            reason="通用开关频率",
            risk="频率影响磁性元件选型，实际应用需根据效率和EMI要求调整",
            confidence=0.6,
        )
    if field_name == "efficiency_target":
        return Assumption(
            field="efficiency_target",
            assumed_value="85%",
            reason="典型 Buck 转换效率目标",
            risk="若效率要求更高，需选择更优器件或优化拓扑",
            confidence=0.6,
        )
    return None


def _make_led_missing_question(field_name: str) -> UnresolvedQuestion:
    """生成 LED 缺失字段的问题"""
    if field_name == "v_supply":
        return UnresolvedQuestion(
            field="v_supply",
            question="请提供LED供电电压",
            why_needed="LED 限流电阻计算依赖供电电压",
            priority=ConstraintPriority.REQUIRED,
        )
    raise ValueError(f"未知 LED 必要字段: {field_name}")


def _make_led_assumption(field_name: str) -> Assumption | None:
    """生成 LED 可选字段的假设"""
    if field_name == "led_color":
        return Assumption(
            field="led_color",
            assumed_value="green",
            reason="默认绿色指示灯",
            risk="不同颜色 LED 正向电压不同（红~2V，绿~2.2V，蓝~3.2V），影响限流电阻计算",
            confidence=0.5,
        )
    if field_name == "led_current":
        return Assumption(
            field="led_current",
            assumed_value="10mA",
            reason="常规指示灯电流",
            risk="若亮度要求不同，需调整电流和限流电阻",
            confidence=0.7,
        )
    if field_name == "brightness_preference":
        return Assumption(
            field="brightness_preference",
            assumed_value="normal",
            reason="默认普通亮度",
            risk="高亮或低功耗需求需调整电流设计",
            confidence=0.6,
        )
    return None


def _make_divider_missing_question(field_name: str) -> UnresolvedQuestion:
    """生成分压器缺失字段的问题"""
    if field_name == "v_in":
        return UnresolvedQuestion(
            field="v_in",
            question="请提供分压器输入电压",
            why_needed="分压器电阻计算需要知道输入电压",
            priority=ConstraintPriority.REQUIRED,
        )
    if field_name == "v_out":
        return UnresolvedQuestion(
            field="v_out",
            question="请提供分压输出目标电压",
            why_needed="分压比由输入电压和目标输出电压共同决定",
            priority=ConstraintPriority.REQUIRED,
        )
    raise ValueError(f"未知分压器必要字段: {field_name}")


def _make_divider_assumption(field_name: str) -> Assumption | None:
    """生成分压器可选字段的假设"""
    if field_name == "divider_current":
        return Assumption(
            field="divider_current",
            assumed_value="1mA",
            reason="分压器典型工作电流",
            risk="电流过小影响精度，过大增加功耗",
            confidence=0.6,
        )
    if field_name == "precision":
        return Assumption(
            field="precision",
            assumed_value="1%",
            reason="常规精度要求",
            risk="高精度应用需使用更精密电阻",
            confidence=0.6,
        )
    return None


# ============================================================
# 需求澄清器
# ============================================================


class RequirementClarifier:
    """需求澄清器

    检测规划结果中的缺失约束，区分必要参数和可选参数，
    生成假设和需要用户回答的问题。

    Args:
        use_mock: True 使用规则模式（默认），False 预留给未来 AI 模式
    """

    def __init__(self, use_mock: bool = True) -> None:
        self.use_mock = use_mock

    def clarify(self, user_input: str, plan: DesignPlan) -> ClarificationResult:
        """分析设计规划，产出澄清结果

        Args:
            user_input: 用户原始需求文本
            plan: 规划器产出的 DesignPlan

        Returns:
            ClarificationResult 澄清结果
        """
        if self.use_mock:
            return self._clarify_mock(user_input, plan)
        return self._clarify_ai(user_input, plan)

    # ----------------------------------------------------------
    # AI 增强模式
    # ----------------------------------------------------------

    def _clarify_ai(self, user_input: str, plan: DesignPlan) -> ClarificationResult:
        """利用 AI 增强需求澄清。

        先运行基于规则的 mock 分析，再让 AI 检查是否有遗漏的约束
        或隐含的设计意图。AI 失败时降级到纯规则结果。
        """
        # 1. 先获取规则基线结果
        base_result = self._clarify_mock(user_input, plan)

        # 2. 构建 AI prompt 进行增强检查
        try:
            from schemaforge.ai.client import call_llm_json

            modules_desc = "\n".join(
                f"  - {m.category}（参数: {m.parameters}）"
                for m in plan.modules
            )
            missing_desc = "\n".join(
                f"  - {q.question}（字段: {q.field}）"
                for q in base_result.missing_required
            ) or "  无"
            assumptions_desc = "\n".join(
                f"  - {a.field} = {a.assumed_value}（{a.reason}）"
                for a in base_result.assumptions
            ) or "  无"

            system_prompt = (
                "你是电路设计需求分析助手。分析用户需求和初步规划，"
                "检查是否有遗漏的重要约束或隐含的设计意图。\n\n"
                "输出严格 JSON：\n"
                '{"additional_questions": [{"field": "字段名", "question": "问题"}], '
                '"additional_assumptions": [{"field": "字段名", "value": "假设值", "reason": "原因"}], '
                '"confidence_adjustment": 0.0}\n'
                "如果没有额外发现，返回空列表和 0 调整。"
            )

            user_msg = (
                f"用户需求: {user_input}\n\n"
                f"规划模块:\n{modules_desc}\n\n"
                f"已识别缺失约束:\n{missing_desc}\n\n"
                f"已有假设:\n{assumptions_desc}\n\n"
                "请检查是否有遗漏。"
            )

            ai_data = call_llm_json(
                system_prompt=system_prompt,
                user_message=user_msg,
                temperature=0.2,
                max_retries=2,
            )

            if ai_data is None:
                return base_result

            # 3. 合并 AI 发现到基线结果
            for q_data in ai_data.get("additional_questions", []):
                ai_field = str(q_data.get("field", ""))
                question_text = str(q_data.get("question", ""))
                if ai_field and question_text:
                    # 避免重复
                    if not any(
                        q.field == ai_field
                        for q in base_result.optional_preferences
                    ):
                        base_result.optional_preferences.append(
                            UnresolvedQuestion(
                                field=ai_field,
                                question=question_text,
                                priority=ConstraintPriority.OPTIONAL,
                                default_if_skipped="",
                            )
                        )

            for a_data in ai_data.get("additional_assumptions", []):
                a_field = str(a_data.get("field", ""))
                a_value = str(a_data.get("value", ""))
                a_reason = str(a_data.get("reason", ""))
                if a_field and a_value:
                    if not any(a.field == a_field for a in base_result.assumptions):
                        base_result.assumptions.append(
                            Assumption(
                                field=a_field,
                                assumed_value=a_value,
                                reason=a_reason or "AI 推断",
                                confidence=0.6,
                            )
                        )

            # 4. 微调置信度
            adj = ai_data.get("confidence_adjustment", 0.0)
            if isinstance(adj, (int, float)):
                base_result.confidence = max(
                    0.0, min(1.0, base_result.confidence + float(adj))
                )

        except Exception:
            # AI 失败，降级到纯规则结果
            pass

        return base_result

    # ----------------------------------------------------------
    # Mock（规则）模式
    # ----------------------------------------------------------

    def _clarify_mock(self, user_input: str, plan: DesignPlan) -> ClarificationResult:
        """基于规则的澄清逻辑"""
        known_constraints: list[Constraint] = []
        missing_required: list[UnresolvedQuestion] = []
        optional_preferences: list[UnresolvedQuestion] = []
        assumptions: list[Assumption] = []

        for mod in plan.modules:
            # 1. 提取已知约束
            for param_name, param_value in mod.parameters.items():
                known_constraints.append(
                    Constraint(
                        name=param_name,
                        value=param_value,
                        priority=ConstraintPriority.REQUIRED,
                        source="user",
                        confidence=1.0,
                    )
                )

            # 2. 按分类检查缺失约束
            category = mod.category.lower()
            if category == "ldo":
                self._check_ldo(
                    mod, missing_required, optional_preferences, assumptions
                )
            elif category == "buck":
                self._check_buck(
                    mod, missing_required, optional_preferences, assumptions
                )
            elif category == "led":
                self._check_led(
                    mod, missing_required, optional_preferences, assumptions
                )
            elif category == "voltage_divider":
                self._check_divider(
                    mod, missing_required, optional_preferences, assumptions
                )
            # 其他分类不做强制检查

        # 3. 计算置信度
        confidence = self._calc_confidence(plan, missing_required, optional_preferences)

        return ClarificationResult(
            known_constraints=known_constraints,
            missing_required=missing_required,
            optional_preferences=optional_preferences,
            assumptions=assumptions,
            confidence=confidence,
        )

    def _check_ldo(
        self,
        mod: ModuleRequirement,
        missing_required: list[UnresolvedQuestion],
        optional_preferences: list[UnresolvedQuestion],
        assumptions: list[Assumption],
    ) -> None:
        """检查 LDO 模块的约束"""
        params = mod.parameters

        # 必要字段
        for req_field in _LDO_REQUIRED:
            if req_field not in params or not params[req_field]:
                missing_required.append(_make_ldo_missing_question(req_field))

        # 可选字段
        for opt_field in _LDO_OPTIONAL:
            if opt_field not in params or not params[opt_field]:
                assumption = _make_ldo_assumption(opt_field)
                if assumption is not None:
                    assumptions.append(assumption)
                    optional_preferences.append(
                        UnresolvedQuestion(
                            field=opt_field,
                            question=f"请提供 {opt_field}（可选，已使用默认值 {assumption.assumed_value}）",
                            default_if_skipped=assumption.assumed_value,
                            priority=ConstraintPriority.OPTIONAL,
                        )
                    )

    def _check_buck(
        self,
        mod: ModuleRequirement,
        missing_required: list[UnresolvedQuestion],
        optional_preferences: list[UnresolvedQuestion],
        assumptions: list[Assumption],
    ) -> None:
        """检查 Buck 模块的约束"""
        params = mod.parameters

        # 必要字段（与 LDO 相同）
        for req_field in _BUCK_REQUIRED:
            if req_field not in params or not params[req_field]:
                missing_required.append(_make_ldo_missing_question(req_field))

        # 可选字段
        for opt_field in _BUCK_OPTIONAL:
            if opt_field not in params or not params[opt_field]:
                assumption = _make_buck_assumption(opt_field)
                if assumption is not None:
                    assumptions.append(assumption)
                    optional_preferences.append(
                        UnresolvedQuestion(
                            field=opt_field,
                            question=f"请提供 {opt_field}（可选，已使用默认值 {assumption.assumed_value}）",
                            default_if_skipped=assumption.assumed_value,
                            priority=ConstraintPriority.OPTIONAL,
                        )
                    )

    def _check_led(
        self,
        mod: ModuleRequirement,
        missing_required: list[UnresolvedQuestion],
        optional_preferences: list[UnresolvedQuestion],
        assumptions: list[Assumption],
    ) -> None:
        """检查 LED 模块的约束"""
        params = mod.parameters

        # 必要字段
        for req_field in _LED_REQUIRED:
            if req_field not in params or not params[req_field]:
                missing_required.append(_make_led_missing_question(req_field))

        # 可选字段
        for opt_field in _LED_OPTIONAL:
            if opt_field not in params or not params[opt_field]:
                assumption = _make_led_assumption(opt_field)
                if assumption is not None:
                    assumptions.append(assumption)
                    optional_preferences.append(
                        UnresolvedQuestion(
                            field=opt_field,
                            question=f"请提供 {opt_field}（可选，已使用默认值 {assumption.assumed_value}）",
                            default_if_skipped=assumption.assumed_value,
                            priority=ConstraintPriority.OPTIONAL,
                        )
                    )

    def _check_divider(
        self,
        mod: ModuleRequirement,
        missing_required: list[UnresolvedQuestion],
        optional_preferences: list[UnresolvedQuestion],
        assumptions: list[Assumption],
    ) -> None:
        """检查电压分压器模块的约束"""
        params = mod.parameters

        # 必要字段
        for req_field in _DIVIDER_REQUIRED:
            if req_field not in params or not params[req_field]:
                missing_required.append(_make_divider_missing_question(req_field))

        # 可选字段
        for opt_field in _DIVIDER_OPTIONAL:
            if opt_field not in params or not params[opt_field]:
                assumption = _make_divider_assumption(opt_field)
                if assumption is not None:
                    assumptions.append(assumption)
                    optional_preferences.append(
                        UnresolvedQuestion(
                            field=opt_field,
                            question=f"请提供 {opt_field}（可选，已使用默认值 {assumption.assumed_value}）",
                            default_if_skipped=assumption.assumed_value,
                            priority=ConstraintPriority.OPTIONAL,
                        )
                    )

    def _calc_confidence(
        self,
        plan: DesignPlan,
        missing_required: list[UnresolvedQuestion],
        optional_preferences: list[UnresolvedQuestion],
    ) -> float:
        """计算需求完整度置信度

        置信度算法：
        - 无模块或无缺失 → 高置信度 (0.9)
        - 有必要约束缺失 → 依缺失比例降低
        - 有可选约束缺失 → 轻微降低

        Returns:
            0-1 的置信度值
        """
        if not plan.modules:
            return 0.9  # 无模块，视为完整

        # 统计总字段数（按分类）
        total_required = 0
        total_optional = 0
        for mod in plan.modules:
            category = mod.category.lower()
            if category == "ldo":
                total_required += len(_LDO_REQUIRED)
                total_optional += len(_LDO_OPTIONAL)
            elif category == "buck":
                total_required += len(_BUCK_REQUIRED)
                total_optional += len(_BUCK_OPTIONAL)
            elif category == "led":
                total_required += len(_LED_REQUIRED)
                total_optional += len(_LED_OPTIONAL)
            elif category == "voltage_divider":
                total_required += len(_DIVIDER_REQUIRED)
                total_optional += len(_DIVIDER_OPTIONAL)
            else:
                # 其他分类：假设有 2 个字段
                total_required += 2

        if total_required == 0 and total_optional == 0:
            return 0.9

        n_missing_req = len(missing_required)
        n_missing_opt = len(optional_preferences)

        # 必要字段缺失权重 0.7，可选字段缺失权重 0.3
        req_score = 1.0 - (n_missing_req / max(total_required, 1)) * 0.7
        opt_score = 1.0 - (n_missing_opt / max(total_optional, 1)) * 0.3

        confidence = req_score * opt_score

        # 限制在 [0.05, 0.95]
        return max(0.05, min(0.95, confidence))
