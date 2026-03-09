"""Tests for schemaforge.system.resolver (T032-T039)。

使用真实器件库数据 (schemaforge/store) 验证器件解析与模块实例化。
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from schemaforge.library.models import DeviceModel
from schemaforge.library.store import ComponentStore
from schemaforge.system.models import (
    ModuleInstance,
    ModuleIntent,
    ModuleStatus,
    NetType,
    PortRef,
)
from schemaforge.system.resolver import (
    get_device_ports,
    get_power_ports,
    get_signal_ports,
    instantiate_module_from_device,
    resolve_alias_part,
    resolve_exact_part,
    resolve_part_candidates,
    validate_module_instance,
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


@pytest.fixture()
def alias_store(tmp_path: Path) -> ComponentStore:
    """包含带别名器件的临时库。"""
    dst = tmp_path / "alias_store"
    shutil.copytree(STORE_SRC, dst)
    store = ComponentStore(dst)
    store.rebuild_index()

    # 给 AMS1117-3.3 添加别名
    device = store.get_device("AMS1117-3.3")
    assert device is not None
    updated = device.model_copy(
        update={"aliases": ["AMS1117", "1117-3.3", "ams1117-3v3"]},
    )
    store.save_device(updated)
    return store


# ============================================================
# T032: resolve_exact_part
# ============================================================


class TestResolveExactPart:
    """T032: 精确型号查找。"""

    def test_tps5430_exact(self, real_store: ComponentStore) -> None:
        """TPS5430 精确命中。"""
        device = resolve_exact_part(real_store, "TPS5430")
        assert device is not None
        assert device.part_number == "TPS5430"
        assert device.category == "buck"

    def test_ams1117_exact(self, real_store: ComponentStore) -> None:
        """AMS1117-3.3 精确命中。"""
        device = resolve_exact_part(real_store, "AMS1117-3.3")
        assert device is not None
        assert device.part_number == "AMS1117-3.3"
        assert device.category == "ldo"

    def test_nonexistent_returns_none(self, real_store: ComponentStore) -> None:
        """不存在的型号返回 None。"""
        assert resolve_exact_part(real_store, "NONEXISTENT-999") is None

    def test_empty_part_number(self, real_store: ComponentStore) -> None:
        """空型号返回 None。"""
        assert resolve_exact_part(real_store, "") is None


# ============================================================
# T033: resolve_alias_part
# ============================================================


class TestResolveAliasPart:
    """T033: 别名查找。"""

    def test_alias_hit(self, alias_store: ComponentStore) -> None:
        """通过别名命中 AMS1117-3.3。"""
        device = resolve_alias_part(alias_store, "AMS1117")
        assert device is not None
        assert device.part_number == "AMS1117-3.3"

    def test_alias_case_insensitive(self, alias_store: ComponentStore) -> None:
        """别名匹配大小写不敏感。"""
        device = resolve_alias_part(alias_store, "ams1117-3v3")
        assert device is not None
        assert device.part_number == "AMS1117-3.3"

    def test_alias_miss(self, alias_store: ComponentStore) -> None:
        """不存在的别名返回 None。"""
        assert resolve_alias_part(alias_store, "BOGUS_ALIAS") is None

    def test_alias_empty(self, real_store: ComponentStore) -> None:
        """空别名返回 None。"""
        assert resolve_alias_part(real_store, "") is None


# ============================================================
# T034: resolve_part_candidates
# ============================================================


class TestResolvePartCandidates:
    """T034: 候选器件搜索。"""

    def test_exact_first(self, real_store: ComponentStore) -> None:
        """part_number_hint 精确命中时直接返回单元素列表。"""
        intent = ModuleIntent(
            intent_id="buck1",
            role="降压",
            part_number_hint="TPS5430",
        )
        result = resolve_part_candidates(real_store, intent)
        assert len(result) == 1
        assert result[0].part_number == "TPS5430"

    def test_alias_fallback(self, alias_store: ComponentStore) -> None:
        """part_number_hint 精确未命中，回退别名。"""
        intent = ModuleIntent(
            intent_id="ldo1",
            role="稳压",
            part_number_hint="AMS1117",
        )
        result = resolve_part_candidates(alias_store, intent)
        assert len(result) == 1
        assert result[0].part_number == "AMS1117-3.3"

    def test_explicit_part_does_not_fallback_to_category(
        self, real_store: ComponentStore,
    ) -> None:
        """显式料号未命中时，不允许再回退到同类近似器件。"""
        intent = ModuleIntent(
            intent_id="buck1",
            role="降压",
            part_number_hint="TPS5450",
            category_hint="buck",
            electrical_targets={"v_in": "20", "v_out": "3.3"},
        )

        result = resolve_part_candidates(real_store, intent)

        assert result == []

    def test_explicit_family_variant_can_resolve_unique_match(
        self, real_store: ComponentStore,
    ) -> None:
        """显式家族名只在唯一可证明变体时允许落到具体型号。"""
        intent = ModuleIntent(
            intent_id="ldo1",
            role="稳压",
            part_number_hint="AMS1117",
            category_hint="ldo",
            electrical_targets={"v_out": "3.3"},
        )

        result = resolve_part_candidates(real_store, intent)

        assert len(result) == 1
        assert result[0].part_number == "AMS1117-3.3"

    def test_category_search(self, real_store: ComponentStore) -> None:
        """按类别搜索 buck 器件。"""
        intent = ModuleIntent(
            intent_id="buck1",
            role="降压",
            category_hint="buck",
            electrical_targets={"v_in": "24", "v_out": "5"},
        )
        result = resolve_part_candidates(real_store, intent)
        assert len(result) >= 1
        # 所有结果都是 buck 类别
        for d in result:
            assert d.category == "buck"

    def test_empty_category_returns_empty(
        self, real_store: ComponentStore,
    ) -> None:
        """无 part_number_hint 且无 category_hint 返回空列表。"""
        intent = ModuleIntent(intent_id="x1", role="未知")
        result = resolve_part_candidates(real_store, intent)
        assert result == []


# ============================================================
# T035: get_device_ports
# ============================================================


class TestGetDevicePorts:
    """T035: 器件引脚 → 语义端口映射。"""

    def test_tps5430_ports(self, real_store: ComponentStore) -> None:
        """TPS5430 的引脚映射正确，且暴露 topology 衍生的 VOUT。"""
        device = resolve_exact_part(real_store, "TPS5430")
        assert device is not None
        ports = get_device_ports(device)

        assert "VIN" in ports
        assert ports["VIN"].port_role == "power_in"
        assert ports["VIN"].net_class == NetType.POWER

        assert "GND" in ports
        assert ports["GND"].port_role == "ground"
        assert ports["GND"].net_class == NetType.GROUND

        assert "SW" in ports
        assert ports["SW"].port_role == "switch"

        assert "FB" in ports
        assert ports["FB"].port_role == "feedback"

        assert "BST" in ports
        assert ports["BST"].port_role == "bootstrap"

        assert "EN" in ports
        assert ports["EN"].port_role == "enable"

        assert "VOUT" in ports
        assert ports["VOUT"].port_role == "power_out"

    def test_ams1117_ports(self, real_store: ComponentStore) -> None:
        """AMS1117-3.3 的引脚映射：VIN(power_in), VOUT(power_out), GND(ground)。"""
        device = resolve_exact_part(real_store, "AMS1117-3.3")
        assert device is not None
        ports = get_device_ports(device)

        assert ports["VIN"].port_role == "power_in"
        assert ports["VOUT"].port_role == "power_out"
        assert ports["GND"].port_role == "ground"

    def test_stm32_gpio_ports(self, real_store: ComponentStore) -> None:
        """STM32F103C8T6 的 GPIO 引脚被识别为 gpio 角色。"""
        device = resolve_exact_part(real_store, "STM32F103C8T6")
        assert device is not None
        ports = get_device_ports(device)

        # PA0-WKUP 应该被识别为 GPIO
        assert "PA0-WKUP" in ports
        assert ports["PA0-WKUP"].port_role == "gpio"

        assert "PA1" in ports
        assert ports["PA1"].port_role == "gpio"

        # PB6 应识别为 GPIO（虽然描述含 I2C1_SCL，但引脚名是 PB6）
        assert "PB6" in ports
        assert ports["PB6"].port_role == "gpio"

    def test_stm32_vdd_vss_ports(self, real_store: ComponentStore) -> None:
        """STM32 的 VDD_1/VSS_1 等带后缀的电源引脚正确识别。"""
        device = resolve_exact_part(real_store, "STM32F103C8T6")
        assert device is not None
        ports = get_device_ports(device)

        assert "VDD_1" in ports
        assert ports["VDD_1"].port_role == "power_in"
        assert ports["VDD_1"].net_class == NetType.POWER

        assert "VSS_1" in ports
        assert ports["VSS_1"].port_role == "ground"
        assert ports["VSS_1"].net_class == NetType.GROUND

    def test_no_symbol_returns_empty(self) -> None:
        """无 symbol 的器件返回空字典。"""
        device = DeviceModel(part_number="NO_SYMBOL", symbol=None)
        assert get_device_ports(device) == {}

    def test_led_virtual_ports_from_topology(self, real_store: ComponentStore) -> None:
        device = resolve_exact_part(real_store, "LED_INDICATOR")
        assert device is not None
        ports = get_device_ports(device)

        assert "ANODE" in ports
        assert ports["ANODE"].port_role == "other"
        assert "GND" in ports
        assert ports["GND"].port_role == "ground"


# ============================================================
# T036: get_power_ports
# ============================================================


class TestGetPowerPorts:
    """T036: 电源端口提取。"""

    def test_tps5430_power(self, real_store: ComponentStore) -> None:
        """TPS5430 电源端口含 VIN / VOUT / GND。"""
        device = resolve_exact_part(real_store, "TPS5430")
        assert device is not None
        power = get_power_ports(device)

        assert "VIN" in power
        assert "VOUT" in power
        assert "GND" in power
        # SW 不属于 power_in/power_out/ground，不在 power_ports 里
        assert "SW" not in power
        assert "FB" not in power

    def test_ams1117_power(self, real_store: ComponentStore) -> None:
        """AMS1117 电源端口含 VIN, VOUT, GND。"""
        device = resolve_exact_part(real_store, "AMS1117-3.3")
        assert device is not None
        power = get_power_ports(device)

        assert "VIN" in power
        assert "VOUT" in power
        assert "GND" in power
        assert len(power) == 3


# ============================================================
# T037: get_signal_ports
# ============================================================


class TestGetSignalPorts:
    """T037: 信号端口提取。"""

    def test_stm32_signals(self, real_store: ComponentStore) -> None:
        """STM32 有大量 GPIO 信号端口。"""
        device = resolve_exact_part(real_store, "STM32F103C8T6")
        assert device is not None
        signals = get_signal_ports(device)

        # 应有多个 GPIO 端口
        gpio_ports = [p for p in signals.values() if p.port_role == "gpio"]
        assert len(gpio_ports) >= 10

    def test_tps5430_no_signal(self, real_store: ComponentStore) -> None:
        """TPS5430 无 GPIO/SPI/I2C/UART 信号端口。"""
        device = resolve_exact_part(real_store, "TPS5430")
        assert device is not None
        signals = get_signal_ports(device)
        assert len(signals) == 0


# ============================================================
# T038: instantiate_module_from_device
# ============================================================


class TestInstantiateModule:
    """T038: 模块实例化。"""

    def test_basic_instantiation(self, real_store: ComponentStore) -> None:
        """基本实例化：intent + device → ModuleInstance。"""
        intent = ModuleIntent(
            intent_id="buck1",
            role="第一级降压",
            part_number_hint="TPS5430",
            category_hint="buck",
            electrical_targets={"v_in": "24", "v_out": "5", "i_out": "2"},
        )
        device = resolve_exact_part(real_store, "TPS5430")
        assert device is not None

        instance = instantiate_module_from_device(intent, device)

        assert instance.module_id == "buck1"
        assert instance.role == "第一级降压"
        assert instance.status == ModuleStatus.RESOLVED
        assert instance.device is device
        assert instance.resolved_category == "buck"

        # 参数继承
        assert instance.parameters["v_in"] == "24"
        assert instance.parameters["v_out"] == "5"
        assert instance.parameters["i_out"] == "2"

        # 端口已映射
        assert "VIN" in instance.resolved_ports
        assert instance.resolved_ports["VIN"].module_id == "buck1"
        assert instance.resolved_ports["GND"].port_role == "ground"

    def test_evidence_recorded(self, real_store: ComponentStore) -> None:
        """实例化记录 evidence。"""
        intent = ModuleIntent(intent_id="ldo1", role="稳压")
        device = resolve_exact_part(real_store, "AMS1117-3.3")
        assert device is not None

        instance = instantiate_module_from_device(intent, device)
        assert any("AMS1117-3.3" in e for e in instance.evidence)


# ============================================================
# T039: validate_module_instance
# ============================================================


class TestValidateModuleInstance:
    """T039: 模块实例校验。"""

    def test_valid_instance(self, real_store: ComponentStore) -> None:
        """有效实例校验通过（无错误）。"""
        intent = ModuleIntent(
            intent_id="buck1",
            role="降压",
            category_hint="buck",
            electrical_targets={"v_in": "24", "v_out": "5"},
        )
        device = resolve_exact_part(real_store, "TPS5430")
        assert device is not None
        instance = instantiate_module_from_device(intent, device)
        errors = validate_module_instance(instance)
        assert errors == []

    def test_missing_gnd(self) -> None:
        """缺少 GND 端口报错。"""
        # 构造一个只有 VIN 的假器件
        instance = ModuleInstance(
            module_id="test1",
            role="test",
            resolved_ports={
                "VIN": PortRef(
                    module_id="test1",
                    port_role="power_in",
                    pin_name="VIN",
                    net_class=NetType.POWER,
                ),
            },
            status=ModuleStatus.RESOLVED,
        )
        errors = validate_module_instance(instance)
        assert any("GND" in e or "接地" in e for e in errors)

    def test_missing_power(self) -> None:
        """缺少电源端口报错。"""
        instance = ModuleInstance(
            module_id="test2",
            role="test",
            resolved_ports={
                "GND": PortRef(
                    module_id="test2",
                    port_role="ground",
                    pin_name="GND",
                    net_class=NetType.GROUND,
                ),
            },
            status=ModuleStatus.RESOLVED,
        )
        errors = validate_module_instance(instance)
        assert any("电源" in e or "power" in e for e in errors)

    def test_no_ports(self) -> None:
        """无端口映射报错。"""
        instance = ModuleInstance(
            module_id="empty1",
            role="empty",
            resolved_ports={},
            status=ModuleStatus.RESOLVED,
        )
        errors = validate_module_instance(instance)
        assert len(errors) >= 1
        assert any("无端口" in e for e in errors)

    def test_invalid_target_key(self) -> None:
        """非法 electrical_target key 报错。"""
        instance = ModuleInstance(
            module_id="test3",
            role="test",
            resolved_ports={
                "VIN": PortRef(
                    module_id="test3",
                    port_role="power_in",
                    pin_name="VIN",
                    net_class=NetType.POWER,
                ),
                "GND": PortRef(
                    module_id="test3",
                    port_role="ground",
                    pin_name="GND",
                    net_class=NetType.GROUND,
                ),
            },
            parameters={"bogus_param": "999"},
            status=ModuleStatus.RESOLVED,
        )
        errors = validate_module_instance(instance)
        assert any("bogus_param" in e for e in errors)

    def test_category_mismatch(self, real_store: ComponentStore) -> None:
        """器件类别与 resolved_category 不匹配报错。"""
        device = resolve_exact_part(real_store, "TPS5430")
        assert device is not None
        intent = ModuleIntent(
            intent_id="ldo_wrong",
            role="稳压",
            category_hint="ldo",
        )
        instance = instantiate_module_from_device(intent, device)
        # 手动覆盖 resolved_category 制造冲突
        instance.resolved_category = "ldo"
        errors = validate_module_instance(instance)
        assert any("类别不匹配" in e for e in errors)
