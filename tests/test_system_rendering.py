"""Tests for schemaforge.system.rendering (T082-T090).

验证系统级渲染：模块布局、电源链、控制支路、模块间连线、
网络标签、GND 符号、占位块、以及完整 SVG 输出。
"""

from __future__ import annotations

import os
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

import pytest
import schemdraw

from schemaforge.library.store import ComponentStore
from schemaforge.system.models import (
    ModuleInstance,
    ModuleStatus,
    NetType,
    PortRef,
    ResolvedConnection,
    SystemDesignIR,
    SystemDesignRequest,
    SystemNet,
)
from schemaforge.system.rendering import (
    _build_global_ref_map,
    _draw_placeholder_module,
    _order_power_chain,
    draw_control_module,
    draw_gnd_symbols,
    draw_intermodule_wires,
    draw_net_labels,
    draw_power_module,
    layout_control_side,
    layout_power_chain,
    render_system_svg,
    render_system_svg_with_metadata,
)


# ============================================================
# Helpers
# ============================================================


def _make_port(
    module_id: str,
    role: str,
    pin_name: str,
    net_class: NetType = NetType.SIGNAL,
) -> PortRef:
    return PortRef(
        module_id=module_id,
        port_role=role,
        pin_name=pin_name,
        net_class=net_class,
    )


def _make_buck_instance(module_id: str = "buck1") -> ModuleInstance:
    return ModuleInstance(
        module_id=module_id,
        role="降压",
        resolved_category="buck",
        resolved_ports={
            "VIN": _make_port(module_id, "power_in", "VIN", NetType.POWER),
            "VOUT": _make_port(module_id, "power_out", "VOUT", NetType.POWER),
            "GND": _make_port(module_id, "ground", "GND", NetType.GROUND),
            "SW": _make_port(module_id, "switch", "SW", NetType.POWER),
            "FB": _make_port(module_id, "feedback", "FB", NetType.SIGNAL),
            "EN": _make_port(module_id, "enable", "EN", NetType.CONTROL),
            "BST": _make_port(module_id, "bootstrap", "BST", NetType.SIGNAL),
        },
        parameters={"v_in": "24", "v_out": "5", "i_out": "2"},
        status=ModuleStatus.RESOLVED,
        external_components=[
            {"type": "capacitor", "role": "input_cap", "value": "10uF"},
            {"type": "capacitor", "role": "output_cap", "value": "22uF"},
            {"type": "inductor", "role": "inductor", "value": "4.7uH"},
        ],
    )


def _make_ldo_instance(module_id: str = "ldo1") -> ModuleInstance:
    return ModuleInstance(
        module_id=module_id,
        role="稳压",
        resolved_category="ldo",
        resolved_ports={
            "VIN": _make_port(module_id, "power_in", "VIN", NetType.POWER),
            "VOUT": _make_port(module_id, "power_out", "VOUT", NetType.POWER),
            "GND": _make_port(module_id, "ground", "GND", NetType.GROUND),
        },
        parameters={"v_in": "5", "v_out": "3.3"},
        status=ModuleStatus.RESOLVED,
        external_components=[
            {"type": "capacitor", "role": "input_cap", "value": "10uF"},
            {"type": "capacitor", "role": "output_cap", "value": "22uF"},
        ],
    )


def _make_mcu_instance(module_id: str = "mcu1") -> ModuleInstance:
    return ModuleInstance(
        module_id=module_id,
        role="主控",
        resolved_category="mcu",
        resolved_ports={
            "VDD": _make_port(module_id, "power_in", "VDD", NetType.POWER),
            "VSS": _make_port(module_id, "ground", "VSS", NetType.GROUND),
            "PA0": _make_port(module_id, "gpio", "PA0"),
            "PA1": _make_port(module_id, "gpio", "PA1"),
        },
        parameters={"v_out": "3.3"},
        status=ModuleStatus.RESOLVED,
    )


