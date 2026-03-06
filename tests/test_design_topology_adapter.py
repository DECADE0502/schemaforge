"""拓扑适配器测试"""

from __future__ import annotations

from pathlib import Path

from schemaforge.core.models import PinType
from schemaforge.design.topology_adapter import (
    AdaptedModule,
    TopologyAdapter,
    _strip_unit,
)
from schemaforge.library.models import (
    DeviceModel,
    ExternalComponent,
    SymbolDef,
    SymbolPin,
    TopologyConnection,
    TopologyDef,
)
from schemaforge.core.models import ParameterDef


def _make_ldo_device() -> DeviceModel:
    """创建测试用 LDO DeviceModel"""
    return DeviceModel(
        part_number="AMS1117-3.3",
        manufacturer="AMS",
        description="LDO线性稳压器 3.3V 1A SOT-223",
        category="ldo",
        specs={
            "v_out": "3.3V",
            "v_dropout": "1.1V",
            "i_out_max": "1A",
            "v_in_max": "15V",
        },
        symbol=SymbolDef(
            pins=[
                SymbolPin(
                    name="VIN", pin_number="3",
                    side="left", pin_type=PinType.POWER_IN,
                    slot="1/3",
                ),
                SymbolPin(
                    name="VOUT", pin_number="2",
                    side="right", pin_type=PinType.POWER_OUT,
                    slot="1/3",
                ),
                SymbolPin(
                    name="GND", pin_number="1",
                    side="bottom", pin_type=PinType.GROUND,
                    slot="1/1",
                ),
            ],
            size=(4.0, 3.0),
        ),
        topology=TopologyDef(
            circuit_type="ldo",
            external_components=[
                ExternalComponent(
                    role="input_cap",
                    ref_prefix="C",
                    default_value="10uF",
                    value_expression="{c_in}",
                    schemdraw_element="Capacitor",
                ),
                ExternalComponent(
                    role="output_cap",
                    ref_prefix="C",
                    default_value="22uF",
                    value_expression="{c_out}",
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
                    net_name="VOUT",
                    device_pin="VOUT",
                    external_refs=["output_cap.1"],
                    is_power=True,
                ),
                TopologyConnection(
                    net_name="GND",
                    device_pin="GND",
                    external_refs=["input_cap.2", "output_cap.2"],
                    is_ground=True,
                ),
            ],
            parameters={
                "v_in": ParameterDef(name="v_in", default="5", unit="V"),
                "c_in": ParameterDef(name="c_in", default="10uF"),
                "c_out": ParameterDef(name="c_out", default="22uF"),
            },
        ),
        package="SOT-223",
    )


def _make_no_topology_device() -> DeviceModel:
    """创建无拓扑的器件"""
    return DeviceModel(
        part_number="GENERIC_IC",
        description="通用IC无拓扑",
        category="other",
    )


