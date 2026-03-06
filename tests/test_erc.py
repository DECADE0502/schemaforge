"""测试ERC电气规则检查"""

import pytest
from schemaforge.core.erc import ERCChecker
from schemaforge.core.models import (
    CircuitInstance,
    ComponentInstance,
    Net,
    NetConnection,
)


@pytest.fixture
def checker():
    return ERCChecker()


class TestNetMinimum:
    def test_valid_net(self, checker):
        circuit = CircuitInstance(
            name="test",
            template_name="voltage_divider",
            nets=[
                Net(
                    name="VMID",
                    connections=[
                        NetConnection(component_ref="R1", pin_name="2"),
                        NetConnection(component_ref="R2", pin_name="1"),
                    ],
                ),
            ],
        )
        errors = checker.check_net_minimum(circuit)
        assert len(errors) == 0

    def test_single_connection_net(self, checker):
        circuit = CircuitInstance(
            name="test",
            template_name="voltage_divider",
            nets=[
                Net(
                    name="VIN",
                    connections=[
                        NetConnection(component_ref="R1", pin_name="1"),
                    ],
                ),
            ],
        )
        errors = checker.check_net_minimum(circuit)
        assert len(errors) == 1
        assert errors[0].rule == "net_minimum"


class TestParameterRange:
    def test_valid_params(self, checker):
        circuit = CircuitInstance(
            name="test",
            template_name="voltage_divider",
            input_parameters={"r_total": "20", "v_in": "5", "v_out": "2.5"},
        )
        errors = checker.check_parameter_range(circuit)
        assert len(errors) == 0

    def test_zero_resistance(self, checker):
        circuit = CircuitInstance(
            name="test",
            template_name="voltage_divider",
            input_parameters={"r_total": "0"},
        )
        errors = checker.check_parameter_range(circuit)
        assert len(errors) == 1
        assert errors[0].rule == "param_range"

    def test_negative_current(self, checker):
        circuit = CircuitInstance(
            name="test",
            template_name="led_indicator",
            input_parameters={"led_current": "-5"},
        )
        errors = checker.check_parameter_range(circuit)
        assert len(errors) == 1

    def test_zero_frequency(self, checker):
        circuit = CircuitInstance(
            name="test",
            template_name="rc_lowpass",
            input_parameters={"f_cutoff": "0"},
        )
        errors = checker.check_parameter_range(circuit)
        assert len(errors) == 1


class TestShortCircuit:
    def test_no_short(self, checker):
        circuit = CircuitInstance(
            name="test",
            template_name="voltage_divider",
            nets=[
                Net(
                    name="VIN",
                    connections=[
                        NetConnection(component_ref="R1", pin_name="1"),
                    ],
                    is_power=True,
                ),
            ],
        )
        errors = checker.check_short_circuit(circuit)
        assert len(errors) == 0


class TestEngineERC:
    def test_divider_no_errors(self, checker):
        """分压器模板应该没有ERC错误"""
        from schemaforge.core.engine import SchemaForgeEngine
        engine = SchemaForgeEngine(use_mock=True)
        result = engine.process("分压采样电路")
        erc_errors = [e for e in result.erc_errors if e.severity.value == "error"]
        assert len(erc_errors) == 0

    def test_ldo_no_errors(self, checker):
        """LDO模板应该没有ERC错误"""
        from schemaforge.core.engine import SchemaForgeEngine
        engine = SchemaForgeEngine(use_mock=True)
        result = engine.process("5V转3.3V稳压")
        erc_errors = [e for e in result.erc_errors if e.severity.value == "error"]
        assert len(erc_errors) == 0