def _make_led_instance(module_id: str = "led1") -> ModuleInstance:
    return ModuleInstance(
        module_id=module_id,
        role="指示灯",
        resolved_category="led",
        resolved_ports={
            "ANODE": _make_port(module_id, "other", "ANODE"),
            "CATHODE": _make_port(module_id, "ground", "CATHODE", NetType.GROUND),
        },
        parameters={"led_color": "green", "v_supply": "3.3", "led_current": "10"},
        status=ModuleStatus.RESOLVED,
        external_components=[
            {"type": "resistor", "role": "led_limit", "value": "110"},
        ],
    )


def _make_request(text: str = "test") -> SystemDesignRequest:
    return SystemDesignRequest(raw_text=text)


def _make_ir(
    modules: dict[str, ModuleInstance] | None = None,
    connections: list[ResolvedConnection] | None = None,
    nets: dict[str, SystemNet] | None = None,
) -> SystemDesignIR:
    return SystemDesignIR(
        request=_make_request(),
        module_instances=modules or {},
        connections=connections or [],
        nets=nets or {},
    )


def _make_power_connection(
    src_module: str,
    dst_module: str,
    net_name: str = "NET_5V",
) -> ResolvedConnection:
    return ResolvedConnection(
        resolved_connection_id=f"conn_{src_module}_{dst_module}",
        src_port=_make_port(src_module, "power_out", "VOUT", NetType.POWER),
        dst_port=_make_port(dst_module, "power_in", "VIN", NetType.POWER),
        net_name=net_name,
        rule_id="RULE_POWER_SUPPLY",
        evidence="电源供电链",
    )


# ============================================================
# T082: draw_power_module
# ============================================================


class TestDrawPowerModule:
    """T082: 单个电源模块绘制。"""

    def test_buck_module_returns_anchors(self) -> None:
        """Buck 模块返回 VIN/VOUT/GND 锚点。"""
        buck = _make_buck_instance()
        with schemdraw.Drawing(show=False) as d:
            d.config(fontsize=10, unit=3)
            anchors = draw_power_module(d, buck, (0, 0), {"input_cap": "C1", "output_cap": "C2", "inductor": "L1"})

        assert "VIN" in anchors
        assert "VOUT" in anchors
        assert "GND" in anchors
        # 各锚点是 (x, y) 元组
        for key, pos in anchors.items():
            assert len(pos) == 2
            assert isinstance(pos[0], (int, float))
            assert isinstance(pos[1], (int, float))

    def test_ldo_module_returns_anchors(self) -> None:
        """LDO 模块返回 VIN/VOUT/GND 锚点。"""
        ldo = _make_ldo_instance()
        with schemdraw.Drawing(show=False) as d:
            d.config(fontsize=10, unit=3)
            anchors = draw_power_module(d, ldo, (0, 0), {"input_cap": "C3", "output_cap": "C4"})

        assert "VIN" in anchors
        assert "VOUT" in anchors
        assert "GND" in anchors

    def test_buck_vout_x_greater_than_vin_x(self) -> None:
        """Buck VOUT 的 x 坐标大于 VIN（从左到右）。"""
        buck = _make_buck_instance()
        with schemdraw.Drawing(show=False) as d:
            d.config(fontsize=10, unit=3)
            anchors = draw_power_module(d, buck, (0, 0), {})

        assert anchors["VOUT"][0] > anchors["VIN"][0]

    def test_ldo_vout_x_greater_than_vin_x(self) -> None:
        """LDO VOUT 的 x 坐标大于 VIN。"""
        ldo = _make_ldo_instance()
        with schemdraw.Drawing(show=False) as d:
            d.config(fontsize=10, unit=3)
            anchors = draw_power_module(d, ldo, (0, 0), {})

        assert anchors["VOUT"][0] > anchors["VIN"][0]

    def test_module_at_nonzero_origin(self) -> None:
        """模块可以在非零原点放置。"""
        ldo = _make_ldo_instance()
        with schemdraw.Drawing(show=False) as d:
            d.config(fontsize=10, unit=3)
            anchors = draw_power_module(d, ldo, (20, 5), {})

        assert anchors["VIN"][0] == pytest.approx(20.0, abs=0.5)


