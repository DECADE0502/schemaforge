"""TopologyDraft 拓扑草稿生成器测试"""

from __future__ import annotations

import pytest

from schemaforge.core.models import PinType
from schemaforge.design.topology_draft import (
    NetDraft,
    TopologyDraft,
    TopologyDraftGenerator,
)
from schemaforge.library.models import (
    DeviceModel,
    SymbolDef,
    SymbolPin,
    TopologyDef,
)


# ============================================================
# 测试用 DeviceModel 工厂
# ============================================================


def _make_ldo_no_topology() -> DeviceModel:
    return DeviceModel(
        part_number="AMS1117-3.3",
        manufacturer="AMS",
        description="LDO 3.3V 1A",
        category="ldo",
        symbol=SymbolDef(
            pins=[
                SymbolPin(name="VIN", pin_type=PinType.POWER_IN),
                SymbolPin(name="VOUT", pin_type=PinType.POWER_OUT),
                SymbolPin(name="GND", pin_type=PinType.GROUND),
            ],
        ),
        topology=None,
    )


def _make_led_no_topology() -> DeviceModel:
    return DeviceModel(
        part_number="LED-GREEN",
        description="绿色 LED",
        category="led",
        topology=None,
    )


def _make_buck_no_topology() -> DeviceModel:
    return DeviceModel(
        part_number="MP2307",
        description="Buck 降压转换器",
        category="buck",
        topology=None,
    )


def _make_divider_no_topology() -> DeviceModel:
    return DeviceModel(
        part_number="DIVIDER-1",
        description="电阻分压器",
        category="voltage_divider",
        topology=None,
    )


def _make_unknown_no_topology() -> DeviceModel:
    return DeviceModel(
        part_number="UNKNOWN-IC",
        description="未知器件",
        category="unknown_type",
        topology=None,
    )


# ============================================================
# NetDraft 基础测试
# ============================================================


class TestNetDraft:
    def test_create_basic(self) -> None:
        net = NetDraft(name="VIN")
        assert net.name == "VIN"
        assert net.pin_connections == []
        assert net.is_power is False
        assert net.is_ground is False

    def test_create_with_pins(self) -> None:
        net = NetDraft(
            name="GND",
            pin_connections=["U1.GND", "C1.2"],
            is_ground=True,
        )
        assert net.name == "GND"
        assert len(net.pin_connections) == 2
        assert net.is_ground is True

    def test_power_net(self) -> None:
        net = NetDraft(name="VCC", pin_connections=["U1.VCC", "R1.1"], is_power=True)
        assert net.is_power is True
        assert "U1.VCC" in net.pin_connections


# ============================================================
# TopologyDraft 基础测试
# ============================================================


class TestTopologyDraft:
    def test_create_minimal(self) -> None:
        draft = TopologyDraft(name="ldo")
        assert draft.name == "ldo"
        assert draft.description == ""
        assert draft.nets == []
        assert draft.components == []
        assert draft.confidence == 0.8

    def test_create_full(self) -> None:
        draft = TopologyDraft(
            name="ldo",
            description="LDO 推荐电路",
            nets=[
                NetDraft(name="VIN", pin_connections=["U1.VIN", "C1.1"], is_power=True),
                NetDraft(
                    name="GND", pin_connections=["U1.GND", "C1.2"], is_ground=True
                ),
            ],
            components=[{"role": "input_cap", "ref_prefix": "C"}],
            confidence=0.9,
        )
        assert len(draft.nets) == 2
        assert len(draft.components) == 1
        assert draft.confidence == 0.9


# ============================================================
# TopologyDraftGenerator — Mock 生成测试
# ============================================================


class TestMockGeneration:
    def setup_method(self) -> None:
        self.gen = TopologyDraftGenerator(use_mock=True)

    def test_generate_ldo(self) -> None:
        device = _make_ldo_no_topology()
        draft = self.gen.generate(device)
        assert isinstance(draft, TopologyDraft)
        assert draft.name == "ldo"
        assert len(draft.nets) >= 3
        net_names = {n.name for n in draft.nets}
        assert "VIN" in net_names
        assert "VOUT" in net_names
        assert "GND" in net_names

    def test_generate_led(self) -> None:
        device = _make_led_no_topology()
        draft = self.gen.generate(device)
        assert draft.name == "led_driver"
        assert len(draft.components) >= 1
        assert any(c["role"] == "limit_resistor" for c in draft.components)

    def test_generate_voltage_divider(self) -> None:
        device = _make_divider_no_topology()
        draft = self.gen.generate(device)
        assert draft.name == "voltage_divider"
        roles = {c["role"] for c in draft.components}
        assert "upper_resistor" in roles
        assert "lower_resistor" in roles

    def test_generate_buck(self) -> None:
        device = _make_buck_no_topology()
        draft = self.gen.generate(device)
        assert draft.name == "buck"
        roles = {c["role"] for c in draft.components}
        assert "inductor" in roles
        assert "output_cap" in roles

    def test_generate_unknown_raises(self) -> None:
        device = _make_unknown_no_topology()
        with pytest.raises(ValueError, match="无法为器件"):
            self.gen.generate(device)

    def test_ldo_has_confidence(self) -> None:
        device = _make_ldo_no_topology()
        draft = self.gen.generate(device)
        assert 0.0 < draft.confidence <= 1.0

    def test_buck_has_boot_cap(self) -> None:
        device = _make_buck_no_topology()
        draft = self.gen.generate(device)
        roles = {c["role"] for c in draft.components}
        assert "boot_cap" in roles


