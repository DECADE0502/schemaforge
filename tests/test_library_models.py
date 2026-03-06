"""SchemaForge 器件库模型测试

测试 SymbolPin, SymbolDef, TopologyDef, DeviceModel 等数据模型的
创建、序列化、反序列化。
"""

from __future__ import annotations

import json


from schemaforge.core.models import ParameterDef, PinType
from schemaforge.library.models import (
    DeviceModel,
    ExternalComponent,
    PinSide,
    SymbolDef,
    SymbolPin,
    TopologyConnection,
    TopologyDef,
)


# ============================================================
# SymbolPin 测试
# ============================================================

class TestSymbolPin:
    """SymbolPin 模型测试"""

    def test_create_basic_pin(self) -> None:
        """创建基本引脚"""
        pin = SymbolPin(name="VIN")
        assert pin.name == "VIN"
        assert pin.side == PinSide.LEFT
        assert pin.pin_type == PinType.PASSIVE
        assert pin.pin_number == ""
        assert pin.inverted is False

    def test_create_power_pin(self) -> None:
        """创建电源输入引脚"""
        pin = SymbolPin(
            name="VCC",
            pin_number="1",
            side=PinSide.TOP,
            pin_type=PinType.POWER_IN,
            slot="1/2",
            description="电源输入",
        )
        assert pin.name == "VCC"
        assert pin.pin_number == "1"
        assert pin.side == PinSide.TOP
        assert pin.pin_type == PinType.POWER_IN
        assert pin.slot == "1/2"
        assert pin.description == "电源输入"

    def test_pin_serialization(self) -> None:
        """引脚序列化与反序列化"""
        pin = SymbolPin(
            name="GND",
            pin_number="4",
            side=PinSide.BOTTOM,
            pin_type=PinType.GROUND,
        )
        data = pin.model_dump()
        assert data["name"] == "GND"
        assert data["side"] == "bottom"
        assert data["pin_type"] == "ground"

        restored = SymbolPin.model_validate(data)
        assert restored == pin

    def test_pin_json_roundtrip(self) -> None:
        """引脚 JSON 往返"""
        pin = SymbolPin(
            name="OUT",
            side=PinSide.RIGHT,
            pin_type=PinType.OUTPUT,
            inverted=True,
            anchor_name="out_pin",
        )
        json_str = pin.model_dump_json()
        restored = SymbolPin.model_validate_json(json_str)
        assert restored == pin
        assert restored.inverted is True
        assert restored.anchor_name == "out_pin"


# ============================================================
# SymbolDef 测试
# ============================================================

class TestSymbolDef:
    """SymbolDef 模型测试"""

    def test_create_symbol(self) -> None:
        """创建基本符号定义"""
        sym = SymbolDef(
            pins=[
                SymbolPin(name="VIN", side=PinSide.LEFT, pin_type=PinType.POWER_IN),
                SymbolPin(name="VOUT", side=PinSide.RIGHT, pin_type=PinType.POWER_OUT),
                SymbolPin(name="GND", side=PinSide.BOTTOM, pin_type=PinType.GROUND),
            ],
            size=(4, 3),
        )
        assert len(sym.pins) == 3
        assert sym.size == (4, 3)
        assert sym.edge_pad_w == 0.5
        assert sym.lead_len == 0.5

    def test_symbol_without_size(self) -> None:
        """符号可以不指定尺寸"""
        sym = SymbolDef(pins=[SymbolPin(name="A")])
        assert sym.size is None

    def test_symbol_serialization(self) -> None:
        """符号序列化"""
        sym = SymbolDef(
            pins=[
                SymbolPin(name="IN", side=PinSide.LEFT),
                SymbolPin(name="OUT", side=PinSide.RIGHT),
            ],
            size=(3, 2),
            pin_spacing=1.5,
        )
        data = sym.model_dump()
        assert len(data["pins"]) == 2
        assert data["size"] == (3, 2)
        assert data["pin_spacing"] == 1.5

    def test_symbol_to_icpin_kwargs(self) -> None:
        """从 SymbolPin 生成 schemdraw IcPin 兼容的参数字典"""
        pin = SymbolPin(
            name="VIN",
            pin_number="3",
            side=PinSide.LEFT,
            slot="1/3",
            inverted=True,
        )
        # 辅助函数：将 SymbolPin 转为 IcPin kwargs
        kwargs = _symbol_pin_to_icpin_kwargs(pin)
        assert kwargs["name"] == "VIN"
        assert kwargs["pin"] == "3"
        assert kwargs["side"] == "left"
        assert kwargs["slot"] == "1/3"
        assert kwargs["invert"] is True

    def test_symbol_all_pins_to_icpin(self) -> None:
        """批量转换符号中所有引脚"""
        sym = SymbolDef(
            pins=[
                SymbolPin(name="VIN", side=PinSide.LEFT, pin_number="3", slot="1/3"),
                SymbolPin(name="VOUT", side=PinSide.RIGHT, pin_number="2", slot="1/3"),
                SymbolPin(name="GND", side=PinSide.BOTTOM, pin_number="1", slot="1/1"),
            ],
        )
        all_kwargs = [_symbol_pin_to_icpin_kwargs(p) for p in sym.pins]
        assert len(all_kwargs) == 3
        sides = {k["side"] for k in all_kwargs}
        assert sides == {"left", "right", "bottom"}