# ============================================================
# T082: draw_control_module
# ============================================================


class TestDrawControlModule:
    """T082: 控制模块绘制。"""

    def test_mcu_module_returns_anchors(self) -> None:
        """MCU 模块返回 VDD/GND/GPIO 锚点。"""
        mcu = _make_mcu_instance()
        with schemdraw.Drawing(show=False) as d:
            d.config(fontsize=10, unit=3)
            anchors = draw_control_module(d, mcu, (0, 0), {})

        assert "VDD" in anchors
        assert "GND" in anchors

    def test_led_module_returns_anchors(self) -> None:
        """LED 模块返回 ANODE/GND 锚点。"""
        led = _make_led_instance()
        with schemdraw.Drawing(show=False) as d:
            d.config(fontsize=10, unit=3)
            anchors = draw_control_module(d, led, (0, 0), {"led_limit": "R1"})

        assert "ANODE" in anchors
        assert "GND" in anchors


class TestRenderMetadata:
    def test_render_system_svg_with_metadata_emits_real_geometry(self) -> None:
        buck = _make_buck_instance()
        ldo = _make_ldo_instance()
        mcu = _make_mcu_instance()
        led = _make_led_instance()

        connections = [
            _make_power_connection("buck1", "ldo1", "NET_5V"),
            ResolvedConnection(
                resolved_connection_id="conn_ldo1_mcu1",
                src_port=_make_port("ldo1", "power_out", "VOUT", NetType.POWER),
                dst_port=_make_port("mcu1", "power_in", "VDD", NetType.POWER),
                net_name="NET_3V3",
                rule_id="RULE_POWER_SUPPLY",
            ),
            ResolvedConnection(
                resolved_connection_id="gpio_led",
                src_port=_make_port("mcu1", "gpio", "PA1"),
                dst_port=_make_port("led1", "other", "ANODE"),
                net_name="NET_PA1_led1",
                rule_id="RULE_GPIO_LED",
            ),
        ]
        ir = _make_ir(
            modules={
                "buck1": buck,
                "ldo1": ldo,
                "mcu1": mcu,
                "led1": led,
            },
            connections=connections,
        )

        svg_path, metadata = render_system_svg_with_metadata(ir)

        assert os.path.exists(svg_path)
        assert set(metadata.module_bboxes) == {"buck1", "ldo1", "mcu1", "led1"}
        assert "mcu1" in metadata.anchor_points
        assert len(metadata.wire_paths) >= 3
        assert metadata.canvas_size[0] > 0
        assert metadata.canvas_size[1] > 0

    def test_layout_spec_moves_module_in_metadata(self) -> None:
        buck = _make_buck_instance()
        ldo = _make_ldo_instance()
        ir = _make_ir(
            modules={"buck1": buck, "ldo1": ldo},
            connections=[
                _make_power_connection("buck1", "ldo1", "NET_5V"),
            ],
        )
        layout_spec = SimpleNamespace(
            module_positions={"ldo1": (30.0, 2.0)},
            canvas_width=60.0,
            canvas_height=20.0,
            module_spacing_scale=1.0,
        )

        _svg_path, metadata = render_system_svg_with_metadata(
            ir,
            layout_spec=layout_spec,
        )

        assert metadata.module_bboxes["ldo1"][0] == pytest.approx(31.0, abs=2.0)
        assert metadata.canvas_size[0] >= 60.0


# ============================================================
# T083: layout_power_chain
# ============================================================


