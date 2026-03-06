"""TopologyRenderer 通用拓扑渲染器测试

测试内容：
- build_ic_element 构建IC元件
- 5种布局策略各自生成有效SVG
- render() 分发到正确的布局函数
- render() 对未知电路类型抛异常
- render() 对无拓扑定义的器件抛异常
- 从 store 加载 AMS1117-3.3 并渲染
- 新旧渲染路径对比（都能成功生成SVG）
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from schemaforge.library.models import (
    DeviceModel,
    ExternalComponent,
    PinSide,
    SymbolDef,
    SymbolPin,
    TopologyDef,
)
from schemaforge.library.store import ComponentStore
from schemaforge.schematic.renderer import TopologyRenderer


# ============================================================
# Fixture
# ============================================================

@pytest.fixture
def renderer() -> TopologyRenderer:
    return TopologyRenderer()


@pytest.fixture
def store() -> ComponentStore:
    """加载实际的器件库"""
    store_dir = Path(__file__).parent.parent / "schemaforge" / "store"
    s = ComponentStore(store_dir)
    s.rebuild_index()
    return s


@pytest.fixture
def ams1117_device(store: ComponentStore) -> DeviceModel:
    """从 store 加载 AMS1117-3.3"""
    device = store.get_device("AMS1117-3.3")
    assert device is not None, "AMS1117-3.3 应存在于器件库中"
    return device


@pytest.fixture
def divider_device(store: ComponentStore) -> DeviceModel:
    """从 store 加载 VOLTAGE_DIVIDER"""
    device = store.get_device("VOLTAGE_DIVIDER")
    assert device is not None
    return device


@pytest.fixture
def led_device(store: ComponentStore) -> DeviceModel:
    """从 store 加载 LED_INDICATOR"""
    device = store.get_device("LED_INDICATOR")
    assert device is not None
    return device


@pytest.fixture
def rc_device(store: ComponentStore) -> DeviceModel:
    """从 store 加载 RC_LOWPASS"""
    device = store.get_device("RC_LOWPASS")
    assert device is not None
    return device


@pytest.fixture
def no_topology_device() -> DeviceModel:
    """没有拓扑定义的器件"""
    return DeviceModel(
        part_number="BARE_RESISTOR",
        description="纯电阻，无拓扑",
    )


@pytest.fixture
def unknown_type_device() -> DeviceModel:
    """未知电路类型的器件"""
    return DeviceModel(
        part_number="MYSTERY_IC",
        topology=TopologyDef(circuit_type="quantum_flux_capacitor"),
    )


# ============================================================
# build_ic_element 测试
# ============================================================

class TestBuildIcElement:
    """测试 TopologyRenderer.build_ic_element"""

    def test_build_from_ams1117_symbol(self, ams1117_device: DeviceModel) -> None:
        """从 AMS1117 的 SymbolDef 构建 IC 元件"""
        assert ams1117_device.symbol is not None
        ic = TopologyRenderer.build_ic_element(ams1117_device.symbol, "AMS1117-3.3")
        # schemdraw Ic 对象应存在
        assert ic is not None

    def test_build_with_minimal_symbol(self) -> None:
        """最小化 SymbolDef 也能构建"""
        symbol = SymbolDef(
            pins=[
                SymbolPin(name="IN", side=PinSide.LEFT),
                SymbolPin(name="OUT", side=PinSide.RIGHT),
            ],
        )
        ic = TopologyRenderer.build_ic_element(symbol, "TEST_IC")
        assert ic is not None

    def test_build_with_all_pin_options(self) -> None:
        """包含所有可选字段的引脚"""
        symbol = SymbolDef(
            pins=[
                SymbolPin(
                    name="VIN",
                    pin_number="1",
                    side=PinSide.LEFT,
                    slot="1/2",
                    inverted=False,
                    anchor_name="",
                ),
                SymbolPin(
                    name="VOUT",
                    pin_number="2",
                    side=PinSide.RIGHT,
                    slot="1/2",
                    inverted=True,
                ),
            ],
            size=(3.0, 2.0),
            edge_pad_w=0.3,
            edge_pad_h=0.4,
            pin_spacing=1.2,
            lead_len=0.6,
            label_position="bottom",
        )
        ic = TopologyRenderer.build_ic_element(symbol, "FULL_IC")
        assert ic is not None


# ============================================================
# 布局策略注册测试
# ============================================================

class TestLayoutRegistration:
    """测试布局策略已正确注册"""

    def test_all_five_strategies_registered(self) -> None:
        """5种布局策略应全部注册"""
        expected = {"ldo", "buck", "voltage_divider", "led_driver", "rc_filter"}
        actual = set(TopologyRenderer.LAYOUT_STRATEGIES.keys())
        assert expected.issubset(actual), f"缺少策略: {expected - actual}"


# ============================================================
# 各布局策略渲染测试
# ============================================================

class TestLayoutLDO:
    """LDO 布局渲染测试"""

    def test_render_ldo_produces_svg(
        self, renderer: TopologyRenderer, ams1117_device: DeviceModel
    ) -> None:
        """LDO布局应生成有效的SVG文件"""
        params = {"v_in": 5.0, "v_out": "3.3"}
        path = renderer.render(ams1117_device, params, filename="test_topo_ldo.svg")
        assert os.path.exists(path)
        assert os.path.getsize(path) > 100
        with open(path, encoding="utf-8") as f:
            content = f.read()
        assert "<svg" in content
        assert "</svg>" in content

    def test_render_ldo_uses_device_defaults(
        self, renderer: TopologyRenderer, ams1117_device: DeviceModel
    ) -> None:
        """不传电容值时应使用器件拓扑的默认值"""
        path = renderer.render(ams1117_device, {}, filename="test_topo_ldo_defaults.svg")
        assert os.path.exists(path)


class TestLayoutBuck:
    """Buck 布局渲染测试"""

    def test_render_buck_produces_svg(self, renderer: TopologyRenderer) -> None:
        """Buck布局应生成有效的SVG文件"""
        # Buck 目前无实际器件在store中，用构造的DeviceModel测试
        device = DeviceModel(
            part_number="TPS54202",
            description="Buck降压转换器",
            topology=TopologyDef(circuit_type="buck"),
        )
        params = {"v_in": 12.0, "v_out": 3.3}
        path = renderer.render(device, params, filename="test_topo_buck.svg")
        assert os.path.exists(path)
        assert os.path.getsize(path) > 100
        with open(path, encoding="utf-8") as f:
            content = f.read()
        assert "<svg" in content


class TestLayoutVoltageDivider:
    """电压分压器布局渲染测试"""

    def test_render_divider_produces_svg(
        self, renderer: TopologyRenderer, divider_device: DeviceModel
    ) -> None:
        """分压器布局应生成有效的SVG文件"""
        params = {"v_in": 5.0, "v_out": 2.5, "r_total": 20.0}
        path = renderer.render(divider_device, params, filename="test_topo_divider.svg")
        assert os.path.exists(path)
        with open(path, encoding="utf-8") as f:
            content = f.read()
        assert "<svg" in content


class TestLayoutLEDDriver:
    """LED驱动器布局渲染测试"""

    def test_render_led_produces_svg(
        self, renderer: TopologyRenderer, led_device: DeviceModel
    ) -> None:
        """LED驱动器布局应生成有效的SVG文件"""
        params = {"v_supply": 3.3, "led_color": "green", "led_current": 10.0}
        path = renderer.render(led_device, params, filename="test_topo_led.svg")
        assert os.path.exists(path)
        with open(path, encoding="utf-8") as f:
            content = f.read()
        assert "<svg" in content


class TestLayoutRCFilter:
    """RC滤波器布局渲染测试"""

    def test_render_rc_produces_svg(
        self, renderer: TopologyRenderer, rc_device: DeviceModel
    ) -> None:
        """RC滤波器布局应生成有效的SVG文件"""
        params = {"f_cutoff": 1000.0, "r_value": 10.0}
        path = renderer.render(rc_device, params, filename="test_topo_rc.svg")
        assert os.path.exists(path)
        with open(path, encoding="utf-8") as f:
            content = f.read()
        assert "<svg" in content


# ============================================================
# render() 分发与异常测试
# ============================================================

class TestRenderDispatch:
    """测试 render() 方法的分发和异常处理"""

    def test_render_dispatches_to_ldo(
        self, renderer: TopologyRenderer, ams1117_device: DeviceModel
    ) -> None:
        """render() 应正确分发到 LDO 布局"""
        path = renderer.render(ams1117_device, {}, filename="test_dispatch_ldo.svg")
        assert "topo" in os.path.basename(path) or os.path.exists(path)

    def test_render_raises_for_no_topology(
        self, renderer: TopologyRenderer, no_topology_device: DeviceModel
    ) -> None:
        """无拓扑定义的器件应抛出 ValueError"""
        with pytest.raises(ValueError, match="没有拓扑定义"):
            renderer.render(no_topology_device, {})

    def test_render_raises_for_unknown_type(
        self, renderer: TopologyRenderer, unknown_type_device: DeviceModel
    ) -> None:
        """未知电路类型应抛出 ValueError"""
        with pytest.raises(ValueError, match="不支持的电路类型"):
            renderer.render(unknown_type_device, {})

    def test_render_from_params_alias(
        self, renderer: TopologyRenderer, ams1117_device: DeviceModel
    ) -> None:
        """render_from_params 应与 render 等效"""
        path = renderer.render_from_params(ams1117_device, {"v_in": 5.0, "v_out": "3.3"})
        assert os.path.exists(path)


# ============================================================
# Store集成测试：从JSON加载器件并渲染
# ============================================================

class TestStoreIntegration:
    """从器件库加载数据并通过 TopologyRenderer 渲染"""

    def test_load_and_render_ams1117(
        self, renderer: TopologyRenderer, ams1117_device: DeviceModel
    ) -> None:
        """加载 AMS1117-3.3 并渲染完整的LDO电路"""
        assert ams1117_device.symbol is not None
        assert ams1117_device.topology is not None
        assert ams1117_device.topology.circuit_type == "ldo"

        path = renderer.render(
            ams1117_device,
            {"v_in": 5.0, "v_out": "3.3"},
            filename="test_store_ams1117.svg",
        )
        assert os.path.exists(path)
        assert os.path.getsize(path) > 100


# ============================================================
# 新旧渲染路径对比
# ============================================================

class TestOldVsNewRendering:
    """对比旧硬编码渲染和新拓扑渲染"""

    def test_both_paths_produce_svg_for_ldo(
        self, renderer: TopologyRenderer, ams1117_device: DeviceModel
    ) -> None:
        """旧 render_ldo_from_params 和新 TopologyRenderer 都应生成SVG"""
        from schemaforge.render.ldo import render_ldo_from_params

        params = {"v_in": 5.0, "v_out": "3.3", "ic_model": "AMS1117"}

        # 旧路径
        old_path = render_ldo_from_params(params)
        assert os.path.exists(old_path)

        # 新路径
        new_path = renderer.render(ams1117_device, params, filename="test_new_ldo.svg")
        assert os.path.exists(new_path)

        # 两个文件都是有效SVG
        for p in [old_path, new_path]:
            with open(p, encoding="utf-8") as f:
                content = f.read()
            assert "<svg" in content

    def test_both_paths_produce_svg_for_divider(
        self, renderer: TopologyRenderer, divider_device: DeviceModel
    ) -> None:
        """旧 render_divider_from_params 和新路径都应生成SVG"""
        from schemaforge.render.divider import render_divider_from_params

        params = {"v_in": 5.0, "v_out": 2.5, "r_total": 20.0}

        old_path = render_divider_from_params(params)
        assert os.path.exists(old_path)

        new_path = renderer.render(divider_device, params, filename="test_new_divider.svg")
        assert os.path.exists(new_path)