# ============================================================
# TopologyDef 测试
# ============================================================

class TestTopologyDef:
    """TopologyDef 模型测试"""

    def test_create_topology(self) -> None:
        """创建基本拓扑"""
        topo = TopologyDef(circuit_type="ldo")
        assert topo.circuit_type == "ldo"
        assert topo.external_components == []
        assert topo.connections == []

    def test_topology_with_components(self) -> None:
        """拓扑包含外部器件和连接"""
        topo = TopologyDef(
            circuit_type="ldo",
            external_components=[
                ExternalComponent(
                    role="input_cap",
                    ref_prefix="C",
                    default_value="10uF",
                    schemdraw_element="Capacitor",
                ),
            ],
            connections=[
                TopologyConnection(
                    net_name="VIN",
                    device_pin="VIN",
                    external_refs=["input_cap.1"],
                    is_power=True,
                ),
                TopologyConnection(
                    net_name="GND",
                    device_pin="GND",
                    external_refs=["input_cap.2"],
                    is_ground=True,
                ),
            ],
        )
        assert len(topo.external_components) == 1
        assert topo.external_components[0].role == "input_cap"
        assert len(topo.connections) == 2
        assert topo.connections[0].is_power is True
        assert topo.connections[1].is_ground is True

    def test_topology_with_parameters(self) -> None:
        """拓扑包含参数和计算"""
        topo = TopologyDef(
            circuit_type="voltage_divider",
            parameters={
                "v_in": ParameterDef(name="v_in", type="float", unit="V", default="5"),
            },
            calculations={"ratio": "v_out / v_in"},
            design_rules=["v_out < v_in"],
        )
        assert "v_in" in topo.parameters
        assert topo.calculations["ratio"] == "v_out / v_in"
        assert len(topo.design_rules) == 1

    def test_topology_json_roundtrip(self) -> None:
        """拓扑 JSON 往返"""
        topo = TopologyDef(
            circuit_type="rc_filter",
            external_components=[
                ExternalComponent(
                    role="filter_r",
                    ref_prefix="R",
                    default_value="10k",
                    schemdraw_element="Resistor",
                ),
            ],
            connections=[
                TopologyConnection(net_name="IN", external_refs=["filter_r.1"]),
            ],
        )
        json_str = topo.model_dump_json()
        restored = TopologyDef.model_validate_json(json_str)
        assert restored.circuit_type == "rc_filter"
        assert len(restored.external_components) == 1
        assert restored.external_components[0].role == "filter_r"


# ============================================================
# ExternalComponent 测试
# ============================================================

class TestExternalComponent:
    """ExternalComponent 模型测试"""

    def test_create_component(self) -> None:
        """创建外部器件"""
        comp = ExternalComponent(
            role="output_cap",
            ref_prefix="C",
            default_value="22uF",
            value_expression="{c_out}",
            constraints={"voltage_rating": ">= v_out * 1.5"},
            schemdraw_element="Capacitor",
        )
        assert comp.role == "output_cap"
        assert comp.required is True
        assert comp.constraints["voltage_rating"] == ">= v_out * 1.5"

    def test_optional_component(self) -> None:
        """可选外部器件"""
        comp = ExternalComponent(
            role="feedback_cap",
            ref_prefix="C",
            required=False,
            default_value="100pF",
        )
        assert comp.required is False


# ============================================================
# DeviceModel 测试
# ============================================================