class TestLayoutPowerChain:
    """T083: 电源链布局。"""

    def test_single_buck_layout(self) -> None:
        """单个 Buck 模块正常布局。"""
        buck = _make_buck_instance()
        ir = _make_ir(modules={"buck1": buck})

        with schemdraw.Drawing(show=False) as d:
            d.config(fontsize=10, unit=3)
            anchors = layout_power_chain(d, ir, {"buck1": {}})

        assert "buck1" in anchors
        assert "VIN" in anchors["buck1"]
        assert "VOUT" in anchors["buck1"]

    def test_buck_ldo_chain_left_to_right(self) -> None:
        """Buck + LDO 电源链从左到右排列。"""
        buck = _make_buck_instance()
        ldo = _make_ldo_instance()
        conn = _make_power_connection("buck1", "ldo1")

        ir = _make_ir(
            modules={"buck1": buck, "ldo1": ldo},
            connections=[conn],
        )

        with schemdraw.Drawing(show=False) as d:
            d.config(fontsize=10, unit=3)
            anchors = layout_power_chain(d, ir, {"buck1": {}, "ldo1": {}})

        assert "buck1" in anchors
        assert "ldo1" in anchors
        # LDO 在 Buck 右侧
        assert anchors["ldo1"]["VIN"][0] > anchors["buck1"]["VIN"][0]

    def test_power_chain_order_deterministic(self) -> None:
        """电源链排序是确定性的 (C63)。"""
        buck = _make_buck_instance()
        ldo = _make_ldo_instance()
        conn = _make_power_connection("buck1", "ldo1")

        ir = _make_ir(
            modules={"buck1": buck, "ldo1": ldo},
            connections=[conn],
        )

        order = _order_power_chain(ir)
        assert len(order) == 2
        assert order[0].module_id == "buck1"
        assert order[1].module_id == "ldo1"

    def test_empty_power_chain(self) -> None:
        """无电源模块时返回空。"""
        mcu = _make_mcu_instance()
        ir = _make_ir(modules={"mcu1": mcu})

        with schemdraw.Drawing(show=False) as d:
            d.config(fontsize=10, unit=3)
            anchors = layout_power_chain(d, ir, {})

        assert len(anchors) == 0


# ============================================================
# T084: layout_control_side
# ============================================================


class TestLayoutControlSide:
    """T084: 控制支路布局。"""

    def test_mcu_placed_below_power_chain(self) -> None:
        """MCU 放置在电源链下方 (C69)。"""
        buck = _make_buck_instance()
        mcu = _make_mcu_instance()

        ir = _make_ir(modules={"buck1": buck, "mcu1": mcu})

        with schemdraw.Drawing(show=False) as d:
            d.config(fontsize=10, unit=3)
            power_anchors = layout_power_chain(d, ir, {"buck1": {}})
            layout_control_side(d, ir, power_anchors, {"mcu1": {}})

        assert "mcu1" in power_anchors
        # 控制模块 Y 坐标应低于电源链
        mcu_gnd_y = power_anchors["mcu1"]["GND"][1]
        buck_gnd_y = power_anchors["buck1"]["GND"][1]
        assert mcu_gnd_y < buck_gnd_y  # schemdraw Y 轴向下为负

    def test_led_placed_in_control_area(self) -> None:
        """LED 放置在控制区域。"""
        ldo = _make_ldo_instance()
        led = _make_led_instance()

        ir = _make_ir(modules={"ldo1": ldo, "led1": led})

        with schemdraw.Drawing(show=False) as d:
            d.config(fontsize=10, unit=3)
            power_anchors = layout_power_chain(d, ir, {"ldo1": {}})
            layout_control_side(d, ir, power_anchors, {"led1": {"led_limit": "R1"}})

        assert "led1" in power_anchors

    def test_no_control_modules_noop(self) -> None:
        """无控制模块时不出错。"""
        buck = _make_buck_instance()
        ir = _make_ir(modules={"buck1": buck})

        with schemdraw.Drawing(show=False) as d:
            d.config(fontsize=10, unit=3)
            power_anchors = layout_power_chain(d, ir, {"buck1": {}})
            layout_control_side(d, ir, power_anchors, {})

        assert "buck1" in power_anchors
        assert len(power_anchors) == 1


