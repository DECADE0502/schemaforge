"""SchemaForge 设计审查引擎

工程级审查层，在基础合理性检查之上提供深度审查：
- 阻断问题（BLOCKING）：必须修复才能出图
- 警告（WARNING）：建议修复，可继续
- 建议（RECOMMENDATION）：改进建议
- 布局注意事项（LAYOUT_NOTE）：PCB布局相关
- 调试注意事项（BRINGUP_NOTE）：上电调试相关

审查规则涵盖：
- LDO：压差余量、热耗散、电容配置、最大输入电压
- LED：电流、电阻功耗、供电电压
- 分压器：分压电流、负载阻抗
- 通用：去耦电容、热降额
- 模块间：功率预算、接地路径

用法::

    engine = DesignReviewEngine()

    module = ModuleReviewInput(
        role="main_ldo",
        category="ldo",
        device=device_model,
        parameters={"v_in": "5V", "v_out": "3.3V", "i_out": "0.5A"},
    )

    result = engine.review_module(module)
    # result.issues — 所有发现的问题
    # result.passed — 是否通过（无BLOCKING）
    # result.has_blocking — 是否有阻断问题

    full_review = engine.review_design([module1, module2])
    # full_review.issues — 所有模块+跨模块问题
    # full_review.overall_passed — 整体是否通过
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

from schemaforge.design.ir import (
    DesignReview,
    IssueCategory,
    ModuleReview,
    ReviewIssue,
    ReviewSeverity,
)
from schemaforge.library.models import DeviceModel


# ============================================================
# 输入模型
# ============================================================


@dataclass
class ModuleReviewInput:
    """审查引擎的输入"""

    role: str  # 模块角色
    category: str  # 模块类别（ldo, led, voltage_divider, 等）
    device: DeviceModel  # 器件模型
    parameters: dict[str, str] = field(default_factory=dict)  # 设计参数


# ============================================================
# 审查引擎
# ============================================================


class DesignReviewEngine:
    """设计审查引擎

    提供工程级审查，超越基础合理性检查。
    每个规则产生 ReviewIssue，使用 IR 中定义的 ReviewSeverity 和 IssueCategory。
    """

    def __init__(self) -> None:
        pass

    def review_module(self, module: ModuleReviewInput) -> ModuleReview:
        """审查单个模块，返回模块级审查报告

        Args:
            module: 模块审查输入

        Returns:
            ModuleReview — 包含所有发现的问题及通过/失败状态
        """
        issues: list[ReviewIssue] = []

        category = module.category.lower()

        # 按类别执行专属规则
        if category == "ldo":
            issues.extend(self._review_ldo(module))
        elif category in ("led", "led_indicator"):
            issues.extend(self._review_led(module))
        elif category in ("voltage_divider", "divider"):
            issues.extend(self._review_voltage_divider(module))

        # 通用规则（所有模块）
        issues.extend(self._review_general(module))

        # 布局注意事项
        issues.extend(self._review_layout(module))

        # 调试注意事项
        issues.extend(self._review_bringup(module))

        # 设置 module_role
        for issue in issues:
            if not issue.module_role:
                issue.module_role = module.role

        # 判断是否通过
        has_blocking = any(i.severity == ReviewSeverity.BLOCKING for i in issues)
        return ModuleReview(
            issues=issues,
            passed=not has_blocking,
        )

    def review_design(self, modules: list[ModuleReviewInput]) -> DesignReview:
        """审查整个设计，返回全局审查报告

        对每个模块执行模块级审查，再执行跨模块检查。

        Args:
            modules: 模块审查输入列表

        Returns:
            DesignReview — 包含所有模块+跨模块问题及整体通过状态
        """
        all_issues: list[ReviewIssue] = []

        # 各模块审查
        for module in modules:
            module_review = self.review_module(module)
            all_issues.extend(module_review.issues)

        # 跨模块检查
        if modules:
            all_issues.extend(self._review_cross_module(modules))

        has_blocking = any(i.severity == ReviewSeverity.BLOCKING for i in all_issues)
        return DesignReview(
            issues=all_issues,
            overall_passed=not has_blocking,
            reviewed_at=datetime.now().isoformat(),
        )

    # ============================================================
    # LDO 规则
    # ============================================================

    def _review_ldo(self, module: ModuleReviewInput) -> list[ReviewIssue]:
        """LDO 专属审查规则"""
        issues: list[ReviewIssue] = []
        params = module.parameters
        device = module.device

        v_in = _parse_numeric(params.get("v_in", ""))
        v_out = _parse_numeric(params.get("v_out", ""))
        i_out = _parse_numeric(params.get("i_out", ""))
        v_dropout = _parse_numeric(device.specs.get("v_dropout", ""))
        v_in_max = _parse_numeric(device.specs.get("v_in_max", ""))

        # --- ldo_dropout_margin ---
        if v_in is not None and v_out is not None and v_dropout is not None:
            actual_dropout = v_in - v_out
            if actual_dropout < v_dropout:
                issues.append(
                    ReviewIssue(
                        severity=ReviewSeverity.BLOCKING,
                        category=IssueCategory.ELECTRICAL,
                        rule_id="ldo_dropout_margin",
                        message=(
                            f"输入输出压差不足：实际压差 {actual_dropout:.2f}V "
                            f"< 最小压差 {v_dropout:.2f}V，LDO 无法正常稳压"
                        ),
                        suggestion=(
                            f"将输入电压提高至 {v_out + v_dropout:.2f}V 以上，"
                            f"或选择更低压差的 LDO"
                        ),
                        evidence=(
                            f"v_in={v_in:.2f}V, v_out={v_out:.2f}V, "
                            f"actual_dropout={actual_dropout:.2f}V, "
                            f"required_dropout={v_dropout:.2f}V"
                        ),
                    )
                )

        # --- ldo_thermal_dissipation ---
        if v_in is not None and v_out is not None and i_out is not None:
            power = (v_in - v_out) * i_out
            if power > 1.0:
                issues.append(
                    ReviewIssue(
                        severity=ReviewSeverity.BLOCKING,
                        category=IssueCategory.THERMAL,
                        rule_id="ldo_thermal_dissipation",
                        message=(
                            f"LDO 热耗散过高：{power:.2f}W，超过 1W 阈值，"
                            f"可能导致器件过热损坏"
                        ),
                        suggestion="降低输入电压、减小负载电流，或改用 Buck 降压提高效率",
                        evidence=(
                            f"P = (v_in - v_out) × i_out = "
                            f"({v_in:.2f} - {v_out:.2f}) × {i_out:.3f} = {power:.2f}W"
                        ),
                    )
                )
            elif power > 0.5:
                issues.append(
                    ReviewIssue(
                        severity=ReviewSeverity.WARNING,
                        category=IssueCategory.THERMAL,
                        rule_id="ldo_thermal_dissipation",
                        message=(
                            f"LDO 热耗散偏高：{power:.2f}W，超过 0.5W，需要注意散热设计"
                        ),
                        suggestion="确保 PCB 有足够散热铜皮，考虑加装散热片或改用效率更高的拓扑",
                        evidence=(
                            f"P = (v_in - v_out) × i_out = "
                            f"({v_in:.2f} - {v_out:.2f}) × {i_out:.3f} = {power:.2f}W"
                        ),
                    )
                )

        # --- ldo_max_vin_exceeded ---
        if v_in is not None and v_in_max is not None:
            if v_in > v_in_max:
                issues.append(
                    ReviewIssue(
                        severity=ReviewSeverity.BLOCKING,
                        category=IssueCategory.ELECTRICAL,
                        rule_id="ldo_max_vin_exceeded",
                        message=(
                            f"输入电压 {v_in:.2f}V 超过器件最大额定输入电压 {v_in_max:.2f}V，"
                            f"可能损坏器件"
                        ),
                        suggestion=(
                            f"降低输入电压至 {v_in_max:.2f}V 以下，或更换耐压更高的 LDO"
                        ),
                        evidence=f"v_in={v_in:.2f}V, spec v_in_max={v_in_max:.2f}V",
                    )
                )

        # --- ldo_input_cap_present ---
        issues.extend(
            self._check_external_component(
                module=module,
                comp_role="input_cap",
                rule_id="ldo_input_cap_present",
                severity=ReviewSeverity.WARNING,
                category=IssueCategory.COMPLETENESS,
                message="LDO 输入端缺少旁路电容，可能影响稳定性",
                suggestion="在 LDO 输入端添加 10μF 以上电容",
            )
        )

        # --- ldo_output_cap_present ---
        issues.extend(
            self._check_external_component(
                module=module,
                comp_role="output_cap",
                rule_id="ldo_output_cap_present",
                severity=ReviewSeverity.WARNING,
                category=IssueCategory.COMPLETENESS,
                message="LDO 输出端缺少滤波电容，可能导致输出不稳定",
                suggestion="在 LDO 输出端添加 22μF 以上电容",
            )
        )

        # --- ldo_output_cap_esr ---
        # 总是给出 ESR 建议（只要有输出电容拓扑定义）
        if module.device.topology is not None:
            has_output_cap = any(
                c.role == "output_cap"
                for c in module.device.topology.external_components
            )
            if has_output_cap:
                issues.append(
                    ReviewIssue(
                        severity=ReviewSeverity.RECOMMENDATION,
                        category=IssueCategory.STABILITY,
                        rule_id="ldo_output_cap_esr",
                        message="建议输出电容选用低ESR型号",
                        suggestion="选用 ESR < 100mΩ 的低ESR电容（如钽电容或聚合物铝电解电容），避免振荡",
                    )
                )

        return issues

    # ============================================================
    # LED 规则
    # ============================================================

    def _review_led(self, module: ModuleReviewInput) -> list[ReviewIssue]:
        """LED 专属审查规则"""
        issues: list[ReviewIssue] = []
        params = module.parameters

        i_led = _parse_numeric(params.get("led_current", params.get("i_led", "")))
        v_supply = _parse_numeric(params.get("v_supply", params.get("v_in", "")))
        v_f = _parse_numeric(params.get("led_vf", params.get("v_f", "")))
        r_limit = _parse_numeric(params.get("r_limit", params.get("r_series", "")))

        # --- led_supply_too_low ---
        if v_supply is not None and v_f is not None:
            if v_supply <= v_f:
                issues.append(
                    ReviewIssue(
                        severity=ReviewSeverity.BLOCKING,
                        category=IssueCategory.ELECTRICAL,
                        rule_id="led_supply_too_low",
                        message=(
                            f"供电电压不足以驱动LED：v_supply={v_supply:.2f}V ≤ "
                            f"LED正向电压 v_f={v_f:.2f}V"
                        ),
                        suggestion=f"将供电电压提高至 {v_f + 0.5:.1f}V 以上（需留足够余量）",
                        evidence=f"v_supply={v_supply:.2f}V, led_vf={v_f:.2f}V",
                    )
                )

        # --- led_current_excessive ---
        if i_led is not None:
            if i_led > 0.020:  # 20mA
                issues.append(
                    ReviewIssue(
                        severity=ReviewSeverity.WARNING,
                        category=IssueCategory.ELECTRICAL,
                        rule_id="led_current_excessive",
                        message=(
                            f"LED电流超出常规指示灯范围：{i_led * 1000:.1f}mA > 20mA，"
                            f"请确认LED额定电流"
                        ),
                        suggestion="常规指示灯 LED 电流建议 1~20mA，大电流应用请确认 LED 热设计",
                        evidence=f"led_current={i_led * 1000:.1f}mA",
                    )
                )

        # --- led_resistor_power ---
        # 计算限流电阻功耗
        if i_led is not None:
            if r_limit is not None:
                r_power = i_led * i_led * r_limit
            elif v_supply is not None and v_f is not None:
                v_r = v_supply - v_f
                r_power = v_r * i_led if v_r > 0 else 0.0
            else:
                r_power = None

            if r_power is not None and r_power > 0.125:
                issues.append(
                    ReviewIssue(
                        severity=ReviewSeverity.WARNING,
                        category=IssueCategory.THERMAL,
                        rule_id="led_resistor_power",
                        message=(
                            f"限流电阻功耗偏大：{r_power * 1000:.1f}mW > 125mW，"
                            f"需使用额定功率足够的电阻"
                        ),
                        suggestion="选用额定功率 ≥ 2× 实际功耗的电阻，建议使用 0.25W 或以上规格",
                        evidence=f"P_resistor={r_power * 1000:.1f}mW",
                    )
                )

        return issues

    # ============================================================
    # 分压器规则
    # ============================================================

    def _review_voltage_divider(self, module: ModuleReviewInput) -> list[ReviewIssue]:
        """电压分压器专属审查规则"""
        issues: list[ReviewIssue] = []
        params = module.parameters

        v_in = _parse_numeric(params.get("v_in", ""))
        r_top = _parse_numeric(params.get("r_top", params.get("r1", "")))
        r_bot = _parse_numeric(
            params.get("r_bot", params.get("r_bottom", params.get("r2", "")))
        )

        # --- divider_current_excessive ---
        if v_in is not None and r_top is not None and r_bot is not None:
            r_total = r_top + r_bot
            if r_total > 0:
                i_div = v_in / r_total
                if i_div > 0.010:  # 10mA
                    issues.append(
                        ReviewIssue(
                            severity=ReviewSeverity.WARNING,
                            category=IssueCategory.ELECTRICAL,
                            rule_id="divider_current_excessive",
                            message=(
                                f"分压电流偏大：{i_div * 1000:.2f}mA > 10mA，"
                                f"会产生不必要的静态功耗"
                            ),
                            suggestion="增大分压电阻阻值（如 10kΩ~100kΩ），减小静态功耗",
                            evidence=f"I = v_in / (r_top + r_bot) = {v_in:.2f} / {r_total:.0f} = {i_div * 1000:.2f}mA",
                        )
                    )

        # --- divider_load_impedance ---
        issues.append(
            ReviewIssue(
                severity=ReviewSeverity.RECOMMENDATION,
                category=IssueCategory.ELECTRICAL,
                rule_id="divider_load_impedance",
                message="注意下级输入阻抗对分压比的影响",
                suggestion="确保下级负载阻抗 >> 分压下电阻（建议 ≥ 10× R_bot），否则分压比会偏低",
            )
        )

        return issues

    # ============================================================
    # 通用规则
    # ============================================================

    def _review_general(self, module: ModuleReviewInput) -> list[ReviewIssue]:
        """通用审查规则（适用于所有模块）"""
        issues: list[ReviewIssue] = []
        device = module.device
        params = module.parameters

        # --- decoupling_missing ---
        # 若器件有 specs 但拓扑中无 bypass/decoupling 电容
        has_specs = bool(device.specs)
        has_topology = device.topology is not None
        if has_specs and has_topology:
            comp_roles = {c.role for c in device.topology.external_components}
            has_decoupling = any(
                r in comp_roles
                for r in ("bypass_cap", "decoupling_cap", "input_cap", "output_cap")
            )
            if not has_decoupling:
                issues.append(
                    ReviewIssue(
                        severity=ReviewSeverity.RECOMMENDATION,
                        category=IssueCategory.COMPLETENESS,
                        rule_id="decoupling_missing",
                        message="建议在电源引脚附近添加去耦电容",
                        suggestion="在 VCC/VIN 引脚添加 100nF 陶瓷电容进行去耦，提高抗干扰能力",
                    )
                )

        # --- thermal_derating ---
        # 检查是否工作在额定值的 80% 以上
        v_in = _parse_numeric(params.get("v_in", ""))
        v_in_max = _parse_numeric(device.specs.get("v_in_max", ""))
        i_out = _parse_numeric(params.get("i_out", ""))
        i_out_max = _parse_numeric(device.specs.get("i_out_max", ""))

        if v_in is not None and v_in_max is not None and v_in_max > 0:
            ratio = v_in / v_in_max
            if ratio > 0.8:
                issues.append(
                    ReviewIssue(
                        severity=ReviewSeverity.WARNING,
                        category=IssueCategory.THERMAL,
                        rule_id="thermal_derating",
                        message=(
                            f"输入电压已达额定最大值的 {ratio * 100:.0f}%，"
                            f"建议留20%余量"
                        ),
                        suggestion="将工作电压控制在额定最大值的 80% 以内，提高可靠性",
                        evidence=f"v_in={v_in:.2f}V, v_in_max={v_in_max:.2f}V, ratio={ratio:.0%}",
                    )
                )
        elif i_out is not None and i_out_max is not None and i_out_max > 0:
            ratio = i_out / i_out_max
            if ratio > 0.8:
                issues.append(
                    ReviewIssue(
                        severity=ReviewSeverity.WARNING,
                        category=IssueCategory.THERMAL,
                        rule_id="thermal_derating",
                        message=(
                            f"输出电流已达额定最大值的 {ratio * 100:.0f}%，"
                            f"建议留20%余量"
                        ),
                        suggestion="将工作电流控制在额定最大值的 80% 以内，提高可靠性",
                        evidence=f"i_out={i_out:.3f}A, i_out_max={i_out_max:.3f}A, ratio={ratio:.0%}",
                    )
                )

        return issues

    # ============================================================
    # 布局注意事项
    # ============================================================

    def _review_layout(self, module: ModuleReviewInput) -> list[ReviewIssue]:
        """布局注意事项（LAYOUT_NOTE 严重级别）"""
        issues: list[ReviewIssue] = []
        category = module.category.lower()

        if category == "ldo":
            issues.append(
                ReviewIssue(
                    severity=ReviewSeverity.LAYOUT_NOTE,
                    category=IssueCategory.STABILITY,
                    rule_id="ldo_layout_caps_close",
                    message="输入输出电容应紧贴IC引脚放置",
                    suggestion="电容距离 IC 引脚应 < 2mm，减小走线寄生电感，避免 LDO 振荡",
                )
            )
        elif category in ("led", "led_indicator"):
            issues.append(
                ReviewIssue(
                    severity=ReviewSeverity.LAYOUT_NOTE,
                    category=IssueCategory.COMPLETENESS,
                    rule_id="led_layout_resistor_close",
                    message="限流电阻应靠近LED放置",
                    suggestion="限流电阻放置在 LED 附近（同侧），减少 EMI 并保证限流效果",
                )
            )

        return issues

    # ============================================================
    # 调试注意事项
    # ============================================================

    def _review_bringup(self, module: ModuleReviewInput) -> list[ReviewIssue]:
        """调试注意事项（BRINGUP_NOTE 严重级别）"""
        issues: list[ReviewIssue] = []
        category = module.category.lower()

        if category == "ldo":
            issues.append(
                ReviewIssue(
                    severity=ReviewSeverity.BRINGUP_NOTE,
                    category=IssueCategory.STABILITY,
                    rule_id="ldo_bringup_startup",
                    message="上电时检查输出电压稳定时间",
                    suggestion="用示波器观察上电瞬间输出波形，确认无过冲且在规格时间内稳定",
                )
            )
        elif category in ("led", "led_indicator"):
            issues.append(
                ReviewIssue(
                    severity=ReviewSeverity.BRINGUP_NOTE,
                    category=IssueCategory.COMPLETENESS,
                    rule_id="led_bringup_polarity",
                    message="注意确认LED极性正确",
                    suggestion="上电前用万用表二极管档确认 LED 阳极（A）和阴极（K）方向",
                )
            )

        return issues

    # ============================================================
    # 跨模块检查
    # ============================================================

    def _review_cross_module(
        self, modules: list[ModuleReviewInput]
    ) -> list[ReviewIssue]:
        """跨模块审查规则"""
        issues: list[ReviewIssue] = []

        # --- power_budget_check ---
        total_power = 0.0
        power_details: list[str] = []
        for module in modules:
            params = module.parameters
            category = module.category.lower()
            v_in = _parse_numeric(params.get("v_in", ""))
            v_out = _parse_numeric(params.get("v_out", ""))
            i_out = _parse_numeric(params.get("i_out", ""))

            if (
                category == "ldo"
                and v_in is not None
                and v_out is not None
                and i_out is not None
            ):
                p = (v_in - v_out) * i_out
                total_power += p
                power_details.append(f"{module.role}: {p:.2f}W")
            elif v_in is not None and i_out is not None:
                p = v_in * i_out
                total_power += p
                power_details.append(f"{module.role}: {p:.2f}W")

        if total_power > 2.0:
            issues.append(
                ReviewIssue(
                    severity=ReviewSeverity.WARNING,
                    category=IssueCategory.THERMAL,
                    rule_id="power_budget_check",
                    message=(
                        f"全设计总功耗偏高：{total_power:.2f}W > 2W，需关注系统散热设计"
                    ),
                    suggestion="评估各模块功耗分配，考虑提高转换效率或加强散热措施",
                    evidence=", ".join(power_details) if power_details else "",
                )
            )

        # --- ground_path_check ---
        issues.append(
            ReviewIssue(
                severity=ReviewSeverity.RECOMMENDATION,
                category=IssueCategory.ELECTRICAL,
                rule_id="ground_path_check",
                message="确保所有模块共享可靠接地路径",
                suggestion="采用星形接地拓扑，大电流地和小信号地分开，最终汇于单点接地",
            )
        )

        return issues

    # ============================================================
    # 工具方法
    # ============================================================

    def _check_external_component(
        self,
        module: ModuleReviewInput,
        comp_role: str,
        rule_id: str,
        severity: ReviewSeverity,
        category: IssueCategory,
        message: str,
        suggestion: str,
    ) -> list[ReviewIssue]:
        """检查拓扑中是否存在指定角色的外部元件"""
        if module.device.topology is None:
            return []

        comp_roles = {c.role for c in module.device.topology.external_components}
        if comp_role not in comp_roles:
            return [
                ReviewIssue(
                    severity=severity,
                    category=category,
                    rule_id=rule_id,
                    message=message,
                    suggestion=suggestion,
                )
            ]
        return []


# ============================================================
# 工具函数
# ============================================================


def _parse_numeric(text: str) -> float | None:
    """从字符串中提取浮点数，支持单位后缀换算

    支持的后缀（大小写不敏感）：
    - m → × 1e-3（毫）
    - u, μ → × 1e-6（微）
    - k → × 1e3（千）
    - M → × 1e6（兆）

    示例::

        _parse_numeric("3.3V")   → 3.3
        _parse_numeric("500mA")  → 0.5
        _parse_numeric("10uF")   → 1e-5
        _parse_numeric("4.7k")   → 4700.0
        _parse_numeric("1A")     → 1.0
        _parse_numeric("1.1")    → 1.1
    """
    if not text:
        return None

    text = text.strip()
    # 匹配数值+可选单位前缀+可选单位字母
    # 例如 "3.3V", "500mA", "10uF", "4.7kΩ", "1M"
    m = re.match(
        r"^([+-]?\d*\.?\d+(?:[eE][+-]?\d+)?)\s*([mMuUμkK]?)",
        text,
    )
    if not m:
        return None

    try:
        value = float(m.group(1))
    except ValueError:
        return None

    prefix = m.group(2)
    multiplier = _PREFIX_MULTIPLIER.get(prefix, 1.0)
    return value * multiplier


_PREFIX_MULTIPLIER: dict[str, float] = {
    "m": 1e-3,
    "u": 1e-6,
    "U": 1e-6,
    "μ": 1e-6,
    "k": 1e3,
    "K": 1e3,
    "M": 1e6,
    "": 1.0,
}
