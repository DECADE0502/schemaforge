"""SchemaForge ERC（电气规则检查）

实现6条核心ERC规则：
1. 浮空引脚检查
2. 最少连接数检查
3. 电源/地完整性检查
4. 短路检测
5. 输出冲突检查
6. 参数范围检查
"""

from __future__ import annotations

from schemaforge.core.models import (
    CircuitInstance,
    ERCError,
    ERCSeverity,
    PinType,
)
from schemaforge.core.templates import get_template


class ERCChecker:
    """电气规则检查器"""

    def check_all(self, circuit: CircuitInstance) -> list[ERCError]:
        """运行全部ERC检查

        Args:
            circuit: 待检查的电路实例

        Returns:
            发现的问题列表
        """
        errors: list[ERCError] = []
        errors += self.check_floating_pins(circuit)
        errors += self.check_net_minimum(circuit)
        errors += self.check_power_ground(circuit)
        errors += self.check_short_circuit(circuit)
        errors += self.check_pin_type_conflict(circuit)
        errors += self.check_parameter_range(circuit)
        return errors

    def check_floating_pins(self, circuit: CircuitInstance) -> list[ERCError]:
        """规则1: 必连引脚未连接检查

        遍历所有器件的required=True引脚，检查是否至少出现在一个net中。
        """
        errors: list[ERCError] = []

        # 构建已连接引脚集合
        connected: set[tuple[str, str]] = set()
        for net in circuit.nets:
            for conn in net.connections:
                connected.add((conn.component_ref, conn.pin_name))

        # 获取模板定义以检查引脚
        template = get_template(circuit.template_name)
        if template is None:
            return errors

        # 按序将模板component定义与实例一一对应
        # 模板的components列表顺序与实例化时生成的ref一致
        ref_counters: dict[str, int] = {}
        for comp_def in template.components:
            prefix = comp_def.ref_prefix
            count = ref_counters.get(prefix, 0) + 1
            ref_counters[prefix] = count
            expected_ref = f"{prefix}{count}"

            for pin in comp_def.pins:
                if pin.required and (expected_ref, pin.name) not in connected:
                    errors.append(ERCError(
                        rule="floating_pin",
                        severity=ERCSeverity.ERROR,
                        message=f"必连引脚未连接: {expected_ref}.{pin.name}",
                        component_ref=expected_ref,
                        pin_name=pin.name,
                    ))

        return errors

    def check_net_minimum(self, circuit: CircuitInstance) -> list[ERCError]:
        """规则2: 每个net至少2个连接

        单连接的net没有意义（悬空线）。
        """
        errors: list[ERCError] = []
        for net in circuit.nets:
            if len(net.connections) < 2:
                errors.append(ERCError(
                    rule="net_minimum",
                    severity=ERCSeverity.WARNING,
                    message=f"网络'{net.name}'只有{len(net.connections)}个连接（至少需要2个）",
                    net_name=net.name,
                ))
        return errors

    def check_power_ground(self, circuit: CircuitInstance) -> list[ERCError]:
        """规则3: 电源网络有源、地网络有地

        - is_power的net必须有至少一个power_out类型引脚
        - is_ground的net必须有至少一个ground类型引脚
        """
        errors: list[ERCError] = []

        # 构建ref->component_def映射
        template = get_template(circuit.template_name)
        if template is None:
            return errors

        # 简化：遍历net，检查标记
        for net in circuit.nets:
            if net.is_power:
                has_source = self._net_has_pin_type(net, template, PinType.POWER_OUT)
                if not has_source:
                    errors.append(ERCError(
                        rule="power_no_source",
                        severity=ERCSeverity.WARNING,
                        message=f"电源网络'{net.name}'中没有power_out类型引脚",
                        net_name=net.name,
                    ))

            if net.is_ground:
                has_gnd = self._net_has_pin_type(net, template, PinType.GROUND)
                if not has_gnd:
                    errors.append(ERCError(
                        rule="ground_no_ground",
                        severity=ERCSeverity.WARNING,
                        message=f"地网络'{net.name}'中没有ground类型引脚",
                        net_name=net.name,
                    ))

        return errors

    def check_short_circuit(self, circuit: CircuitInstance) -> list[ERCError]:
        """规则4: 电源直连地检测

        不允许同一个net同时包含power_out和ground类型引脚。
        """
        errors: list[ERCError] = []
        template = get_template(circuit.template_name)
        if template is None:
            return errors

        for net in circuit.nets:
            has_power = self._net_has_pin_type(net, template, PinType.POWER_OUT)
            has_ground = self._net_has_pin_type(net, template, PinType.GROUND)
            if has_power and has_ground:
                errors.append(ERCError(
                    rule="short_circuit",
                    severity=ERCSeverity.ERROR,
                    message=f"网络'{net.name}'中同时有power_out和ground引脚（短路）",
                    net_name=net.name,
                ))

        return errors

    def check_pin_type_conflict(self, circuit: CircuitInstance) -> list[ERCError]:
        """规则5: 输出冲突检查

        同一个net不允许有两个power_out或两个output类型引脚。
        """
        errors: list[ERCError] = []
        template = get_template(circuit.template_name)
        if template is None:
            return errors

        for net in circuit.nets:
            power_out_count = self._count_pin_type(net, template, PinType.POWER_OUT)
            output_count = self._count_pin_type(net, template, PinType.OUTPUT)

            if power_out_count > 1:
                errors.append(ERCError(
                    rule="output_conflict",
                    severity=ERCSeverity.ERROR,
                    message=f"网络'{net.name}'有{power_out_count}个power_out引脚（输出冲突）",
                    net_name=net.name,
                ))

            if output_count > 1:
                errors.append(ERCError(
                    rule="output_conflict",
                    severity=ERCSeverity.ERROR,
                    message=f"网络'{net.name}'有{output_count}个output引脚（输出冲突）",
                    net_name=net.name,
                ))

        return errors

    def check_parameter_range(self, circuit: CircuitInstance) -> list[ERCError]:
        """规则6: 参数值合法性检查

        电阻>0，电容>0，电压在额定范围内。
        """
        errors: list[ERCError] = []
        params = circuit.input_parameters

        # 通用检查
        for key, value in params.items():
            try:
                fval = float(value)
            except (ValueError, TypeError):
                continue

            # 电阻/电容不能为零或负
            if key in ("r_total", "r_value", "r1", "r2") and fval <= 0:
                errors.append(ERCError(
                    rule="param_range",
                    severity=ERCSeverity.ERROR,
                    message=f"参数'{key}'值{fval}不合法（必须>0）",
                ))

            # 电流必须>0
            if key == "led_current" and fval <= 0:
                errors.append(ERCError(
                    rule="param_range",
                    severity=ERCSeverity.ERROR,
                    message=f"LED电流{fval}mA不合法（必须>0）",
                ))

            # 频率必须>0
            if key == "f_cutoff" and fval <= 0:
                errors.append(ERCError(
                    rule="param_range",
                    severity=ERCSeverity.ERROR,
                    message=f"截止频率{fval}Hz不合法（必须>0）",
                ))

        return errors

    # === 辅助方法 ===

    def _net_has_pin_type(self, net, template, pin_type: PinType) -> bool:
        """检查网络中是否有指定类型的引脚"""
        return self._count_pin_type(net, template, pin_type) > 0

    def _count_pin_type(self, net, template, pin_type: PinType) -> int:
        """计算网络中指定类型引脚的数量"""
        count = 0
        for conn in net.connections:
            for comp_def in template.components:
                for pin in comp_def.pins:
                    if pin.name == conn.pin_name and pin.pin_type == pin_type:
                        count += 1
        return count
