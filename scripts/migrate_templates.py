"""SchemaForge 模板迁移脚本

将 schemaforge.core.templates 中的 4 个现有模板转换为 DeviceModel JSON 文件，
保存到 schemaforge/store/devices/ 目录。

用法:
    python scripts/migrate_templates.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# 确保项目根目录在搜索路径中
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from schemaforge.core.models import ParameterDef, PinType  # noqa: E402
from schemaforge.library.models import (  # noqa: E402
    DeviceModel,
    ExternalComponent,
    PinSide,
    SymbolDef,
    SymbolPin,
    TopologyConnection,
    TopologyDef,
)
from schemaforge.library.store import ComponentStore  # noqa: E402


def migrate_ldo_regulator() -> DeviceModel:
    """将 ldo_regulator 模板转换为 AMS1117-3.3 DeviceModel"""
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
                    name="VIN",
                    pin_number="3",
                    side=PinSide.LEFT,
                    pin_type=PinType.POWER_IN,
                    slot="1/3",
                    description="输入电压",
                ),
                SymbolPin(
                    name="VOUT",
                    pin_number="2",
                    side=PinSide.RIGHT,
                    pin_type=PinType.POWER_OUT,
                    slot="1/3",
                    description="输出电压",
                ),
                SymbolPin(
                    name="GND",
                    pin_number="1",
                    side=PinSide.BOTTOM,
                    pin_type=PinType.GROUND,
                    slot="1/1",
                    description="接地",
                ),
            ],
            size=(4, 3),
        ),
        topology=TopologyDef(
            circuit_type="ldo",
            external_components=[
                ExternalComponent(
                    role="input_cap",
                    ref_prefix="C",
                    required=True,
                    default_value="10uF",
                    value_expression="{c_in}",
                    schemdraw_element="Capacitor",
                ),
                ExternalComponent(
                    role="output_cap",
                    ref_prefix="C",
                    required=True,
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
                "v_in": ParameterDef(
                    name="v_in",
                    display_name="输入电压",
                    type="float",
                    unit="V",
                    default="5",
                    min_val=0.5,
                    max_val=100.0,
                ),
                "c_in": ParameterDef(
                    name="c_in",
                    display_name="输入电容",
                    type="string",
                    default="10uF",
                ),
                "c_out": ParameterDef(
                    name="c_out",
                    display_name="输出电容",
                    type="string",
                    default="22uF",
                ),
            },
        ),
        spice_model="XU{ref} {VIN} {VOUT} {GND} AMS1117",
        lcsc_part="C347222",
        package="SOT-223",
        source="migrated",
        notes="从 ldo_regulator 模板迁移",
    )


def migrate_voltage_divider() -> DeviceModel:
    """将 voltage_divider 模板转换为通用分压器 DeviceModel"""
    return DeviceModel(
        part_number="VOLTAGE_DIVIDER",
        manufacturer="",
        description="电压分压器电路（两个电阻组成）",
        category="passive_circuit",
        topology=TopologyDef(
            circuit_type="voltage_divider",
            external_components=[
                ExternalComponent(
                    role="r_upper",
                    ref_prefix="R",
                    required=True,
                    default_value="10k",
                    value_expression="{r1_value}",
                    schemdraw_element="Resistor",
                ),
                ExternalComponent(
                    role="r_lower",
                    ref_prefix="R",
                    required=True,
                    default_value="10k",
                    value_expression="{r2_value}",
                    schemdraw_element="Resistor",
                ),
            ],
            connections=[
                TopologyConnection(
                    net_name="VIN",
                    external_refs=["r_upper.1"],
                    is_power=True,
                ),
                TopologyConnection(
                    net_name="VMID",
                    external_refs=["r_upper.2", "r_lower.1"],
                ),
                TopologyConnection(
                    net_name="GND",
                    external_refs=["r_lower.2"],
                    is_ground=True,
                ),
            ],
            parameters={
                "v_in": ParameterDef(
                    name="v_in",
                    display_name="输入电压",
                    type="float",
                    unit="V",
                    default="5",
                    min_val=0.1,
                    max_val=100.0,
                ),
                "v_out": ParameterDef(
                    name="v_out",
                    display_name="期望输出电压",
                    type="float",
                    unit="V",
                    default="2.5",
                    min_val=0.01,
                    max_val=99.0,
                ),
                "r_total": ParameterDef(
                    name="r_total",
                    display_name="总阻值预算",
                    type="float",
                    unit="kOhm",
                    default="20",
                    min_val=1.0,
                    max_val=1000.0,
                ),
            },
            calculations={
                "ratio": "v_out / v_in",
                "r2_value": "r_total * 1000 * ratio",
                "r1_value": "r_total * 1000 - r2_value",
            },
        ),
        source="migrated",
        notes="从 voltage_divider 模板迁移",
    )


def migrate_led_indicator() -> DeviceModel:
    """将 led_indicator 模板转换为 LED DeviceModel"""
    return DeviceModel(
        part_number="LED_INDICATOR",
        manufacturer="",
        description="LED指示灯电路（带限流电阻）",
        category="led",
        specs={
            "v_forward_red": "2.0V",
            "v_forward_green": "2.2V",
            "v_forward_blue": "3.0V",
            "v_forward_white": "3.0V",
        },
        topology=TopologyDef(
            circuit_type="led_driver",
            external_components=[
                ExternalComponent(
                    role="current_limit_resistor",
                    ref_prefix="R",
                    required=True,
                    default_value="120",
                    value_expression="{r_limit_value}",
                    schemdraw_element="Resistor",
                ),
            ],
            connections=[
                TopologyConnection(
                    net_name="VCC",
                    external_refs=["current_limit_resistor.1"],
                    is_power=True,
                ),
                TopologyConnection(
                    net_name="LED_ANODE",
                    external_refs=["current_limit_resistor.2"],
                ),
                TopologyConnection(
                    net_name="GND",
                    is_ground=True,
                ),
            ],
            parameters={
                "v_supply": ParameterDef(
                    name="v_supply",
                    display_name="电源电压",
                    type="float",
                    unit="V",
                    default="3.3",
                    min_val=1.8,
                    max_val=24.0,
                ),
                "led_color": ParameterDef(
                    name="led_color",
                    display_name="LED颜色",
                    type="choice",
                    default="green",
                    choices=["red", "green", "blue", "white"],
                ),
                "led_current": ParameterDef(
                    name="led_current",
                    display_name="LED电流",
                    type="float",
                    unit="mA",
                    default="10",
                    min_val=1.0,
                    max_val=30.0,
                ),
            },
            calculations={
                "v_forward": "{'red': 2.0, 'green': 2.2, 'blue': 3.0, 'white': 3.0}[led_color]",
                "r_limit_value": "(v_supply - v_forward) / (led_current / 1000)",
            },
        ),
        lcsc_part="C2297",
        package="0805",
        source="migrated",
        notes="从 led_indicator 模板迁移",
    )


def migrate_rc_lowpass() -> DeviceModel:
    """将 rc_lowpass 模板转换为 RC低通滤波器 DeviceModel"""
    return DeviceModel(
        part_number="RC_LOWPASS",
        manufacturer="",
        description="一阶RC低通滤波器",
        category="passive_circuit",
        topology=TopologyDef(
            circuit_type="rc_filter",
            external_components=[
                ExternalComponent(
                    role="filter_resistor",
                    ref_prefix="R",
                    required=True,
                    default_value="10k",
                    value_expression="{r_value_ohm}",
                    schemdraw_element="Resistor",
                ),
                ExternalComponent(
                    role="filter_capacitor",
                    ref_prefix="C",
                    required=True,
                    default_value="15.9nF",
                    value_expression="{c_value}",
                    schemdraw_element="Capacitor",
                ),
            ],
            connections=[
                TopologyConnection(
                    net_name="IN",
                    external_refs=["filter_resistor.1"],
                ),
                TopologyConnection(
                    net_name="OUT",
                    external_refs=["filter_resistor.2", "filter_capacitor.1"],
                ),
                TopologyConnection(
                    net_name="GND",
                    external_refs=["filter_capacitor.2"],
                    is_ground=True,
                ),
            ],
            parameters={
                "f_cutoff": ParameterDef(
                    name="f_cutoff",
                    display_name="截止频率",
                    type="float",
                    unit="Hz",
                    default="1000",
                    min_val=0.1,
                    max_val=10000000.0,
                ),
                "r_value": ParameterDef(
                    name="r_value",
                    display_name="电阻值",
                    type="float",
                    unit="kOhm",
                    default="10",
                    min_val=0.001,
                    max_val=10000.0,
                ),
            },
            calculations={
                "r_value_ohm": "r_value * 1000",
                "c_value": "1 / (2 * 3.14159265 * f_cutoff * r_value * 1000)",
            },
        ),
        source="migrated",
        notes="从 rc_lowpass 模板迁移",
    )


def main() -> None:
    """执行迁移：将4个模板转换为 DeviceModel JSON 文件"""
    store_dir = PROJECT_ROOT / "schemaforge" / "store"
    store = ComponentStore(store_dir)

    devices = [
        migrate_ldo_regulator(),
        migrate_voltage_divider(),
        migrate_led_indicator(),
        migrate_rc_lowpass(),
    ]

    print(f"迁移目标目录: {store.devices_dir}")
    print(f"SQLite索引: {store.db_path}")
    print()

    for device in devices:
        path = store.save_device(device)
        print(f"  [OK] {device.part_number:<20s} -> {path.name}")

    print()
    print(f"迁移完成: {len(devices)} 个器件已保存")
    print(f"索引中共有: {len(store.list_devices())} 个器件")


if __name__ == "__main__":
    main()
