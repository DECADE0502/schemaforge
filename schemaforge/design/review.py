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
        elif category == "buck":
            issues.extend(self._review_buck(module))
        elif category in ("led", "led_indicator"):
            issues.extend(self._review_led(module))
        elif category in ("voltage_divider", "divider"):
            issues.extend(self._review_voltage_divider(module))
        elif category in ("rc_filter", "passive_circuit"):
            issues.extend(self._review_rc_filter(module))
        elif category == "boost":
            issues.extend(self._review_boost(module))
        elif category == "flyback":
            issues.extend(self._review_flyback(module))
        elif category == "sepic":
            issues.extend(self._review_sepic(module))
        elif category in ("opamp", "op_amp"):
            issues.extend(self._review_opamp(module))

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
    # Buck 规则
    # ============================================================

    def _review_buck(self, module: ModuleReviewInput) -> list[ReviewIssue]:
        """Buck 降压转换器专属审查规则"""
        issues: list[ReviewIssue] = []
        params = module.parameters
        device = module.device

        v_in = _parse_numeric(params.get("v_in", ""))
        v_out = _parse_numeric(params.get("v_out", ""))
        i_out = _parse_numeric(params.get("i_out_max", params.get("i_out", "")))
        v_in_max = _parse_numeric(device.specs.get("v_in_max", ""))
        fsw = _parse_numeric(params.get("fsw", device.specs.get("fsw", "")))

        # --- buck_inductor_saturation (BLOCKING) ---
        if i_out is not None:
            i_ripple_est = i_out * 0.3
            i_peak = i_out + i_ripple_est / 2
            i_sat_required = i_peak * 1.3
            issues.append(
                ReviewIssue(
                    severity=ReviewSeverity.BLOCKING,
                    category=IssueCategory.ELECTRICAL,
                    rule_id="buck_inductor_saturation",
                    message=(
                        f"电感饱和电流需 ≥ {i_sat_required:.2f}A "
                        f"（峰值电流 {i_peak:.2f}A × 1.3 安全裕量）"
                    ),
                    suggestion=(
                        f"选择饱和电流 ≥ {i_sat_required:.1f}A 的电感，"
                        f"否则大电流时电感饱和导致电流失控"
                    ),
                    evidence=(
                        f"i_out={i_out:.2f}A, i_ripple_est={i_ripple_est:.2f}A, "
                        f"i_peak={i_peak:.2f}A"
                    ),
                )
            )

        # --- buck_input_cap_rms (WARNING) ---
        if v_in is not None and v_out is not None and i_out is not None and v_in > 0:
            duty = v_out / v_in
            i_rms = i_out * (duty * (1 - duty)) ** 0.5
            if i_rms > 0.5:
                issues.append(
                    ReviewIssue(
                        severity=ReviewSeverity.WARNING,
                        category=IssueCategory.THERMAL,
                        rule_id="buck_input_cap_rms",
                        message=(
                            f"输入电容 RMS 纹波电流较大：{i_rms:.2f}A，"
                            f"需使用低ESR电容承受此纹波"
                        ),
                        suggestion="选用额定纹波电流 ≥ 实际RMS电流的低ESR陶瓷电容，或并联多颗",
                        evidence=(
                            f"duty={duty:.2f}, I_rms = I_out × √(D×(1-D)) = "
                            f"{i_out:.2f} × √({duty:.2f}×{1 - duty:.2f}) = {i_rms:.2f}A"
                        ),
                    )
                )

        # --- buck_output_ripple (WARNING) ---
        if v_in is not None and v_out is not None and fsw is not None and v_in > 0:
            duty = v_out / v_in
            l_value = 22e-6
            fsw_hz = fsw * 1000 if fsw < 10000 else fsw
            if fsw_hz > 0:
                delta_il = (v_in - v_out) * duty / (fsw_hz * l_value)
                ripple_mv = delta_il / (47e-6 * fsw_hz) * 1000 if delta_il > 0 else 0
                if delta_il > 0:
                    issues.append(
                        ReviewIssue(
                            severity=ReviewSeverity.WARNING,
                            category=IssueCategory.ELECTRICAL,
                            rule_id="buck_output_ripple",
                            message=(
                                f"电感纹波电流约 {delta_il:.2f}A，"
                                f"输出纹波约 {ripple_mv:.0f}mV（估算值，取决于实际电容ESR）"
                            ),
                            suggestion="若纹波过大，增大电感值或输出电容，或加后级LC滤波",
                            evidence=(
                                f"ΔI_L = (V_in-V_out)×D/(f×L) = "
                                f"({v_in:.1f}-{v_out:.1f})×{duty:.2f}/({fsw_hz:.0f}×22μH)"
                            ),
                        )
                    )

        # --- buck_feedback_accuracy (BLOCKING) ---
        if v_out is not None:
            v_fb = 1.22
            r_fb_lower = 10000
            r_fb_upper_ideal = r_fb_lower * (v_out / v_fb - 1)
            if r_fb_upper_ideal > 0:
                v_out_actual = v_fb * (1 + r_fb_upper_ideal / r_fb_lower)
                error_pct = abs(v_out_actual - v_out) / v_out * 100 if v_out > 0 else 0
                issues.append(
                    ReviewIssue(
                        severity=ReviewSeverity.BLOCKING,
                        category=IssueCategory.ELECTRICAL,
                        rule_id="buck_feedback_accuracy",
                        message=(
                            f"反馈分压网络：R_upper={r_fb_upper_ideal:.0f}Ω / "
                            f"R_lower={r_fb_lower}Ω → V_out={v_out_actual:.3f}V "
                            f"（误差 {error_pct:.2f}%）"
                        ),
                        suggestion=(
                            "使用1%精度电阻，并确认电阻值为E96标准值，"
                            "反馈走线远离SW节点避免噪声耦合"
                        ),
                        evidence=(
                            f"V_out = V_fb × (1 + R_upper/R_lower) = "
                            f"{v_fb} × (1 + {r_fb_upper_ideal:.0f}/{r_fb_lower})"
                        ),
                    )
                )

        # --- buck_max_vin_exceeded (BLOCKING) ---
        if v_in is not None and v_in_max is not None:
            if v_in > v_in_max:
                issues.append(
                    ReviewIssue(
                        severity=ReviewSeverity.BLOCKING,
                        category=IssueCategory.ELECTRICAL,
                        rule_id="buck_max_vin_exceeded",
                        message=(
                            f"输入电压 {v_in:.2f}V 超过器件最大额定输入 {v_in_max:.2f}V"
                        ),
                        suggestion=f"降低输入电压至 {v_in_max:.2f}V 以下，或选择耐压更高的Buck IC",
                        evidence=f"v_in={v_in:.2f}V, spec v_in_max={v_in_max:.2f}V",
                    )
                )

        # --- buck_bootstrap_cap (RECOMMENDATION) ---
        issues.extend(
            self._check_external_component(
                module=module,
                comp_role="boot_cap",
                rule_id="buck_bootstrap_cap",
                severity=ReviewSeverity.RECOMMENDATION,
                category=IssueCategory.COMPLETENESS,
                message="建议添加自举电容（100nF）以确保高侧驱动正常工作",
                suggestion="在BST和SW引脚间放置100nF陶瓷电容，距IC引脚<2mm",
            )
        )

        # --- buck_catch_diode (WARNING) ---
        issues.extend(
            self._check_external_component(
                module=module,
                comp_role="catch_diode",
                rule_id="buck_catch_diode",
                severity=ReviewSeverity.WARNING,
                category=IssueCategory.COMPLETENESS,
                message="异步Buck需要续流二极管，缺失将导致SW节点电压失控",
                suggestion="添加额定电压≥V_in_max的Schottky续流二极管（如SS34）",
            )
        )

        return issues

    # ============================================================
    # RC 滤波器规则
    # ============================================================

    def _review_rc_filter(self, module: ModuleReviewInput) -> list[ReviewIssue]:
        """RC 滤波器专属审查规则"""
        issues: list[ReviewIssue] = []
        params = module.parameters

        f_cutoff = _parse_numeric(params.get("f_cutoff", ""))
        r_value = _parse_numeric(params.get("r_value", params.get("r", "")))

        # --- rc_filter_impedance_mismatch ---
        if r_value is not None:
            if r_value > 100000:
                issues.append(
                    ReviewIssue(
                        severity=ReviewSeverity.WARNING,
                        category=IssueCategory.ELECTRICAL,
                        rule_id="rc_filter_impedance_mismatch",
                        message=(
                            f"滤波电阻阻值偏大：{r_value / 1000:.1f}kΩ > 100kΩ，"
                            f"容易拾取噪声且后级负载可能影响滤波效果"
                        ),
                        suggestion="建议使用 1kΩ~100kΩ 范围的电阻，高阻抗时考虑有源滤波",
                        evidence=f"r_value={r_value:.0f}Ω",
                    )
                )
            elif r_value < 100:
                issues.append(
                    ReviewIssue(
                        severity=ReviewSeverity.WARNING,
                        category=IssueCategory.ELECTRICAL,
                        rule_id="rc_filter_impedance_mismatch",
                        message=(
                            f"滤波电阻阻值偏小：{r_value:.0f}Ω < 100Ω，"
                            f"电容值需很大才能达到低截止频率"
                        ),
                        suggestion="增大电阻值至1kΩ以上，可使用更小的电容达到相同截止频率",
                        evidence=f"r_value={r_value:.0f}Ω",
                    )
                )

        # --- rc_filter_cutoff_range ---
        if f_cutoff is not None:
            if f_cutoff > 1e6:
                issues.append(
                    ReviewIssue(
                        severity=ReviewSeverity.RECOMMENDATION,
                        category=IssueCategory.ELECTRICAL,
                        rule_id="rc_filter_cutoff_range",
                        message=(
                            f"截止频率 {f_cutoff / 1e6:.1f}MHz 较高，"
                            f"元件寄生参数可能影响实际滤波效果"
                        ),
                        suggestion="高频应用建议使用LC滤波器或有源滤波器",
                    )
                )

        # --- rc_filter_load_effect ---
        issues.append(
            ReviewIssue(
                severity=ReviewSeverity.RECOMMENDATION,
                category=IssueCategory.ELECTRICAL,
                rule_id="rc_filter_load_effect",
                message="注意后级负载阻抗对RC滤波器截止频率的影响",
                suggestion="确保后级输入阻抗 >> 滤波电阻（建议 ≥ 10×R），否则截止频率和增益会偏离设计值",
            )
        )

        return issues

    # ============================================================
    # Boost 升压转换器规则
    # ============================================================

    def _review_boost(self, module: ModuleReviewInput) -> list[ReviewIssue]:
        """Boost 升压转换器专属审查规则"""
        issues: list[ReviewIssue] = []
        params = module.parameters
        device = module.device

        v_in = _parse_numeric(params.get("v_in", ""))
        v_out = _parse_numeric(params.get("v_out", ""))
        i_out = _parse_numeric(params.get("i_out_max", params.get("i_out", "")))
        v_in_max = _parse_numeric(device.specs.get("v_in_max", ""))
        fsw = _parse_numeric(params.get("fsw", device.specs.get("fsw", "")))

        # --- boost_vin_vout_relation (BLOCKING) ---
        if v_in is not None and v_out is not None:
            if v_in >= v_out:
                issues.append(
                    ReviewIssue(
                        severity=ReviewSeverity.BLOCKING,
                        category=IssueCategory.ELECTRICAL,
                        rule_id="boost_vin_vout_relation",
                        message=(
                            f"Boost 升压拓扑要求输入电压 < 输出电压，"
                            f"当前 V_in={v_in:.2f}V ≥ V_out={v_out:.2f}V"
                        ),
                        suggestion="确认拓扑选择正确，若需降压请改用 Buck 拓扑",
                        evidence=f"v_in={v_in:.2f}V, v_out={v_out:.2f}V",
                    )
                )

        # --- boost_duty_cycle (BLOCKING / WARNING) ---
        if v_in is not None and v_out is not None and v_out > 0 and v_in < v_out:
            duty = 1 - (v_in / v_out)
            if duty > 0.85:
                issues.append(
                    ReviewIssue(
                        severity=ReviewSeverity.BLOCKING,
                        category=IssueCategory.ELECTRICAL,
                        rule_id="boost_duty_cycle",
                        message=(
                            f"Boost 占空比过高：D={duty:.2f}（>{0.85}），"
                            f"转换效率极低且开关管导通损耗大"
                        ),
                        suggestion="降低升压比（减小 V_out 或增大 V_in），或采用级联升压拓扑",
                        evidence=(
                            f"D = 1 - V_in/V_out = 1 - {v_in:.2f}/{v_out:.2f} = {duty:.2f}"
                        ),
                    )
                )
            elif duty < 0.1:
                issues.append(
                    ReviewIssue(
                        severity=ReviewSeverity.WARNING,
                        category=IssueCategory.ELECTRICAL,
                        rule_id="boost_duty_cycle",
                        message=(
                            f"Boost 占空比过低：D={duty:.2f}（<{0.1}），"
                            f"升压比极小，Boost 拓扑优势不明显"
                        ),
                        suggestion="若升压比接近1，考虑使用 LDO 或 charge pump 替代",
                        evidence=(
                            f"D = 1 - V_in/V_out = 1 - {v_in:.2f}/{v_out:.2f} = {duty:.2f}"
                        ),
                    )
                )

        # --- boost_inductor_ripple (WARNING) ---
        if (
            v_in is not None
            and v_out is not None
            and i_out is not None
            and fsw is not None
            and v_out > 0
            and v_in < v_out
        ):
            duty = 1 - (v_in / v_out)
            fsw_hz = fsw * 1000 if fsw < 10000 else fsw
            l_value = 22e-6  # 典型电感值估算
            if fsw_hz > 0:
                # Boost 输入电流 ≈ I_out / (1-D)
                i_in = i_out / (1 - duty) if duty < 1 else i_out
                delta_il = v_in * duty / (fsw_hz * l_value)
                ripple_ratio = delta_il / i_in if i_in > 0 else 0
                if ripple_ratio > 0.4:
                    issues.append(
                        ReviewIssue(
                            severity=ReviewSeverity.WARNING,
                            category=IssueCategory.ELECTRICAL,
                            rule_id="boost_inductor_ripple",
                            message=(
                                f"电感电流纹波偏大：ΔI_L={delta_il:.2f}A，"
                                f"纹波比={ripple_ratio:.0%}（建议 <40%）"
                            ),
                            suggestion="增大电感值或提高开关频率，降低电流纹波",
                            evidence=(
                                f"ΔI_L = V_in×D/(f×L) = "
                                f"{v_in:.1f}×{duty:.2f}/({fsw_hz:.0f}×22μH) = {delta_il:.2f}A, "
                                f"I_in={i_in:.2f}A"
                            ),
                        )
                    )

        # --- boost_output_cap_ripple (WARNING) ---
        if (
            v_out is not None
            and i_out is not None
            and fsw is not None
            and v_out > 0
        ):
            fsw_hz = fsw * 1000 if fsw < 10000 else fsw
            c_out = 47e-6  # 典型输出电容
            if fsw_hz > 0 and v_in is not None and v_in < v_out:
                duty = 1 - (v_in / v_out)
                # 输出纹波 ΔV ≈ I_out × D / (f × C)
                delta_v = i_out * duty / (fsw_hz * c_out)
                ripple_pct = delta_v / v_out * 100
                if ripple_pct > 2:
                    issues.append(
                        ReviewIssue(
                            severity=ReviewSeverity.WARNING,
                            category=IssueCategory.ELECTRICAL,
                            rule_id="boost_output_cap_ripple",
                            message=(
                                f"输出电容纹波电压偏大：ΔV={delta_v * 1000:.1f}mV，"
                                f"占输出电压的 {ripple_pct:.1f}%（建议 <2%）"
                            ),
                            suggestion="增大输出电容或选用低ESR电容，降低输出纹波",
                            evidence=(
                                f"ΔV = I_out×D/(f×C) = "
                                f"{i_out:.2f}×{duty:.2f}/({fsw_hz:.0f}×47μF) = "
                                f"{delta_v * 1000:.1f}mV"
                            ),
                        )
                    )

        # --- boost_max_vin_exceeded (BLOCKING) ---
        if v_in is not None and v_in_max is not None:
            if v_in > v_in_max:
                issues.append(
                    ReviewIssue(
                        severity=ReviewSeverity.BLOCKING,
                        category=IssueCategory.ELECTRICAL,
                        rule_id="boost_max_vin_exceeded",
                        message=(
                            f"输入电压 {v_in:.2f}V 超过器件最大额定输入 {v_in_max:.2f}V"
                        ),
                        suggestion=f"降低输入电压至 {v_in_max:.2f}V 以下，或选择耐压更高的 Boost IC",
                        evidence=f"v_in={v_in:.2f}V, spec v_in_max={v_in_max:.2f}V",
                    )
                )

        return issues

    # ============================================================
    # Flyback 反激变换器规则
    # ============================================================

    def _review_flyback(self, module: ModuleReviewInput) -> list[ReviewIssue]:
        """Flyback 反激变换器专属审查规则"""
        issues: list[ReviewIssue] = []
        params = module.parameters
        device = module.device

        v_in = _parse_numeric(params.get("v_in", ""))
        fsw = _parse_numeric(params.get("fsw", device.specs.get("fsw", "")))
        n_ratio = _parse_numeric(params.get("turns_ratio", params.get("n", "")))
        v_isolation = _parse_numeric(
            params.get("v_isolation", device.specs.get("v_isolation", ""))
        )
        d_max = _parse_numeric(params.get("d_max", ""))
        ae = _parse_numeric(params.get("ae", ""))  # 磁芯截面积 mm²

        # --- flyback_turns_ratio (WARNING / BLOCKING) ---
        if n_ratio is not None:
            if n_ratio < 0.5:
                issues.append(
                    ReviewIssue(
                        severity=ReviewSeverity.BLOCKING,
                        category=IssueCategory.ELECTRICAL,
                        rule_id="flyback_turns_ratio",
                        message=(
                            f"匝比过低：N={n_ratio:.2f}（<0.5），"
                            f"原边电流极大，变压器和开关管损耗严重"
                        ),
                        suggestion="增大匝比或重新评估输入输出电压需求",
                        evidence=f"turns_ratio={n_ratio:.2f}",
                    )
                )
            elif n_ratio > 20:
                issues.append(
                    ReviewIssue(
                        severity=ReviewSeverity.BLOCKING,
                        category=IssueCategory.ELECTRICAL,
                        rule_id="flyback_turns_ratio",
                        message=(
                            f"匝比过高：N={n_ratio:.2f}（>20），"
                            f"漏感大、耦合差，难以实现可靠设计"
                        ),
                        suggestion="降低匝比，考虑使用多级变换或选择更适合的拓扑",
                        evidence=f"turns_ratio={n_ratio:.2f}",
                    )
                )
            elif n_ratio > 10:
                issues.append(
                    ReviewIssue(
                        severity=ReviewSeverity.WARNING,
                        category=IssueCategory.ELECTRICAL,
                        rule_id="flyback_turns_ratio",
                        message=(
                            f"匝比偏高：N={n_ratio:.2f}（>10），"
                            f"注意漏感和耦合系数对效率的影响"
                        ),
                        suggestion="验证变压器漏感在可接受范围内，优化绕制工艺",
                        evidence=f"turns_ratio={n_ratio:.2f}",
                    )
                )

        # --- flyback_transformer_saturation (WARNING) ---
        if (
            v_in is not None
            and d_max is not None
            and fsw is not None
            and n_ratio is not None
            and ae is not None
        ):
            fsw_hz = fsw * 1000 if fsw < 10000 else fsw
            # Ae 输入单位 mm²，转换为 m²
            ae_m2 = ae * 1e-6
            n_primary = max(n_ratio, 1)  # 用匝比近似原边匝数
            if fsw_hz > 0 and n_primary > 0 and ae_m2 > 0:
                # B_max = V_in × D_max / (f × Np × Ae)
                b_max = v_in * d_max / (fsw_hz * n_primary * ae_m2)
                if b_max > 0.3:  # 铁氧体饱和通常 ~0.3T
                    issues.append(
                        ReviewIssue(
                            severity=ReviewSeverity.WARNING,
                            category=IssueCategory.ELECTRICAL,
                            rule_id="flyback_transformer_saturation",
                            message=(
                                f"变压器磁芯可能饱和：B_max≈{b_max:.2f}T（>0.3T），"
                                f"会导致电流尖峰和效率下降"
                            ),
                            suggestion="增大磁芯截面积、降低占空比、增加原边匝数或提高开关频率",
                            evidence=(
                                f"B_max = V_in×D_max/(f×Np×Ae) = "
                                f"{v_in:.1f}×{d_max:.2f}/({fsw_hz:.0f}×{n_primary:.0f}×{ae_m2:.2e}) "
                                f"= {b_max:.2f}T"
                            ),
                        )
                    )

        # --- flyback_snubber_required (RECOMMENDATION) ---
        issues.extend(
            self._check_external_component(
                module=module,
                comp_role="snubber",
                rule_id="flyback_snubber_required",
                severity=ReviewSeverity.RECOMMENDATION,
                category=IssueCategory.PROTECTION,
                message="反激变换器建议添加 RCD 钳位吸收电路，抑制漏感尖峰",
                suggestion="在原边开关管漏极添加 RCD 钳位电路，将尖峰电压限制在安全范围内",
            )
        )

        # --- flyback_isolation_voltage (BLOCKING) ---
        if v_isolation is not None:
            if v_isolation < 1500:
                issues.append(
                    ReviewIssue(
                        severity=ReviewSeverity.BLOCKING,
                        category=IssueCategory.PROTECTION,
                        rule_id="flyback_isolation_voltage",
                        message=(
                            f"隔离耐压不足：{v_isolation:.0f}V < 1500V，"
                            f"不满足基本安规要求"
                        ),
                        suggestion="选择隔离耐压 ≥ 1500Vrms 的变压器，满足安规认证要求",
                        evidence=f"v_isolation={v_isolation:.0f}V, required ≥ 1500V",
                    )
                )

        return issues

    # ============================================================
    # SEPIC 转换器规则
    # ============================================================

    def _review_sepic(self, module: ModuleReviewInput) -> list[ReviewIssue]:
        """SEPIC 转换器专属审查规则"""
        issues: list[ReviewIssue] = []
        params = module.parameters
        device = module.device

        v_in = _parse_numeric(params.get("v_in", ""))
        v_out = _parse_numeric(params.get("v_out", ""))
        i_out = _parse_numeric(params.get("i_out_max", params.get("i_out", "")))
        fsw = _parse_numeric(params.get("fsw", device.specs.get("fsw", "")))

        # --- sepic_coupling_cap_voltage (WARNING) ---
        # 耦合电容两端电压 ≈ V_in，需确保额定电压足够
        if v_in is not None:
            v_cap_rating = _parse_numeric(
                params.get("coupling_cap_rating", "")
            )
            if v_cap_rating is not None:
                if v_cap_rating < v_in * 1.5:
                    issues.append(
                        ReviewIssue(
                            severity=ReviewSeverity.WARNING,
                            category=IssueCategory.ELECTRICAL,
                            rule_id="sepic_coupling_cap_voltage",
                            message=(
                                f"耦合电容额定电压不足：{v_cap_rating:.1f}V "
                                f"< 1.5×V_in={v_in * 1.5:.1f}V，"
                                f"建议额定电压 ≥ 1.5 倍输入电压"
                            ),
                            suggestion="选择额定电压 ≥ 1.5×V_in 的耦合电容，留足降额余量",
                            evidence=(
                                f"coupling_cap_rating={v_cap_rating:.1f}V, "
                                f"v_in={v_in:.2f}V, "
                                f"required ≥ {v_in * 1.5:.1f}V"
                            ),
                        )
                    )
            else:
                # 无额定电压信息时给出建议
                issues.append(
                    ReviewIssue(
                        severity=ReviewSeverity.RECOMMENDATION,
                        category=IssueCategory.ELECTRICAL,
                        rule_id="sepic_coupling_cap_voltage",
                        message=(
                            f"耦合电容两端电压约等于 V_in={v_in:.2f}V，"
                            f"请确认额定电压 ≥ {v_in * 1.5:.1f}V"
                        ),
                        suggestion="选择额定电压 ≥ 1.5×V_in 的耦合电容，推荐陶瓷或薄膜电容",
                    )
                )

        # --- sepic_coupled_inductor_polarity (RECOMMENDATION) ---
        issues.append(
            ReviewIssue(
                severity=ReviewSeverity.RECOMMENDATION,
                category=IssueCategory.ELECTRICAL,
                rule_id="sepic_coupled_inductor_polarity",
                message="SEPIC 拓扑中耦合电感的同名端极性必须正确，否则无法正常工作",
                suggestion="确认耦合电感同名端（打点端）连接正确：L1 打点端接输入，L2 打点端接耦合电容",
            )
        )

        # --- sepic_output_ripple (WARNING) ---
        if (
            v_in is not None
            and v_out is not None
            and i_out is not None
            and fsw is not None
            and v_out > 0
        ):
            fsw_hz = fsw * 1000 if fsw < 10000 else fsw
            c_out = 47e-6  # 典型输出电容
            if fsw_hz > 0 and (v_in + v_out) > 0:
                duty = v_out / (v_in + v_out)
                # 输出纹波 ΔV ≈ I_out × D / (f × C)
                delta_v = i_out * duty / (fsw_hz * c_out)
                ripple_pct = delta_v / v_out * 100
                if ripple_pct > 2:
                    issues.append(
                        ReviewIssue(
                            severity=ReviewSeverity.WARNING,
                            category=IssueCategory.ELECTRICAL,
                            rule_id="sepic_output_ripple",
                            message=(
                                f"输出纹波电压偏大：ΔV={delta_v * 1000:.1f}mV，"
                                f"占输出电压的 {ripple_pct:.1f}%（建议 <2%）"
                            ),
                            suggestion="增大输出电容或选用低ESR电容，降低输出纹波",
                            evidence=(
                                f"ΔV = I_out×D/(f×C) = "
                                f"{i_out:.2f}×{duty:.2f}/({fsw_hz:.0f}×47μF) = "
                                f"{delta_v * 1000:.1f}mV"
                            ),
                        )
                    )

        return issues

    # ============================================================
    # 运算放大器规则
    # ============================================================

    def _review_opamp(self, module: ModuleReviewInput) -> list[ReviewIssue]:
        """运算放大器专属审查规则"""
        issues: list[ReviewIssue] = []
        params = module.parameters
        device = module.device

        v_supply_pos = _parse_numeric(
            params.get("v_supply_pos", params.get("vcc", params.get("v_supply", "")))
        )
        v_supply_neg = _parse_numeric(
            params.get("v_supply_neg", params.get("vee", ""))
        )
        v_out_max = _parse_numeric(params.get("v_out_max", params.get("v_out", "")))
        v_cm = _parse_numeric(params.get("v_cm", params.get("v_input_cm", "")))
        v_cm_min = _parse_numeric(device.specs.get("v_cm_min", ""))
        v_cm_max = _parse_numeric(device.specs.get("v_cm_max", ""))
        gbw = _parse_numeric(device.specs.get("gbw", device.specs.get("gbw_mhz", "")))
        f_signal = _parse_numeric(params.get("f_signal", params.get("frequency", "")))
        gain = _parse_numeric(params.get("gain", params.get("av", "")))

        # 计算供电轨
        v_rail_pos = v_supply_pos if v_supply_pos is not None else None
        # v_supply_neg 预留给双电源运放扩展使用
        _ = v_supply_neg

        # --- opamp_output_swing (WARNING) ---
        if v_out_max is not None and v_rail_pos is not None:
            # 典型非 rail-to-rail 运放输出摆幅约 ±1V 距离电源轨
            headroom = 1.0
            v_max_available = v_rail_pos - headroom
            if v_out_max > v_max_available:
                issues.append(
                    ReviewIssue(
                        severity=ReviewSeverity.WARNING,
                        category=IssueCategory.ELECTRICAL,
                        rule_id="opamp_output_swing",
                        message=(
                            f"输出电压 {v_out_max:.2f}V 可能超出运放输出摆幅："
                            f"V_supply={v_rail_pos:.2f}V，预留 {headroom}V 余量后"
                            f"最大输出约 {v_max_available:.2f}V"
                        ),
                        suggestion="选用 Rail-to-Rail 输出运放，或降低输出电压要求，或提高供电电压",
                        evidence=(
                            f"v_out_max={v_out_max:.2f}V, "
                            f"v_supply_pos={v_rail_pos:.2f}V, "
                            f"headroom={headroom}V"
                        ),
                    )
                )

        # --- opamp_input_cm_range (BLOCKING) ---
        if v_cm is not None:
            if v_cm_min is not None and v_cm < v_cm_min:
                issues.append(
                    ReviewIssue(
                        severity=ReviewSeverity.BLOCKING,
                        category=IssueCategory.ELECTRICAL,
                        rule_id="opamp_input_cm_range",
                        message=(
                            f"输入共模电压 {v_cm:.2f}V 低于运放规格下限 {v_cm_min:.2f}V，"
                            f"运放无法正常工作"
                        ),
                        suggestion="调整输入偏置电压或选用支持更低共模输入的运放",
                        evidence=(
                            f"v_cm={v_cm:.2f}V, spec v_cm_min={v_cm_min:.2f}V"
                        ),
                    )
                )
            if v_cm_max is not None and v_cm > v_cm_max:
                issues.append(
                    ReviewIssue(
                        severity=ReviewSeverity.BLOCKING,
                        category=IssueCategory.ELECTRICAL,
                        rule_id="opamp_input_cm_range",
                        message=(
                            f"输入共模电压 {v_cm:.2f}V 超过运放规格上限 {v_cm_max:.2f}V，"
                            f"运放无法正常工作"
                        ),
                        suggestion="调整输入偏置电压或选用支持更高共模输入的运放",
                        evidence=(
                            f"v_cm={v_cm:.2f}V, spec v_cm_max={v_cm_max:.2f}V"
                        ),
                    )
                )

        # --- opamp_gbw_check (WARNING) ---
        if gbw is not None and f_signal is not None and gain is not None:
            # GBW in MHz, f_signal 可能是 Hz 或 kHz
            gbw_hz = gbw * 1e6 if gbw < 1e6 else gbw
            f_hz = f_signal * 1e3 if f_signal < 1e6 else f_signal
            required_gbw = f_hz * gain
            if required_gbw > gbw_hz:
                issues.append(
                    ReviewIssue(
                        severity=ReviewSeverity.WARNING,
                        category=IssueCategory.ELECTRICAL,
                        rule_id="opamp_gbw_check",
                        message=(
                            f"增益带宽积不足：所需 GBW={required_gbw / 1e6:.1f}MHz "
                            f"（增益×频率={gain:.0f}×{f_hz / 1e3:.1f}kHz），"
                            f"运放 GBW={gbw_hz / 1e6:.1f}MHz"
                        ),
                        suggestion="选用 GBW 更高的运放，或降低增益/工作频率",
                        evidence=(
                            f"required_GBW = gain × f_signal = "
                            f"{gain:.0f} × {f_hz:.0f}Hz = {required_gbw / 1e6:.1f}MHz, "
                            f"spec GBW={gbw_hz / 1e6:.1f}MHz"
                        ),
                    )
                )

        # --- opamp_stability_high_gain (WARNING) ---
        if gain is not None and gain > 100:
            issues.append(
                ReviewIssue(
                    severity=ReviewSeverity.WARNING,
                    category=IssueCategory.STABILITY,
                    rule_id="opamp_stability_high_gain",
                    message=(
                        f"运放增益较高（Av={gain:.0f}），"
                        f"需注意环路稳定性和相位裕量"
                    ),
                    suggestion=(
                        "高增益电路建议分级放大，添加频率补偿网络，"
                        "并用波特图分析确认相位裕量 > 45°"
                    ),
                    evidence=f"gain={gain:.0f}",
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
        elif category == "buck":
            issues.append(
                ReviewIssue(
                    severity=ReviewSeverity.LAYOUT_NOTE,
                    category=IssueCategory.STABILITY,
                    rule_id="buck_layout_loop",
                    message="SW节点走线应短粗，电感靠近IC，输入电容紧贴VIN/GND",
                    suggestion=(
                        "Buck电路的功率环路（VIN→IC→SW→L→C_out→GND）面积要最小化，"
                        "反馈走线远离SW节点避免噪声耦合"
                    ),
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
        elif category == "boost":
            issues.append(
                ReviewIssue(
                    severity=ReviewSeverity.LAYOUT_NOTE,
                    category=IssueCategory.STABILITY,
                    rule_id="boost_layout_loop",
                    message="Boost电感和二极管应紧贴IC放置，输出电容靠近二极管",
                    suggestion=(
                        "Boost功率环路（VIN→L→SW→D→C_out→GND）面积最小化，"
                        "输入电容紧贴VIN和GND引脚"
                    ),
                )
            )
        elif category == "flyback":
            issues.append(
                ReviewIssue(
                    severity=ReviewSeverity.LAYOUT_NOTE,
                    category=IssueCategory.STABILITY,
                    rule_id="flyback_layout_transformer",
                    message="变压器布局需原副边走线严格隔离，保证安规爬电距离",
                    suggestion=(
                        "原边和副边走线需保持足够爬电距离（≥6mm），"
                        "钳位电路紧贴开关管漏极，输出整流二极管紧贴变压器副边"
                    ),
                )
            )
        elif category == "sepic":
            issues.append(
                ReviewIssue(
                    severity=ReviewSeverity.LAYOUT_NOTE,
                    category=IssueCategory.STABILITY,
                    rule_id="sepic_layout_coupling_cap",
                    message="耦合电容应紧贴两个电感放置，走线短且粗",
                    suggestion=(
                        "SEPIC耦合电容连接两个电感之间的节点，走线寄生电感会影响效率，"
                        "需尽量缩短连接路径"
                    ),
                )
            )
        elif category in ("opamp", "op_amp"):
            issues.append(
                ReviewIssue(
                    severity=ReviewSeverity.LAYOUT_NOTE,
                    category=IssueCategory.STABILITY,
                    rule_id="opamp_layout_decoupling",
                    message="运放电源引脚应紧贴放置去耦电容，信号走线远离数字噪声源",
                    suggestion=(
                        "在 VCC/VEE 引脚放置 100nF 陶瓷电容（<2mm），"
                        "输入走线采用保护环，远离开关电源和数字信号走线"
                    ),
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
        elif category == "buck":
            issues.append(
                ReviewIssue(
                    severity=ReviewSeverity.BRINGUP_NOTE,
                    category=IssueCategory.STABILITY,
                    rule_id="buck_bringup_softstart",
                    message="上电时观察SW节点波形和输出电压软启动过程",
                    suggestion=(
                        "用示波器探测SW节点确认开关波形正常，"
                        "检查输出电压单调上升无过冲，逐步加载测试稳定性"
                    ),
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
        elif category == "boost":
            issues.append(
                ReviewIssue(
                    severity=ReviewSeverity.BRINGUP_NOTE,
                    category=IssueCategory.STABILITY,
                    rule_id="boost_bringup_startup",
                    message="上电时观察输出电压软启动过程和电感电流波形",
                    suggestion=(
                        "用示波器观察输出电压上升过程，确认无过冲；"
                        "用电流探头检查电感电流波形确认连续导通模式"
                    ),
                )
            )
        elif category == "flyback":
            issues.append(
                ReviewIssue(
                    severity=ReviewSeverity.BRINGUP_NOTE,
                    category=IssueCategory.STABILITY,
                    rule_id="flyback_bringup_leakage",
                    message="上电时观察开关管漏极尖峰电压和钳位电路效果",
                    suggestion=(
                        "用示波器测量开关管漏极波形，确认钳位电路有效抑制漏感尖峰，"
                        "尖峰电压不超过开关管额定值的80%"
                    ),
                )
            )
        elif category == "sepic":
            issues.append(
                ReviewIssue(
                    severity=ReviewSeverity.BRINGUP_NOTE,
                    category=IssueCategory.STABILITY,
                    rule_id="sepic_bringup_coupling",
                    message="上电时确认耦合电容电压和两个电感电流波形正常",
                    suggestion=(
                        "测量耦合电容两端直流电压应约等于 V_in，"
                        "检查两个电感电流波形确认正常工作"
                    ),
                )
            )
        elif category in ("opamp", "op_amp"):
            issues.append(
                ReviewIssue(
                    severity=ReviewSeverity.BRINGUP_NOTE,
                    category=IssueCategory.STABILITY,
                    rule_id="opamp_bringup_offset",
                    message="上电后检查运放输出静态偏置和信号完整性",
                    suggestion=(
                        "无输入信号时测量输出直流偏置是否在预期范围，"
                        "输入信号后确认增益和带宽符合设计要求，注意观察是否有振荡"
                    ),
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
            elif category == "buck" and v_out is not None and i_out is not None:
                eff = 0.85
                p_out = v_out * i_out
                p_loss = p_out * (1 / eff - 1)
                total_power += p_loss
                power_details.append(f"{module.role}: {p_loss:.2f}W (Buck损耗)")
            elif category == "boost" and v_out is not None and i_out is not None:
                eff = 0.85
                p_out = v_out * i_out
                p_loss = p_out * (1 / eff - 1)
                total_power += p_loss
                power_details.append(f"{module.role}: {p_loss:.2f}W (Boost损耗)")
            elif category == "flyback" and v_out is not None and i_out is not None:
                eff = 0.80
                p_out = v_out * i_out
                p_loss = p_out * (1 / eff - 1)
                total_power += p_loss
                power_details.append(f"{module.role}: {p_loss:.2f}W (Flyback损耗)")
            elif category == "sepic" and v_out is not None and i_out is not None:
                eff = 0.82
                p_out = v_out * i_out
                p_loss = p_out * (1 / eff - 1)
                total_power += p_loss
                power_details.append(f"{module.role}: {p_loss:.2f}W (SEPIC损耗)")
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