class TestTopologyAdapter:
    """拓扑适配器测试"""

    def setup_method(self) -> None:
        self.adapter = TopologyAdapter()
        self.ldo = _make_ldo_device()

    # --- adapt_single ---

    def test_adapt_single_basic(self) -> None:
        adapted = self.adapter.adapt_single(self.ldo, {"v_in": "5", "v_out": "3.3"})
        assert isinstance(adapted, AdaptedModule)
        assert adapted.device.part_number == "AMS1117-3.3"
        assert adapted.render_params["v_in"] == "5"
        assert adapted.render_params["v_out"] == "3.3"

    def test_adapt_single_default_params(self) -> None:
        adapted = self.adapter.adapt_single(self.ldo)
        # 应该使用拓扑默认参数
        assert adapted.render_params.get("c_in") == "10uF"
        assert adapted.render_params.get("c_out") == "22uF"
        assert adapted.render_params.get("ic_model") == "AMS1117-3.3"

    def test_adapt_single_user_overrides(self) -> None:
        adapted = self.adapter.adapt_single(
            self.ldo, {"c_in": "100uF", "c_out": "47uF"},
        )
        assert adapted.render_params["c_in"] == "100uF"
        assert adapted.render_params["c_out"] == "47uF"

    def test_adapt_single_no_topology_raises(self) -> None:
        device = _make_no_topology_device()
        try:
            self.adapter.adapt_single(device)
            assert False, "应该抛出 ValueError"
        except ValueError as e:
            assert "没有拓扑定义" in str(e)

    def test_adapt_single_role(self) -> None:
        adapted = self.adapter.adapt_single(self.ldo, role="main_regulator")
        assert adapted.role == "main_regulator"

    # --- to_design_spec_module ---

    def test_to_design_spec_module(self) -> None:
        adapted = self.adapter.adapt_single(
            self.ldo, {"v_in": "5", "v_out": "3.3"}, role="main_ldo",
        )
        spec = adapted.to_design_spec_module()
        assert spec["template"] == "ldo_regulator"
        assert spec["instance_name"] == "main_ldo"
        assert spec["parameters"]["v_in"] == "5"

    # --- adapt_multi ---

    def test_adapt_multi(self) -> None:
        result = self.adapter.adapt_multi(
            modules=[
                (self.ldo, {"v_in": "5", "v_out": "3.3"}, "main_ldo"),
            ],
            design_name="测试设计",
        )
        assert len(result.modules) == 1
        assert result.design_name == "测试设计"

    def test_adapt_multi_skips_no_topology(self) -> None:
        no_topo = _make_no_topology_device()
        result = self.adapter.adapt_multi(
            modules=[
                (self.ldo, {}, "ldo"),
                (no_topo, {}, "generic"),
            ],
        )
        assert len(result.modules) == 1  # 只有 ldo 成功
        assert "跳过" in result.notes

    def test_adapt_multi_to_design_spec(self) -> None:
        result = self.adapter.adapt_multi(
            modules=[(self.ldo, {"v_in": "5"}, "main")],
            design_name="Test",
            description="A test",
        )
        spec = result.to_design_spec()
        assert spec["design_name"] == "Test"
        assert len(spec["modules"]) == 1

    # --- render ---

    def test_render_produces_svg(self) -> None:
        # 使用真实 store 中的设备数据（来自 _make_ldo_device）
        svg_path = self.adapter.render(
            self.ldo, {"v_in": "5", "v_out": "3.3"},
        )
        assert svg_path.endswith(".svg")
        assert Path(svg_path).exists()

    # --- build_circuit_instance ---

    def test_build_circuit_instance(self) -> None:
        circuit = self.adapter.build_circuit_instance(
            self.ldo, {"v_in": "5", "v_out": "3.3"}, role="test_ldo",
        )
        assert circuit.name == "test_ldo"
        # U1 + 2 caps = 3 components
        assert len(circuit.components) == 3
        assert circuit.components[0].ref == "U1"
        assert circuit.components[0].component_type == "AMS1117-3.3"

    def test_build_circuit_instance_nets(self) -> None:
        circuit = self.adapter.build_circuit_instance(self.ldo)
        net_names = {n.name for n in circuit.nets}
        assert "VIN" in net_names
        assert "VOUT" in net_names
        assert "GND" in net_names

    # --- generate_exports ---

    def test_generate_exports(self) -> None:
        bom, spice = self.adapter.generate_exports(
            self.ldo, {"v_in": "5", "v_out": "3.3"},
        )
        assert "AMS1117-3.3" in bom or "U1" in bom
        assert isinstance(spice, str)


class TestStripUnit:
    """单位剥离测试"""

    def test_voltage(self) -> None:
        assert _strip_unit("3.3V") == "3.3"

    def test_current(self) -> None:
        assert _strip_unit("1A") == "1"

    def test_ohm(self) -> None:
        assert _strip_unit("100Ω") == "100"

    def test_capacitor_preserved(self) -> None:
        # 带 u/n/p 的不剥离
        assert _strip_unit("10uF") == "10uF"

    def test_plain(self) -> None:
        assert _strip_unit("42") == "42"