# ============================================================
# T085: Global ref map
# ============================================================


class TestGlobalRefMap:
    """T085: 全局参考编号。"""

    def test_ref_map_assigns_unique_refs(self) -> None:
        """每个外围元件获得唯一参考编号 (C64)。"""
        buck = _make_buck_instance()
        ldo = _make_ldo_instance()
        ir = _make_ir(modules={"buck1": buck, "ldo1": ldo})

        ref_map = _build_global_ref_map(ir)

        # Buck 有 3 个外围件，LDO 有 2 个
        all_refs = []
        for mod_refs in ref_map.values():
            all_refs.extend(mod_refs.values())

        assert len(all_refs) == 5
        assert len(set(all_refs)) == 5  # 全部唯一

    def test_ref_map_prefix_correct(self) -> None:
        """参考编号前缀正确（C=电容, R=电阻, L=电感）。"""
        buck = _make_buck_instance()
        ir = _make_ir(modules={"buck1": buck})

        ref_map = _build_global_ref_map(ir)
        buck_refs = ref_map["buck1"]

        assert buck_refs["input_cap"].startswith("C")
        assert buck_refs["output_cap"].startswith("C")
        assert buck_refs["inductor"].startswith("L")


# ============================================================
# T086: Inter-module wires
# ============================================================


class TestIntermoduleWires:
    """T086: 模块间连线。"""

    def test_wire_drawn_between_modules(self) -> None:
        """电源链连线正常绘制。"""
        conn = _make_power_connection("buck1", "ldo1")
        anchors = {
            "buck1": {"VIN": (0, 0), "VOUT": (10, 0), "GND": (5, -2)},
            "ldo1": {"VIN": (14, 0), "VOUT": (24, 0), "GND": (19, -2)},
        }

        with schemdraw.Drawing(show=False) as d:
            d.config(fontsize=10, unit=3)
            draw_intermodule_wires(d, [conn], anchors)

        # 验证 Drawing 包含元素（线段）
        assert len(d.elements) > 0

    def test_missing_anchors_skipped(self) -> None:
        """缺少锚点的连接被跳过。"""
        conn = _make_power_connection("buck1", "ldo1")
        anchors: dict[str, dict[str, tuple[float, float]]] = {
            "buck1": {"VIN": (0, 0), "VOUT": (10, 0)},
            # ldo1 缺失
        }

        with schemdraw.Drawing(show=False) as d:
            d.config(fontsize=10, unit=3)
            draw_intermodule_wires(d, [conn], anchors)

        # 不应崩溃
        assert len(d.elements) == 0


# ============================================================
# T087: Net labels
# ============================================================


class TestNetLabels:
    """T087: 网络标签。"""

    def test_net_label_drawn(self) -> None:
        """非 GND 网络生成标签 (C65)。"""
        net = SystemNet(
            net_id="NET_5V",
            net_name="NET_5V",
            net_type=NetType.POWER,
            voltage_domain="5V",
            members=[_make_port("buck1", "power_out", "VOUT", NetType.POWER)],
        )
        anchors = {
            "buck1": {"VOUT": (10, 0)},
        }

        with schemdraw.Drawing(show=False) as d:
            d.config(fontsize=10, unit=3)
            draw_net_labels(d, {"NET_5V": net}, anchors)

        assert len(d.elements) > 0

    def test_gnd_net_skipped(self) -> None:
        """GND 网络不生成标签（由 GND 符号表示）。"""
        net = SystemNet(
            net_id="GND",
            net_name="GND",
            net_type=NetType.GROUND,
            is_global=True,
            members=[_make_port("buck1", "ground", "GND", NetType.GROUND)],
        )
        anchors = {"buck1": {"GND": (5, -2)}}

        with schemdraw.Drawing(show=False) as d:
            d.config(fontsize=10, unit=3)
            draw_net_labels(d, {"GND": net}, anchors)

        assert len(d.elements) == 0


