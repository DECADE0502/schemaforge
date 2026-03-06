"""合理性检查模块

在设计规划后、渲染前进行电气合理性检查。

检查规则:
- 电压范围: v_in 是否在器件允许范围内
- 压差余量: LDO 的 v_in - v_out 是否满足最小压差
- 电流余量: 负载电流是否超过最大输出电流
- 功率预算: 器件功耗是否合理
- 参数完整性: 关键参数是否缺失
- 拓扑兼容性: 器件拓扑类型是否匹配需求

用法::

    checker = RationalityChecker()
    report = checker.check(device, parameters={"v_in": "12", "v_out": "3.3"})
    if report.has_errors:
        print("合理性检查失败:", report.summary())
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from schemaforge.library.models import DeviceModel


# ============================================================
# 检查结果
# ============================================================

class Severity:
    """严重级别常量"""
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass
class RationalityIssue:
    """合理性问题"""

    rule_id: str                    # 规则标识
    severity: str = Severity.WARNING  # info, warning, error
    message: str = ""               # 问题描述（中文）
    suggestion: str = ""            # 修复建议
    evidence: str = ""              # 依据

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "severity": self.severity,
            "message": self.message,
            "suggestion": self.suggestion,
            "evidence": self.evidence,
        }


@dataclass
class RationalityReport:
    """合理性检查报告"""

    issues: list[RationalityIssue] = field(default_factory=list)
    device_part_number: str = ""
    checked_params: dict[str, str] = field(default_factory=dict)

    @property
    def has_errors(self) -> bool:
        return any(i.severity == Severity.ERROR for i in self.issues)

    @property
    def has_warnings(self) -> bool:
        return any(i.severity == Severity.WARNING for i in self.issues)

    @property
    def errors(self) -> list[RationalityIssue]:
        return [i for i in self.issues if i.severity == Severity.ERROR]

    @property
    def warnings(self) -> list[RationalityIssue]:
        return [i for i in self.issues if i.severity == Severity.WARNING]

    @property
    def is_acceptable(self) -> bool:
        """无 error 即可接受（warning 允许通过）"""
        return not self.has_errors

    def summary(self) -> str:
        """生成摘要"""
        parts: list[str] = []
        for issue in self.issues:
            prefix = {"error": "❌", "warning": "⚠️", "info": "ℹ️"}.get(
                issue.severity, "?"
            )
            parts.append(f"{prefix} [{issue.rule_id}] {issue.message}")
        return "\n".join(parts) if parts else "✅ 合理性检查全部通过"

    def to_dict(self) -> dict[str, Any]:
        return {
            "device": self.device_part_number,
            "is_acceptable": self.is_acceptable,
            "error_count": len(self.errors),
            "warning_count": len(self.warnings),
            "issues": [i.to_dict() for i in self.issues],
        }


# ============================================================
# 合理性检查器
# ============================================================

class RationalityChecker:
    """合理性检查器

    对单个器件 + 参数进行电气合理性检查。
    """

    def check(
        self,
        device: DeviceModel,
        parameters: dict[str, str] | None = None,
    ) -> RationalityReport:
        """执行完整合理性检查

        Args:
            device: 器件模型
            parameters: 设计参数

        Returns:
            RationalityReport
        """
        params = parameters or {}
        report = RationalityReport(
            device_part_number=device.part_number,
            checked_params=params,
        )

        # 执行所有检查规则
        self._check_topology_exists(device, report)
        self._check_voltage_range(device, params, report)
        self._check_dropout_voltage(device, params, report)
        self._check_current_limit(device, params, report)
        self._check_power_dissipation(device, params, report)
        self._check_parameter_completeness(device, params, report)

        return report

    def check_multi(
        self,
        modules: list[tuple[DeviceModel, dict[str, str]]],
    ) -> list[RationalityReport]:
        """批量检查多个模块

        Args:
            modules: [(device, parameters), ...]

        Returns:
            报告列表
        """
        return [self.check(device, params) for device, params in modules]

    # ----------------------------------------------------------
    # 检查规则
    # ----------------------------------------------------------

    def _check_topology_exists(
        self,
        device: DeviceModel,
        report: RationalityReport,
    ) -> None:
        """检查器件是否有拓扑定义"""
        if device.topology is None:
            report.issues.append(RationalityIssue(
                rule_id="TOPO_MISSING",
                severity=Severity.ERROR,
                message=f"器件 {device.part_number} 没有拓扑定义，无法生成原理图",
                suggestion="选择一个有拓扑定义的器件，或手动为该器件添加拓扑",
            ))

    def _check_voltage_range(
        self,
        device: DeviceModel,
        params: dict[str, str],
        report: RationalityReport,
    ) -> None:
        """检查输入电压是否在器件允许范围内"""
        v_in_str = params.get("v_in", "")
        v_in_max_str = device.specs.get("v_in_max", "")

        if not v_in_str or not v_in_max_str:
            return

        v_in = _parse_number(v_in_str)
        v_in_max = _parse_number(v_in_max_str)

        if v_in is None or v_in_max is None:
            return

        if v_in > v_in_max:
            report.issues.append(RationalityIssue(
                rule_id="VIN_OVER_MAX",
                severity=Severity.ERROR,
                message=(
                    f"输入电压 {v_in}V 超过器件最大额定值 {v_in_max}V"
                ),
                suggestion=f"降低输入电压至 {v_in_max}V 以下，或更换耐压更高的器件",
                evidence=f"spec: v_in_max={v_in_max_str}",
            ))
        elif v_in > v_in_max * 0.9:
            report.issues.append(RationalityIssue(
                rule_id="VIN_NEAR_MAX",
                severity=Severity.WARNING,
                message=(
                    f"输入电压 {v_in}V 接近器件最大额定值 {v_in_max}V（裕量 < 10%）"
                ),
                suggestion="建议留出 10% 以上裕量，考虑电压瞬变",
                evidence=f"spec: v_in_max={v_in_max_str}",
            ))

    def _check_dropout_voltage(
        self,
        device: DeviceModel,
        params: dict[str, str],
        report: RationalityReport,
    ) -> None:
        """检查 LDO 压差余量"""
        if device.category != "ldo":
            return

        v_in_str = params.get("v_in", "")
        v_out_str = params.get("v_out", "")
        v_dropout_str = device.specs.get("v_dropout", "")

        if not v_in_str or not v_out_str:
            return

        v_in = _parse_number(v_in_str)
        v_out = _parse_number(v_out_str)
        v_dropout = _parse_number(v_dropout_str) if v_dropout_str else None

        if v_in is None or v_out is None:
            return

        if v_out >= v_in:
            report.issues.append(RationalityIssue(
                rule_id="VOUT_GE_VIN",
                severity=Severity.ERROR,
                message=f"输出电压 {v_out}V ≥ 输入电压 {v_in}V，LDO 无法工作",
                suggestion="输出电压必须低于输入电压",
            ))
            return

        if v_dropout is not None:
            actual_dropout = v_in - v_out
            if actual_dropout < v_dropout:
                report.issues.append(RationalityIssue(
                    rule_id="DROPOUT_INSUFFICIENT",
                    severity=Severity.ERROR,
                    message=(
                        f"输入输出压差 {actual_dropout:.1f}V "
                        f"< 最小压差 {v_dropout}V，LDO 无法正常稳压"
                    ),
                    suggestion=(
                        f"增大输入电压至 {v_out + v_dropout:.1f}V 以上，"
                        f"或选择更低压差的 LDO"
                    ),
                    evidence=f"spec: v_dropout={v_dropout_str}",
                ))
            elif actual_dropout < v_dropout * 1.5:
                report.issues.append(RationalityIssue(
                    rule_id="DROPOUT_MARGINAL",
                    severity=Severity.WARNING,
                    message=(
                        f"输入输出压差 {actual_dropout:.1f}V "
                        f"仅略大于最小压差 {v_dropout}V，裕量不足"
                    ),
                    suggestion="建议输入电压比输出高 1.5× 最小压差以上",
                    evidence=f"spec: v_dropout={v_dropout_str}",
                ))

    def _check_current_limit(
        self,
        device: DeviceModel,
        params: dict[str, str],
        report: RationalityReport,
    ) -> None:
        """检查输出电流是否超限"""
        i_out_str = params.get("i_out", "")
        i_max_str = device.specs.get("i_out_max", "")

        if not i_out_str or not i_max_str:
            return

        i_out = _parse_number(i_out_str)
        i_max = _parse_number(i_max_str)

        if i_out is None or i_max is None:
            return

        if i_out > i_max:
            report.issues.append(RationalityIssue(
                rule_id="IOUT_OVER_MAX",
                severity=Severity.ERROR,
                message=f"输出电流 {i_out}A 超过最大额定值 {i_max}A",
                suggestion="降低负载电流或选择更大电流能力的器件",
                evidence=f"spec: i_out_max={i_max_str}",
            ))
        elif i_out > i_max * 0.8:
            report.issues.append(RationalityIssue(
                rule_id="IOUT_NEAR_MAX",
                severity=Severity.WARNING,
                message=f"输出电流 {i_out}A 接近最大额定值 {i_max}A（裕量 < 20%）",
                suggestion="建议留出 20% 电流裕量以保证可靠性",
                evidence=f"spec: i_out_max={i_max_str}",
            ))

    def _check_power_dissipation(
        self,
        device: DeviceModel,
        params: dict[str, str],
        report: RationalityReport,
    ) -> None:
        """检查 LDO 功耗"""
        if device.category != "ldo":
            return

        v_in_str = params.get("v_in", "")
        v_out_str = params.get("v_out", "")
        i_out_str = params.get("i_out", "")

        if not v_in_str or not v_out_str or not i_out_str:
            return

        v_in = _parse_number(v_in_str)
        v_out = _parse_number(v_out_str)
        i_out = _parse_number(i_out_str)

        if v_in is None or v_out is None or i_out is None:
            return

        power = (v_in - v_out) * i_out

        # SOT-223 典型散热能力约 1.5W（无散热片）
        if power > 2.0:
            report.issues.append(RationalityIssue(
                rule_id="POWER_EXCESSIVE",
                severity=Severity.ERROR,
                message=f"LDO 功耗 {power:.2f}W 过高，可能导致过热",
                suggestion=(
                    "降低输入电压、减小负载电流，或改用 Buck 降压方案"
                ),
                evidence=f"P = ({v_in}V - {v_out}V) × {i_out}A = {power:.2f}W",
            ))
        elif power > 1.0:
            report.issues.append(RationalityIssue(
                rule_id="POWER_HIGH",
                severity=Severity.WARNING,
                message=f"LDO 功耗 {power:.2f}W 较高，需要注意散热",
                suggestion="确保 PCB 有充足散热面积，或考虑加装散热片",
                evidence=f"P = ({v_in}V - {v_out}V) × {i_out}A = {power:.2f}W",
            ))

    def _check_parameter_completeness(
        self,
        device: DeviceModel,
        params: dict[str, str],
        report: RationalityReport,
    ) -> None:
        """检查关键参数是否完整"""
        if device.topology is None:
            return

        # 从拓扑参数定义中获取必需参数
        for param_name, param_def in device.topology.parameters.items():
            if param_name not in params:
                # 有默认值的不告警
                if param_def.default:
                    continue
                report.issues.append(RationalityIssue(
                    rule_id="PARAM_MISSING",
                    severity=Severity.INFO,
                    message=f"参数 {param_name} 未指定，将使用默认行为",
                    suggestion=f"建议明确指定 {param_def.display_name or param_name}",
                ))


# ============================================================
# 工具函数
# ============================================================

def _parse_number(text: str) -> float | None:
    """从文本中解析数值

    "3.3V" → 3.3
    "1A" → 1.0
    "1.1" → 1.1
    """
    m = re.search(r"[-+]?\d*\.?\d+", text.strip())
    if m:
        try:
            return float(m.group())
        except ValueError:
            return None
    return None