class TestDeviceModel:
    """DeviceModel 模型测试"""

    def test_create_minimal(self) -> None:
        """创建最小器件模型（仅料号）"""
        dev = DeviceModel(part_number="R0402_10K")
        assert dev.part_number == "R0402_10K"
        assert dev.category == ""
        assert dev.symbol is None
        assert dev.topology is None
        assert dev.source == "manual"
        assert dev.confidence == 1.0

    def test_create_full_device(self) -> None:
        """创建完整器件模型"""
        dev = DeviceModel(
            part_number="AMS1117-3.3",
            manufacturer="AMS",
            description="LDO 3.3V",
            category="ldo",
            specs={"v_out": "3.3V"},
            symbol=SymbolDef(
                pins=[
                    SymbolPin(name="VIN", side=PinSide.LEFT, pin_type=PinType.POWER_IN),
                    SymbolPin(name="VOUT", side=PinSide.RIGHT, pin_type=PinType.POWER_OUT),
                    SymbolPin(name="GND", side=PinSide.BOTTOM, pin_type=PinType.GROUND),
                ],
                size=(4, 3),
            ),
            topology=TopologyDef(circuit_type="ldo"),
            lcsc_part="C347222",
            package="SOT-223",
        )
        assert dev.manufacturer == "AMS"
        assert dev.symbol is not None
        assert len(dev.symbol.pins) == 3
        assert dev.topology is not None
        assert dev.lcsc_part == "C347222"

    def test_json_roundtrip(self) -> None:
        """DeviceModel JSON 完整往返"""
        dev = DeviceModel(
            part_number="TEST-IC-001",
            manufacturer="TestCorp",
            description="测试IC",
            category="mcu",
            specs={"clock": "48MHz", "flash": "256KB"},
            symbol=SymbolDef(
                pins=[
                    SymbolPin(name="VDD", side=PinSide.TOP, pin_type=PinType.POWER_IN, pin_number="1"),
                    SymbolPin(name="GND", side=PinSide.BOTTOM, pin_type=PinType.GROUND, pin_number="2"),
                    SymbolPin(name="PA0", side=PinSide.LEFT, pin_type=PinType.BIDIRECTIONAL, pin_number="3"),
                    SymbolPin(name="PA1", side=PinSide.RIGHT, pin_type=PinType.BIDIRECTIONAL, pin_number="4"),
                ],
                size=(6, 4),
                pin_spacing=1.5,
            ),
            topology=TopologyDef(
                circuit_type="mcu",
                external_components=[
                    ExternalComponent(
                        role="bypass_cap",
                        ref_prefix="C",
                        default_value="100nF",
                        schemdraw_element="Capacitor",
                    ),
                ],
                connections=[
                    TopologyConnection(
                        net_name="VDD",
                        device_pin="VDD",
                        external_refs=["bypass_cap.1"],
                        is_power=True,
                    ),
                    TopologyConnection(
                        net_name="GND",
                        device_pin="GND",
                        external_refs=["bypass_cap.2"],
                        is_ground=True,
                    ),
                ],
            ),
            spice_model="X{ref} subckt_test",
            lcsc_part="C12345",
            package="QFP-48",
            source="manual",
            confidence=0.95,
            notes="测试用器件",
        )

        json_str = dev.model_dump_json()
        parsed = json.loads(json_str)
        assert parsed["part_number"] == "TEST-IC-001"
        assert len(parsed["symbol"]["pins"]) == 4
        assert parsed["topology"]["circuit_type"] == "mcu"

        restored = DeviceModel.model_validate_json(json_str)
        assert restored.part_number == dev.part_number
        assert restored.manufacturer == dev.manufacturer
        assert restored.symbol is not None
        assert len(restored.symbol.pins) == 4
        assert restored.topology is not None
        assert len(restored.topology.external_components) == 1
        assert restored.confidence == 0.95

    def test_passive_device_no_symbol(self) -> None:
        """无源器件可以没有符号和拓扑"""
        dev = DeviceModel(
            part_number="GRM155R71C104KA88",
            manufacturer="Murata",
            description="100nF 0402 MLCC",
            category="capacitor",
            specs={"capacitance": "100nF", "voltage": "16V"},
            package="0402",
            lcsc_part="C14663",
        )
        assert dev.symbol is None
        assert dev.topology is None

    def test_model_dump_dict(self) -> None:
        """model_dump 生成字典"""
        dev = DeviceModel(
            part_number="X",
            category="test",
            specs={"a": "1"},
        )
        d = dev.model_dump()
        assert isinstance(d, dict)
        assert d["part_number"] == "X"
        assert d["specs"] == {"a": "1"}

    def test_source_field_values(self) -> None:
        """source字段支持多种来源标识"""
        for src in ["manual", "pdf_parsed", "easyeda", "digikey", "migrated"]:
            dev = DeviceModel(part_number="T", source=src)
            assert dev.source == src


# ============================================================
# 辅助函数
# ============================================================

def _symbol_pin_to_icpin_kwargs(pin: SymbolPin) -> dict:
    """将 SymbolPin 转为 schemdraw elm.IcPin 兼容的关键字参数

    这个辅助函数演示了从序列化的 SymbolPin 重建 schemdraw IcPin 的方法。
    """
    kwargs: dict = {
        "name": pin.name,
        "side": pin.side.value,
    }
    if pin.pin_number:
        kwargs["pin"] = pin.pin_number
    if pin.slot:
        kwargs["slot"] = pin.slot
    if pin.inverted:
        kwargs["invert"] = True
    if pin.anchor_name:
        kwargs["anchorname"] = pin.anchor_name
    return kwargs