# ============================================================
# T088: GND symbols
# ============================================================


class TestGndSymbols:
    """T088: GND 符号。"""

    def test_gnd_symbols_no_crash(self) -> None:
        """draw_gnd_symbols 不崩溃 (C66)。"""
        anchors = {
            "buck1": {"VIN": (0, 0), "GND": (5, -2)},
            "ldo1": {"VIN": (14, 0), "GND": (19, -2)},
        }

        with schemdraw.Drawing(show=False) as d:
            d.config(fontsize=10, unit=3)
            draw_gnd_symbols(d, anchors)

        # 不崩溃即可（GND 已由各 draw 函数处理）


# ============================================================
# T067: Placeholder module (C67)
# ============================================================


class TestPlaceholderModule:
    """C67: 缺失模块占位块。"""

    def test_placeholder_returns_anchors(self) -> None:
        """占位模块返回 VIN/VOUT/GND 锚点。"""
        missing = ModuleInstance(
            module_id="unknown1",
            role="未知",
            resolved_category="buck",
            status=ModuleStatus.NEEDS_ASSET,
            missing_part_number="XYZ123",
        )

        with schemdraw.Drawing(show=False) as d:
            d.config(fontsize=10, unit=3)
            anchors = _draw_placeholder_module(d, missing, (0, 0))

        assert "VIN" in anchors
        assert "VOUT" in anchors
        assert "GND" in anchors

    def test_placeholder_in_power_chain(self) -> None:
        """缺失模块在电源链中被占位块替换。"""
        missing = ModuleInstance(
            module_id="buck1",
            role="降压",
            resolved_category="buck",
            status=ModuleStatus.NEEDS_ASSET,
            missing_part_number="UNKNOWN",
        )
        ir = _make_ir(modules={"buck1": missing})

        with schemdraw.Drawing(show=False) as d:
            d.config(fontsize=10, unit=3)
            anchors = layout_power_chain(d, ir, {"buck1": {}})

        assert "buck1" in anchors


# ============================================================
# T090: render_system_svg integration tests
# ============================================================