# ============================================================
# validate_draft 测试
# ============================================================


class TestValidateDraft:
    def setup_method(self) -> None:
        self.gen = TopologyDraftGenerator(use_mock=True)

    def test_valid_ldo_draft(self) -> None:
        device = _make_ldo_no_topology()
        draft = self.gen.generate(device)
        errors = self.gen.validate_draft(draft, device)
        assert errors == [], f"LDO 草稿验证失败：{errors}"

    def test_floating_net_detected(self) -> None:
        device = _make_ldo_no_topology()
        draft = TopologyDraft(
            name="ldo",
            nets=[
                NetDraft(name="VIN", pin_connections=["U1.VIN"], is_power=True),
                NetDraft(
                    name="GND", pin_connections=["U1.GND", "C1.2"], is_ground=True
                ),
            ],
        )
        errors = self.gen.validate_draft(draft, device)
        assert any("浮空" in e for e in errors)

    def test_duplicate_pin_detected(self) -> None:
        device = _make_ldo_no_topology()
        draft = TopologyDraft(
            name="ldo",
            nets=[
                NetDraft(name="VIN", pin_connections=["U1.VIN", "C1.1"], is_power=True),
                NetDraft(
                    name="VOUT", pin_connections=["U1.VIN", "C2.1"], is_power=True
                ),
                NetDraft(
                    name="GND",
                    pin_connections=["U1.GND", "C1.2", "C2.2"],
                    is_ground=True,
                ),
            ],
        )
        errors = self.gen.validate_draft(draft, device)
        assert any("重复" in e for e in errors)

    def test_power_ground_conflict_detected(self) -> None:
        device = _make_ldo_no_topology()
        draft = TopologyDraft(
            name="ldo",
            nets=[
                NetDraft(
                    name="BAD",
                    pin_connections=["U1.VIN", "C1.1"],
                    is_power=True,
                    is_ground=True,
                ),
                NetDraft(
                    name="GND", pin_connections=["U1.GND", "C1.2"], is_ground=True
                ),
            ],
        )
        errors = self.gen.validate_draft(draft, device)
        assert any("矛盾" in e for e in errors)

    def test_missing_ic_pin_detected(self) -> None:
        device = _make_ldo_no_topology()
        draft = TopologyDraft(
            name="ldo",
            nets=[
                NetDraft(name="VIN", pin_connections=["U1.VIN", "C1.1"], is_power=True),
                NetDraft(
                    name="GND", pin_connections=["U1.GND", "C1.2"], is_ground=True
                ),
            ],
        )
        errors = self.gen.validate_draft(draft, device)
        assert any("VOUT" in e for e in errors)


# ============================================================
# draft_to_topology 转换测试
# ============================================================


class TestDraftToTopology:
    def setup_method(self) -> None:
        self.gen = TopologyDraftGenerator(use_mock=True)

    def test_ldo_converts_to_topology_def(self) -> None:
        device = _make_ldo_no_topology()
        draft = self.gen.generate(device)
        topo = self.gen.draft_to_topology(draft, device)
        assert isinstance(topo, TopologyDef)
        assert topo.circuit_type == "ldo"

    def test_ldo_external_components(self) -> None:
        device = _make_ldo_no_topology()
        draft = self.gen.generate(device)
        topo = self.gen.draft_to_topology(draft, device)
        roles = {c.role for c in topo.external_components}
        assert "input_cap" in roles
        assert "output_cap" in roles

    def test_ldo_connections_nets(self) -> None:
        device = _make_ldo_no_topology()
        draft = self.gen.generate(device)
        topo = self.gen.draft_to_topology(draft, device)
        net_names = {c.net_name for c in topo.connections}
        assert "VIN" in net_names
        assert "VOUT" in net_names
        assert "GND" in net_names

    def test_ldo_gnd_is_ground(self) -> None:
        device = _make_ldo_no_topology()
        draft = self.gen.generate(device)
        topo = self.gen.draft_to_topology(draft, device)
        gnd_conn = next(c for c in topo.connections if c.net_name == "GND")
        assert gnd_conn.is_ground is True

    def test_device_pin_extracted(self) -> None:
        device = _make_ldo_no_topology()
        draft = self.gen.generate(device)
        topo = self.gen.draft_to_topology(draft, device)
        vin_conn = next(c for c in topo.connections if c.net_name == "VIN")
        assert vin_conn.device_pin == "VIN"


# ============================================================
# TopologyAdapter fallback 集成测试
# ============================================================


class TestAdapterFallback:
    def test_adapter_fallback_ldo(self) -> None:
        from schemaforge.design.topology_adapter import TopologyAdapter

        adapter = TopologyAdapter(use_mock_draft=True)
        device = _make_ldo_no_topology()
        adapted = adapter.adapt_single(device, {"v_in": "5"})
        assert adapted is not None
        assert adapted.render_params.get("ic_model") == "AMS1117-3.3"

    def test_adapter_fallback_unknown_raises(self) -> None:
        from schemaforge.design.topology_adapter import TopologyAdapter

        adapter = TopologyAdapter(use_mock_draft=True)
        device = _make_unknown_no_topology()
        with pytest.raises(ValueError):
            adapter.adapt_single(device)
