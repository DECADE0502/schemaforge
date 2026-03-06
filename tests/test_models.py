"""测试核心数据模型"""

import pytest
from schemaforge.core.models import (
    CircuitInstance,
    CircuitTemplate,
    ComponentDef,
    ComponentInstance,
    ConnectionSpec,
    DesignSpec,
    ERCError,
    ERCSeverity,
    LayoutHint,
    ModuleSpec,
    Net,
    NetConnection,
    ParameterDef,
    PinDef,
    PinType,
)


class TestPinType:
    def test_pin_types_exist(self):
        assert PinType.POWER_IN == "power_in"
        assert PinType.POWER_OUT == "power_out"
        assert PinType.GROUND == "ground"
        assert PinType.PASSIVE == "passive"

    def test_pin_type_values(self):
        assert len(PinType) == 8


class TestPinDef:
    def test_create_pin(self):
        pin = PinDef(name="VIN", pin_type=PinType.POWER_IN)
        assert pin.name == "VIN"
        assert pin.pin_type == PinType.POWER_IN
        assert pin.required is True

    def test_optional_pin(self):
        pin = PinDef(name="NC", pin_type=PinType.NO_CONNECT, required=False)
        assert pin.required is False


class TestComponentDef:
    def test_create_component(self):
        comp = ComponentDef(
            ref_prefix="R",
            name="电阻",
            pins=[
                PinDef(name="1", pin_type=PinType.PASSIVE),
                PinDef(name="2", pin_type=PinType.PASSIVE),
            ],
        )
        assert comp.ref_prefix == "R"
        assert len(comp.pins) == 2


class TestNet:
    def test_create_net(self):
        net = Net(
            name="VCC",
            connections=[
                NetConnection(component_ref="R1", pin_name="1"),
                NetConnection(component_ref="U1", pin_name="VIN"),
            ],
            is_power=True,
        )
        assert net.name == "VCC"
        assert len(net.connections) == 2
        assert net.is_power is True


class TestCircuitInstance:
    def test_create_instance(self):
        ci = CircuitInstance(
            name="test_circuit",
            components=[
                ComponentInstance(ref="R1", component_type="电阻", parameters={"value": "10kΩ"}),
            ],
            nets=[
                Net(name="VIN", connections=[NetConnection(component_ref="R1", pin_name="1")]),
            ],
            template_name="voltage_divider",
            input_parameters={"v_in": "5", "v_out": "2.5"},
        )
        assert ci.name == "test_circuit"
        assert len(ci.components) == 1
        assert len(ci.nets) == 1


class TestDesignSpec:
    def test_create_design(self):
        spec = DesignSpec(
            design_name="测试设计",
            modules=[
                ModuleSpec(
                    template="voltage_divider",
                    instance_name="div1",
                    parameters={"v_in": "5", "v_out": "2.5"},
                ),
            ],
        )
        assert spec.design_name == "测试设计"
        assert len(spec.modules) == 1

    def test_design_with_connections(self):
        spec = DesignSpec(
            design_name="组合设计",
            modules=[
                ModuleSpec(template="ldo_regulator", instance_name="ldo1"),
                ModuleSpec(template="led_indicator", instance_name="led1"),
            ],
            connections=[
                ConnectionSpec(
                    from_module="ldo1", from_net="VOUT",
                    to_module="led1", to_net="VCC",
                    merged_net_name="VOUT_3V3",
                ),
            ],
        )
        assert len(spec.connections) == 1


class TestERCError:
    def test_create_error(self):
        err = ERCError(
            rule="floating_pin",
            severity=ERCSeverity.ERROR,
            message="必连引脚未连接",
        )
        assert err.rule == "floating_pin"
        assert err.severity == ERCSeverity.ERROR