class TestRenderSystemSvg:
    """T090: 完整 SVG 渲染集成测试。"""

    def test_single_buck_renders_svg(self) -> None:
        """单个 Buck 模块渲染为 SVG 文件。"""
        buck = _make_buck_instance()
        ir = _make_ir(modules={"buck1": buck})

        filepath = render_system_svg(ir, filename="test_single_buck.svg")

        assert os.path.exists(filepath)
        assert filepath.endswith(".svg")

        # 文件非空
        assert os.path.getsize(filepath) > 0

    def test_buck_ldo_renders_with_connection(self) -> None:
        """Buck + LDO 渲染带连接线。"""
        buck = _make_buck_instance()
        ldo = _make_ldo_instance()
        conn = _make_power_connection("buck1", "ldo1")
        net = SystemNet(
            net_id="NET_5V",
            net_name="NET_5V",
            net_type=NetType.POWER,
            voltage_domain="5V",
        )

        ir = _make_ir(
            modules={"buck1": buck, "ldo1": ldo},
            connections=[conn],
            nets={"NET_5V": net},
        )

        filepath = render_system_svg(ir, filename="test_buck_ldo.svg")
        assert os.path.exists(filepath)
        assert os.path.getsize(filepath) > 100

    def test_svg_is_valid_xml(self) -> None:
        """SVG 文件是有效的 XML。"""
        buck = _make_buck_instance()
        ir = _make_ir(modules={"buck1": buck})

        filepath = render_system_svg(ir, filename="test_valid_xml.svg")

        with open(filepath, "rb") as f:
            content = f.read()

        # 应能被 XML 解析器正确解析
        root = ET.fromstring(content)
        assert root.tag.endswith("svg")

    def test_all_modules_present_in_svg(self) -> None:
        """所有模块出现在 SVG 中。"""
        buck = _make_buck_instance()
        ldo = _make_ldo_instance()
        mcu = _make_mcu_instance()

        ir = _make_ir(modules={"buck1": buck, "ldo1": ldo, "mcu1": mcu})

        filepath = render_system_svg(ir, filename="test_all_modules.svg")

        with open(filepath, "r", encoding="utf-8") as f:
            svg_text = f.read()

        # 所有模块标签应出现在 SVG 文本中
        assert "buck1" in svg_text
        assert "ldo1" in svg_text
        assert "mcu1" in svg_text

    def test_full_system_buck_ldo_mcu_led(self) -> None:
        """完整系统: Buck → LDO → MCU → LED 全链路渲染。"""
        buck = _make_buck_instance()
        ldo = _make_ldo_instance()
        mcu = _make_mcu_instance()
        led = _make_led_instance()

        conn_power = _make_power_connection("buck1", "ldo1")
        net_5v = SystemNet(
            net_id="NET_5V", net_name="NET_5V",
            net_type=NetType.POWER, voltage_domain="5V",
        )
        gnd_net = SystemNet(
            net_id="GND", net_name="GND",
            net_type=NetType.GROUND, is_global=True, voltage_domain="0V",
        )

        ir = _make_ir(
            modules={"buck1": buck, "ldo1": ldo, "mcu1": mcu, "led1": led},
            connections=[conn_power],
            nets={"NET_5V": net_5v, "GND": gnd_net},
        )

        filepath = render_system_svg(ir, filename="test_full_system.svg")

        assert os.path.exists(filepath)
        assert os.path.getsize(filepath) > 200

        # SVG 有效 XML
        with open(filepath, "rb") as f:
            root = ET.fromstring(f.read())
        assert root.tag.endswith("svg")

    def test_instances_parameter_override(self) -> None:
        """instances 参数覆盖可正常工作。"""
        buck = _make_buck_instance()
        ir = _make_ir()

        filepath = render_system_svg(ir, instances=[buck], filename="test_instances.svg")

        assert os.path.exists(filepath)
        assert "buck1" in ir.module_instances

    def test_default_filename(self) -> None:
        """默认文件名以 system_design_ 开头，.svg 结尾（含时间戳防缓存）。"""
        buck = _make_buck_instance()
        ir = _make_ir(modules={"buck1": buck})

        filepath = render_system_svg(ir)

        basename = os.path.basename(filepath)
        assert basename.startswith("system_design_")
        assert basename.endswith(".svg")
        assert os.path.exists(filepath)

    def test_empty_ir_no_crash(self) -> None:
        """空 IR 不崩溃。"""
        ir = _make_ir()

        filepath = render_system_svg(ir, filename="test_empty.svg")

        assert os.path.exists(filepath)

    def test_with_unresolved_items(self) -> None:
        """含 unresolved 连接的 IR 正常渲染 (C68)。"""
        buck = _make_buck_instance()
        ir = _make_ir(modules={"buck1": buck})
        ir.unresolved_items.append({
            "type": "unresolved_connection",
            "connection_id": "conn_bad",
            "src_module": "buck1",
            "reason": "无匹配规则",
        })

        filepath = render_system_svg(ir, filename="test_unresolved.svg")
        assert os.path.exists(filepath)

    def test_with_missing_module_placeholder(self) -> None:
        """缺失模块用占位块渲染 (C67)。"""
        missing = ModuleInstance(
            module_id="boost1",
            role="升压",
            resolved_category="boost",
            status=ModuleStatus.NEEDS_ASSET,
            missing_part_number="MISSING_PART",
        )
        ir = _make_ir(modules={"boost1": missing})

        filepath = render_system_svg(ir, filename="test_placeholder.svg")
        assert os.path.exists(filepath)


