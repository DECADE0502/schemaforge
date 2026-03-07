"""Tests for schemaforge.system.connection_rules (T052-T060)。

验证连接规则引擎的规则匹配、电源链、GPIO→LED、SPI、GND 合并、
特殊引脚处理、unresolved 机制、以及 explain 功能。
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from schemaforge.library.store import ComponentStore
from schemaforge.system.connection_rules import (
    RULE_BOOTSTRAP_CAP,
    RULE_ENABLE_PULLUP,
    RULE_GPIO_LED,
    RULE_POWER_SUPPLY,
    RULE_SPI_BUS,
    ConnectionRule,
    explain_connection_rule,
    get_all_rules,
    resolve_all_connections,
    resolve_ground_strategy,
)
from schemaforge.system.models import (
    ConnectionIntent,
    ConnectionSemantic,
    ModuleInstance,
    ModuleStatus,
    NetType,
    PortRef,
    ResolvedConnection,
    SignalType,
)
from schemaforge.system.resolver import (
    instantiate_module_from_device,
    resolve_exact_part,
)


# ============================================================
# Fixtures
# ============================================================

STORE_SRC = Path("schemaforge/store")


@pytest.fixture()
def real_store(tmp_path: Path) -> ComponentStore:
    """从 schemaforge/store 复制到临时目录，返回 ComponentStore。"""
    dst = tmp_path / "store"
    shutil.copytree(STORE_SRC, dst)
    store = ComponentStore(dst)
    store.rebuild_index()
    return store


def _make_port(
    module_id: str,
    role: str,
    pin_name: str,
    net_class: NetType = NetType.SIGNAL,
) -> PortRef:
    """快捷创建 PortRef。"""
    return PortRef(
        module_id=module_id,
        port_role=role,
        pin_name=pin_name,
        net_class=net_class,
    )


def _make_buck_instance(module_id: str = "buck1") -> ModuleInstance:
    """创建模拟 Buck 模块实例（TPS5430 风格）。"""
    return ModuleInstance(
        module_id=module_id,
        role="降压",
        resolved_category="buck",
        resolved_ports={
            "VIN": _make_port(module_id, "power_in", "VIN", NetType.POWER),
            "GND": _make_port(module_id, "ground", "GND", NetType.GROUND),
            "SW": _make_port(module_id, "switch", "SW", NetType.POWER),
            "FB": _make_port(module_id, "feedback", "FB", NetType.SIGNAL),
            "EN": _make_port(module_id, "enable", "EN", NetType.CONTROL),
            "BST": _make_port(module_id, "bootstrap", "BST", NetType.SIGNAL),
        },
        parameters={"v_in": "24", "v_out": "5", "i_out": "2"},
        status=ModuleStatus.RESOLVED,
    )


def _make_ldo_instance(module_id: str = "ldo1") -> ModuleInstance:
    """创建模拟 LDO 模块实例（AMS1117 风格）。"""
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
    )


def _make_mcu_instance(module_id: str = "mcu1") -> ModuleInstance:
    """创建模拟 MCU 模块实例（STM32 风格）。"""
    return ModuleInstance(
        module_id=module_id,
        role="主控",
        resolved_category="mcu",
        resolved_ports={
            "VDD": _make_port(module_id, "power_in", "VDD", NetType.POWER),
            "VSS": _make_port(module_id, "ground", "VSS", NetType.GROUND),
            "PA0": _make_port(module_id, "gpio", "PA0"),
            "PA1": _make_port(module_id, "gpio", "PA1"),
            "PA5": _make_port(module_id, "spi", "PA5"),  # SPI1_SCK
            "PA6": _make_port(module_id, "spi", "PA6"),  # SPI1_MISO
            "PA7": _make_port(module_id, "spi", "PA7"),  # SPI1_MOSI
        },
        parameters={"v_out": "3.3"},
        status=ModuleStatus.RESOLVED,
    )


def _make_led_instance(module_id: str = "led1") -> ModuleInstance:
    """创建模拟 LED 模块实例。"""
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
    )


def _make_flash_instance(module_id: str = "flash1") -> ModuleInstance:
    """创建模拟 SPI Flash 模块实例。"""
    return ModuleInstance(
        module_id=module_id,
        role="存储",
        resolved_category="flash",
        resolved_ports={
            "VCC": _make_port(module_id, "power_in", "VCC", NetType.POWER),
            "GND": _make_port(module_id, "ground", "GND", NetType.GROUND),
            "DI": _make_port(module_id, "spi", "DI"),
            "DO": _make_port(module_id, "spi", "DO"),
            "CLK": _make_port(module_id, "spi", "CLK"),
            "CS": _make_port(module_id, "spi", "CS"),
        },
        parameters={},
        status=ModuleStatus.RESOLVED,
    )


# ============================================================
# T052: ConnectionRule data format
# ============================================================


class TestConnectionRuleFormat:
    """T052: 规则数据格式。"""

    def test_rule_fields(self) -> None:
        """ConnectionRule 包含所有必要字段。"""
        rule = ConnectionRule(
            rule_id="TEST_RULE",
            name="测试规则",
            priority=99,
            src_category="buck",
            src_port_role="power_out",
            dst_category="ldo",
            dst_port_role="power_in",
            signal_type=SignalType.POWER_SUPPLY,
            semantic=ConnectionSemantic.SUPPLY_CHAIN,
            auto_components=[{"type": "resistor", "role": "test"}],
        )
        assert rule.rule_id == "TEST_RULE"
        assert rule.priority == 99
        assert rule.signal_type == SignalType.POWER_SUPPLY
        assert len(rule.auto_components) == 1

    def test_all_rules_sorted_by_priority(self) -> None:
        """get_all_rules 返回按优先级排序的规则列表。"""
        rules = get_all_rules()
        assert len(rules) >= 4
        for i in range(len(rules) - 1):
            assert rules[i].priority <= rules[i + 1].priority

    def test_rule_ids_unique(self) -> None:
        """所有规则 ID 唯一。"""
        rules = get_all_rules()
        ids = [r.rule_id for r in rules]
        assert len(ids) == len(set(ids))


# ============================================================
# T053: Power chain rules
# ============================================================


class TestPowerChainRules:
    """T053: 电源供电链规则。"""

    def test_buck_vout_to_ldo_vin(self) -> None:
        """Buck.VOUT → LDO.VIN 解析为 NET_5V 电源链。"""
        buck = _make_buck_instance()
        # 给 buck 添加 VOUT 端口
        buck.resolved_ports["VOUT"] = _make_port(
            "buck1", "power_out", "VOUT", NetType.POWER,
        )
        ldo = _make_ldo_instance()

        intent = ConnectionIntent(
            connection_id="conn_power_1",
            src_module_intent="buck1",
            dst_module_intent="ldo1",
            signal_type=SignalType.POWER_SUPPLY,
            connection_semantics=ConnectionSemantic.SUPPLY_CHAIN,
        )

        instances = {"buck1": buck, "ldo1": ldo}
        conns, nets, unresolved = resolve_all_connections(instances, [intent])

        # 应有电源链连接
        power_conns = [
            c for c in conns
            if c.rule_id == RULE_POWER_SUPPLY.rule_id
        ]
        assert len(power_conns) >= 1
        assert power_conns[0].net_name == "NET_5V"
        assert power_conns[0].src_port.pin_name == "VOUT"
        assert power_conns[0].dst_port.pin_name == "VIN"

    def test_power_net_voltage_naming(self) -> None:
        """电源网络按电压域命名。"""
        buck = _make_buck_instance()
        buck.resolved_ports["VOUT"] = _make_port(
            "buck1", "power_out", "VOUT", NetType.POWER,
        )
        buck.parameters["v_out"] = "3.3"
        ldo = _make_ldo_instance()

        intent = ConnectionIntent(
            connection_id="conn_power_2",
            src_module_intent="buck1",
            dst_module_intent="ldo1",
            signal_type=SignalType.POWER_SUPPLY,
        )

        instances = {"buck1": buck, "ldo1": ldo}
        conns, nets, _ = resolve_all_connections(instances, [intent])

        power_conns = [
            c for c in conns if c.rule_id == RULE_POWER_SUPPLY.rule_id
        ]
        assert len(power_conns) >= 1
        assert "3.3" in power_conns[0].net_name

    def test_power_net_in_nets_dict(self) -> None:
        """电源网络被加入 nets 字典。"""
        buck = _make_buck_instance()
        buck.resolved_ports["VOUT"] = _make_port(
            "buck1", "power_out", "VOUT", NetType.POWER,
        )
        ldo = _make_ldo_instance()

        intent = ConnectionIntent(
            connection_id="conn_power_3",
            src_module_intent="buck1",
            dst_module_intent="ldo1",
            signal_type=SignalType.POWER_SUPPLY,
        )

        instances = {"buck1": buck, "ldo1": ldo}
        _, nets, _ = resolve_all_connections(instances, [intent])

        assert "NET_5V" in nets
        assert nets["NET_5V"].net_type == NetType.POWER

    def test_power_rule_has_highest_priority(self) -> None:
        """RULE_POWER_SUPPLY 优先级最高。"""
        rules = get_all_rules()
        assert rules[0].rule_id == RULE_POWER_SUPPLY.rule_id


# ============================================================
# T054: GPIO→LED rule
# ============================================================


class TestGpioLedRule:
    """T054: GPIO 驱动 LED 规则。"""

    def test_gpio_led_auto_resistor(self) -> None:
        """MCU.PA1 → LED 自动添加限流电阻 (C33)。"""
        mcu = _make_mcu_instance()
        led = _make_led_instance()

        intent = ConnectionIntent(
            connection_id="conn_led_1",
            src_module_intent="mcu1",
            src_port_hint="PA1",
            dst_module_intent="led1",
            signal_type=SignalType.GPIO,
            connection_semantics=ConnectionSemantic.GPIO_DRIVE,
        )

        instances = {"mcu1": mcu, "led1": led}
        conns, nets, unresolved = resolve_all_connections(instances, [intent])

        # 应有 GPIO→LED 连接
        led_conns = [
            c for c in conns if c.rule_id == RULE_GPIO_LED.rule_id
        ]
        assert len(led_conns) == 1
        assert led_conns[0].src_port.pin_name == "PA1"

        # LED 应自动获得限流电阻
        resistors = [
            comp for comp in led.external_components
            if comp.get("role") == "led_limit"
        ]
        assert len(resistors) >= 1
        # 验证电阻值合理：(3.3 - 2.2) / 0.01 = 110 ohm
        r_value = float(resistors[0]["value"])
        assert 100 <= r_value <= 120  # 允许浮点误差

    def test_gpio_led_resistor_formula(self) -> None:
        """限流电阻包含计算公式。"""
        mcu = _make_mcu_instance()
        led = _make_led_instance()
        led.parameters["led_color"] = "red"

        intent = ConnectionIntent(
            connection_id="conn_led_2",
            src_module_intent="mcu1",
            src_port_hint="PA0",
            dst_module_intent="led1",
            signal_type=SignalType.GPIO,
        )

        instances = {"mcu1": mcu, "led1": led}
        resolve_all_connections(instances, [intent])

        resistors = [
            comp for comp in led.external_components
            if comp.get("role") == "led_limit"
        ]
        assert len(resistors) >= 1
        assert "formula" in resistors[0]
        # Red LED: (3.3 - 1.8) / 0.01 = 150
        r_value = float(resistors[0]["value"])
        assert 145 <= r_value <= 155


# ============================================================
# T055: SPI→Flash skeleton
# ============================================================


class TestSpiBusRule:
    """T055: SPI 总线连接规则。"""

    def test_spi_pin_mapping(self) -> None:
        """MCU SPI 引脚正确映射到 Flash SPI 引脚。"""
        mcu = _make_mcu_instance()
        # 使用标准 SPI 引脚名
        mcu.resolved_ports["SCK"] = _make_port("mcu1", "spi", "SCK")
        mcu.resolved_ports["MOSI"] = _make_port("mcu1", "spi", "MOSI")
        mcu.resolved_ports["MISO"] = _make_port("mcu1", "spi", "MISO")
        mcu.resolved_ports["CS"] = _make_port("mcu1", "spi", "CS")
        flash = _make_flash_instance()

        intent = ConnectionIntent(
            connection_id="conn_spi_1",
            src_module_intent="mcu1",
            dst_module_intent="flash1",
            signal_type=SignalType.SPI,
            connection_semantics=ConnectionSemantic.BUS_CONNECT,
        )

        instances = {"mcu1": mcu, "flash1": flash}
        conns, _, unresolved = resolve_all_connections(instances, [intent])

        spi_conns = [
            c for c in conns if c.rule_id == RULE_SPI_BUS.rule_id
        ]
        # 应至少映射出一些 SPI 连接
        assert len(spi_conns) >= 1

        # 验证有 net_name 包含 SPI
        spi_net_names = {c.net_name for c in spi_conns}
        assert any("SPI" in n for n in spi_net_names)


# ============================================================
# T056: GND merge
# ============================================================


class TestGndMerge:
    """T056: GND 网络合并。"""

    def test_all_modules_share_gnd(self) -> None:
        """所有模块的 GND 端口共享同一个 GND 网络。"""
        buck = _make_buck_instance()
        ldo = _make_ldo_instance()
        mcu = _make_mcu_instance()

        instances = {"buck1": buck, "ldo1": ldo, "mcu1": mcu}
        gnd_net = resolve_ground_strategy(instances)

        assert gnd_net.net_name == "GND"
        assert gnd_net.is_global is True
        assert gnd_net.net_type == NetType.GROUND
        # buck(GND) + ldo(GND) + mcu(VSS) = 3 个 GND 端口
        assert len(gnd_net.members) == 3

    def test_gnd_in_resolved_nets(self) -> None:
        """resolve_all_connections 输出的 nets 包含 GND。"""
        buck = _make_buck_instance()
        ldo = _make_ldo_instance()

        instances = {"buck1": buck, "ldo1": ldo}
        _, nets, _ = resolve_all_connections(instances, [])

        assert "GND" in nets
        assert nets["GND"].is_global is True

    def test_gnd_voltage_domain(self) -> None:
        """GND 网络电压域为 0V。"""
        instances = {"buck1": _make_buck_instance()}
        gnd_net = resolve_ground_strategy(instances)
        assert gnd_net.voltage_domain == "0V"


# ============================================================
# T057: EN/BOOT/FB special rules
# ============================================================


class TestSpecialPinRules:
    """T057: 特殊引脚规则。"""

    def test_en_pullup(self) -> None:
        """Buck 模块 EN 引脚自动添加上拉电阻。"""
        buck = _make_buck_instance()

        instances = {"buck1": buck}
        conns, _, _ = resolve_all_connections(instances, [])

        en_conns = [
            c for c in conns if c.rule_id == RULE_ENABLE_PULLUP.rule_id
        ]
        assert len(en_conns) == 1
        assert en_conns[0].src_port.pin_name == "EN"
        assert en_conns[0].dst_port.pin_name == "VIN"

        # 检查自动元件
        en_comps = [
            c for c in buck.external_components
            if c.get("role") == "en_pullup"
        ]
        assert len(en_comps) == 1
        assert en_comps[0]["value"] == "100000"

    def test_bootstrap_cap(self) -> None:
        """Buck 模块 BST-SW 之间自动添加自举电容。"""
        buck = _make_buck_instance()

        instances = {"buck1": buck}
        conns, _, _ = resolve_all_connections(instances, [])

        bst_conns = [
            c for c in conns if c.rule_id == RULE_BOOTSTRAP_CAP.rule_id
        ]
        assert len(bst_conns) == 1
        assert bst_conns[0].src_port.pin_name == "BST"
        assert bst_conns[0].dst_port.pin_name == "SW"

        # 检查自动元件
        bst_comps = [
            c for c in buck.external_components
            if c.get("role") == "boot_cap"
        ]
        assert len(bst_comps) == 1

    def test_fb_divider_component(self) -> None:
        """Buck 模块 FB 引脚自动生成分压器元件记录。"""
        buck = _make_buck_instance()

        instances = {"buck1": buck}
        resolve_all_connections(instances, [])

        fb_comps = [
            c for c in buck.external_components
            if c.get("role") == "fb_divider"
        ]
        assert len(fb_comps) == 1
        assert fb_comps[0]["type"] == "resistor_divider"


# ============================================================
# T058: Unresolved mechanism
# ============================================================


class TestUnresolvedMechanism:
    """T058: 未解析连接处理。"""

    def test_unknown_connection_produces_unresolved(self) -> None:
        """无匹配规则的连接意图产出 unresolved (C40)。"""
        buck = _make_buck_instance()
        ldo = _make_ldo_instance()

        # 使用不匹配任何规则的信号类型
        intent = ConnectionIntent(
            connection_id="conn_unknown_1",
            src_module_intent="buck1",
            src_port_hint="FB",
            dst_module_intent="ldo1",
            dst_port_hint="VOUT",
            signal_type=SignalType.UART,
        )

        instances = {"buck1": buck, "ldo1": ldo}
        _, _, unresolved = resolve_all_connections(instances, [intent])

        assert len(unresolved) >= 1
        unresolved_conn = [
            u for u in unresolved
            if u.get("connection_id") == "conn_unknown_1"
        ]
        assert len(unresolved_conn) == 1
        assert "reason" in unresolved_conn[0]

    def test_missing_src_module_unresolved(self) -> None:
        """源模块不存在时产出 unresolved。"""
        ldo = _make_ldo_instance()

        intent = ConnectionIntent(
            connection_id="conn_missing_src",
            src_module_intent="nonexistent",
            dst_module_intent="ldo1",
            signal_type=SignalType.POWER_SUPPLY,
        )

        instances = {"ldo1": ldo}
        _, _, unresolved = resolve_all_connections(instances, [intent])

        assert len(unresolved) >= 1
        assert any("nonexistent" in u.get("reason", "") for u in unresolved)

    def test_missing_dst_module_unresolved(self) -> None:
        """目标模块不存在时产出 unresolved。"""
        buck = _make_buck_instance()

        intent = ConnectionIntent(
            connection_id="conn_missing_dst",
            src_module_intent="buck1",
            dst_module_intent="nonexistent",
            signal_type=SignalType.POWER_SUPPLY,
        )

        instances = {"buck1": buck}
        _, _, unresolved = resolve_all_connections(instances, [intent])

        assert len(unresolved) >= 1
        assert any("nonexistent" in u.get("reason", "") for u in unresolved)

    def test_unresolved_has_required_fields(self) -> None:
        """unresolved 记录包含所有必要字段。"""
        buck = _make_buck_instance()

        intent = ConnectionIntent(
            connection_id="conn_bad",
            src_module_intent="buck1",
            dst_module_intent="missing",
            signal_type=SignalType.OTHER,
        )

        instances = {"buck1": buck}
        _, _, unresolved = resolve_all_connections(instances, [intent])

        assert len(unresolved) >= 1
        item = unresolved[0]
        assert "type" in item
        assert "connection_id" in item
        assert "reason" in item
        assert item["type"] == "unresolved_connection"


# ============================================================
# T059: explain_connection_rule
# ============================================================


class TestExplainConnectionRule:
    """T059: 连接规则解释。"""

    def test_explain_returns_readable_text(self) -> None:
        """explain_connection_rule 返回人文可读文本。"""
        conn = ResolvedConnection(
            resolved_connection_id="test_conn",
            src_port=_make_port("buck1", "power_out", "VOUT", NetType.POWER),
            dst_port=_make_port("ldo1", "power_in", "VIN", NetType.POWER),
            net_name="NET_5V",
            rule_id=RULE_POWER_SUPPLY.rule_id,
            evidence="电源供电链: VOUT → VIN",
        )

        text = explain_connection_rule(conn)
        assert "buck1" in text
        assert "ldo1" in text
        assert "VOUT" in text
        assert "VIN" in text
        assert "NET_5V" in text
        assert RULE_POWER_SUPPLY.rule_id in text

    def test_explain_no_rule_id(self) -> None:
        """无 rule_id 的连接也能生成解释。"""
        conn = ResolvedConnection(
            resolved_connection_id="manual_conn",
            src_port=_make_port("a", "other", "X"),
            dst_port=_make_port("b", "other", "Y"),
            net_name="NET_MANUAL",
            rule_id="",
            evidence="",
        )

        text = explain_connection_rule(conn)
        assert "无规则关联" in text
        assert "NET_MANUAL" in text

    def test_explain_includes_rule_name(self) -> None:
        """解释文本包含规则的中文名称。"""
        conn = ResolvedConnection(
            resolved_connection_id="gpio_conn",
            src_port=_make_port("mcu1", "gpio", "PA1"),
            dst_port=_make_port("led1", "other", "ANODE"),
            net_name="NET_PA1_led1",
            rule_id=RULE_GPIO_LED.rule_id,
            evidence="GPIO→LED",
        )

        text = explain_connection_rule(conn)
        assert RULE_GPIO_LED.name in text


# ============================================================
# T060: Priority / integration
# ============================================================


class TestPriorityAndIntegration:
    """T060: 优先级与集成测试。"""

    def test_power_rules_override_generic(self) -> None:
        """电源规则优先级高于通用规则 (C41)。"""
        assert RULE_POWER_SUPPLY.priority < RULE_GPIO_LED.priority
        assert RULE_POWER_SUPPLY.priority < RULE_SPI_BUS.priority

    def test_rule_id_recorded_on_connection(self) -> None:
        """每条已解析连接记录 rule_id (C37)。"""
        buck = _make_buck_instance()
        buck.resolved_ports["VOUT"] = _make_port(
            "buck1", "power_out", "VOUT", NetType.POWER,
        )
        ldo = _make_ldo_instance()

        intent = ConnectionIntent(
            connection_id="conn_trace",
            src_module_intent="buck1",
            dst_module_intent="ldo1",
            signal_type=SignalType.POWER_SUPPLY,
        )

        instances = {"buck1": buck, "ldo1": ldo}
        conns, _, _ = resolve_all_connections(instances, [intent])

        power_conns = [
            c for c in conns if c.resolved_connection_id == "conn_trace"
        ]
        assert len(power_conns) == 1
        assert power_conns[0].rule_id == RULE_POWER_SUPPLY.rule_id

    def test_full_system_integration(self) -> None:
        """完整系统集成: Buck → LDO → MCU → LED 全链路。"""
        buck = _make_buck_instance()
        buck.resolved_ports["VOUT"] = _make_port(
            "buck1", "power_out", "VOUT", NetType.POWER,
        )
        ldo = _make_ldo_instance()
        mcu = _make_mcu_instance()
        led = _make_led_instance()

        intents = [
            ConnectionIntent(
                connection_id="power_buck_ldo",
                src_module_intent="buck1",
                dst_module_intent="ldo1",
                signal_type=SignalType.POWER_SUPPLY,
            ),
            ConnectionIntent(
                connection_id="power_ldo_mcu",
                src_module_intent="ldo1",
                dst_module_intent="mcu1",
                signal_type=SignalType.POWER_SUPPLY,
            ),
            ConnectionIntent(
                connection_id="gpio_led",
                src_module_intent="mcu1",
                src_port_hint="PA1",
                dst_module_intent="led1",
                signal_type=SignalType.GPIO,
            ),
        ]

        instances = {
            "buck1": buck, "ldo1": ldo, "mcu1": mcu, "led1": led,
        }
        conns, nets, unresolved = resolve_all_connections(instances, intents)

        # 应有 GND 网络
        assert "GND" in nets
        assert len(nets["GND"].members) == 4  # buck, ldo, mcu(VSS), led(CATHODE)

        # 应有电源链连接
        power_conns = [
            c for c in conns if c.rule_id == RULE_POWER_SUPPLY.rule_id
        ]
        assert len(power_conns) == 2

        # 应有 GPIO→LED 连接
        gpio_conns = [
            c for c in conns if c.rule_id == RULE_GPIO_LED.rule_id
        ]
        assert len(gpio_conns) == 1

        # LED 应有限流电阻
        assert any(
            c.get("role") == "led_limit" for c in led.external_components
        )

        # Buck 应有特殊引脚连接（EN, BST）
        en_conns = [
            c for c in conns if c.rule_id == RULE_ENABLE_PULLUP.rule_id
        ]
        assert len(en_conns) == 1

        # 无 unresolved
        assert len(unresolved) == 0

    def test_real_devices_power_chain(self, real_store: ComponentStore) -> None:
        """用真实器件库验证 TPS5430.VOUT → AMS1117.VIN。"""
        from schemaforge.system.models import ModuleIntent

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

        # TPS5430 没有 VOUT 引脚（通过外围电感输出），
        # 但为了测试电源链规则，手动添加
        # 注意：实际系统中 buck 的输出由外围综合产出
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

        intent = ConnectionIntent(
            connection_id="real_power",
            src_module_intent="buck1",
            dst_module_intent="ldo1",
            signal_type=SignalType.POWER_SUPPLY,
        )

        instances = {"buck1": buck_inst, "ldo1": ldo_inst}
        conns, nets, unresolved = resolve_all_connections(instances, [intent])

        power_conns = [
            c for c in conns if c.rule_id == RULE_POWER_SUPPLY.rule_id
        ]
        assert len(power_conns) >= 1
        assert "5V" in power_conns[0].net_name
        assert len(unresolved) == 0