# ============================================================
# T090: Real device data tests
# ============================================================


STORE_SRC = Path("schemaforge/store")


@pytest.fixture()
def real_store(tmp_path: Path) -> ComponentStore:
    """从 schemaforge/store 复制到临时目录。"""
    dst = tmp_path / "store"
    shutil.copytree(STORE_SRC, dst)
    store = ComponentStore(dst)
    store.rebuild_index()
    return store


class TestRealDeviceRendering:
    """T090: 真实器件数据渲染测试。"""

    def test_tps5430_buck_render(self, real_store: ComponentStore) -> None:
        """TPS5430 Buck 模块渲染。"""
        from schemaforge.system.models import ModuleIntent
        from schemaforge.system.resolver import (
            instantiate_module_from_device,
            resolve_exact_part,
        )

        tps = resolve_exact_part(real_store, "TPS5430")
        assert tps is not None

        intent = ModuleIntent(
            intent_id="buck1",
            role="降压",
            category_hint="buck",
            electrical_targets={"v_in": "24", "v_out": "5"},
        )
        buck_inst = instantiate_module_from_device(intent, tps)

        # 添加 VOUT 端口（TPS5430 通过外围电感输出）
        if not any(
            p.port_role == "power_out"
            for p in buck_inst.resolved_ports.values()
        ):
            buck_inst.resolved_ports["VOUT_EXT"] = PortRef(
                module_id="buck1",
                port_role="power_out",
                pin_name="VOUT_EXT",
                net_class=NetType.POWER,
            )

        ir = _make_ir(modules={"buck1": buck_inst})
        filepath = render_system_svg(ir, filename="test_tps5430.svg")

        assert os.path.exists(filepath)
        with open(filepath, "rb") as f:
            root = ET.fromstring(f.read())
        assert root.tag.endswith("svg")

    def test_tps5430_ams1117_chain(self, real_store: ComponentStore) -> None:
        """TPS5430 + AMS1117 真实电源链渲染。"""
        from schemaforge.system.models import ModuleIntent
        from schemaforge.system.resolver import (
            instantiate_module_from_device,
            resolve_exact_part,
        )

        tps = resolve_exact_part(real_store, "TPS5430")
        ams = resolve_exact_part(real_store, "AMS1117-3.3")
        assert tps is not None
        assert ams is not None

        buck_intent = ModuleIntent(
            intent_id="buck1",
            role="降压",
            category_hint="buck",
            electrical_targets={"v_in": "24", "v_out": "5"},
        )
        ldo_intent = ModuleIntent(
            intent_id="ldo1",
            role="稳压",
            category_hint="ldo",
            electrical_targets={"v_in": "5", "v_out": "3.3"},
        )

        buck_inst = instantiate_module_from_device(buck_intent, tps)
        ldo_inst = instantiate_module_from_device(ldo_intent, ams)

        # 添加 VOUT 端口
        if not any(
            p.port_role == "power_out"
            for p in buck_inst.resolved_ports.values()
        ):
            buck_inst.resolved_ports["VOUT_EXT"] = PortRef(
                module_id="buck1",
                port_role="power_out",
                pin_name="VOUT_EXT",
                net_class=NetType.POWER,
            )

        conn = _make_power_connection("buck1", "ldo1")
        net = SystemNet(
            net_id="NET_5V", net_name="NET_5V",
            net_type=NetType.POWER, voltage_domain="5V",
        )

        ir = _make_ir(
            modules={"buck1": buck_inst, "ldo1": ldo_inst},
            connections=[conn],
            nets={"NET_5V": net},
        )

        filepath = render_system_svg(ir, filename="test_tps5430_ams1117.svg")

        assert os.path.exists(filepath)
        assert os.path.getsize(filepath) > 200

        with open(filepath, "rb") as f:
            root = ET.fromstring(f.read())
        assert root.tag.endswith("svg")
