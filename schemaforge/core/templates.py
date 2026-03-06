"""SchemaForge 模板注册表

所有电路模板的定义和注册。每个模板定义了：
- 参数（用户/AI需要填的东西）
- 元器件列表（固定）
- 网络连接（固定的拓扑约束）
- 布局提示
- 参数计算规则
"""

from __future__ import annotations

from schemaforge.core.models import (
    CircuitTemplate,
    ComponentDef,
    LayoutHint,
    Net,
    NetConnection,
    ParameterDef,
    PinDef,
    PinType,
)

# ============================================================
# 模板注册表
# ============================================================

TEMPLATE_REGISTRY: dict[str, CircuitTemplate] = {}


def register_template(template: CircuitTemplate) -> CircuitTemplate:
    """注册模板到全局注册表"""
    TEMPLATE_REGISTRY[template.name] = template
    return template


def get_template(name: str) -> CircuitTemplate | None:
    """根据名称获取模板"""
    return TEMPLATE_REGISTRY.get(name)


def list_templates() -> list[str]:
    """列出所有可用模板名"""
    return list(TEMPLATE_REGISTRY.keys())


# ============================================================
# 模板1：电压分压器
# ============================================================

VOLTAGE_DIVIDER = register_template(
    CircuitTemplate(
        name="voltage_divider",
        display_name="电压分压器",
        description="由两个电阻组成的分压电路，用于电压采样或产生参考电压",
        category="signal",
        parameters={
            "v_in": ParameterDef(
                name="v_in",
                display_name="输入电压",
                type="float",
                unit="V",
                default="5",
                min_val=0.1,
                max_val=100.0,
                description="输入电压",
            ),
            "v_out": ParameterDef(
                name="v_out",
                display_name="期望输出电压",
                type="float",
                unit="V",
                default="2.5",
                min_val=0.01,
                max_val=99.0,
                description="期望输出电压（必须小于输入电压）",
            ),
            "r_total": ParameterDef(
                name="r_total",
                display_name="总阻值预算",
                type="float",
                unit="kΩ",
                default="20",
                min_val=1.0,
                max_val=1000.0,
                description="R1+R2的总阻值预算，决定功耗",
            ),
        },
        components=[
            ComponentDef(
                ref_prefix="R",
                name="电阻",
                description="上端电阻R1",
                pins=[
                    PinDef(name="1", pin_type=PinType.PASSIVE, description="引脚1"),
                    PinDef(name="2", pin_type=PinType.PASSIVE, description="引脚2"),
                ],
                parameters={"value": "{r1_value}"},
                lcsc_part="C25879",
                schemdraw_element="Resistor",
                spice_model="R{ref} {p1} {p2} {value}",
            ),
            ComponentDef(
                ref_prefix="R",
                name="电阻",
                description="下端电阻R2",
                pins=[
                    PinDef(name="1", pin_type=PinType.PASSIVE, description="引脚1"),
                    PinDef(name="2", pin_type=PinType.PASSIVE, description="引脚2"),
                ],
                parameters={"value": "{r2_value}"},
                lcsc_part="C25879",
                schemdraw_element="Resistor",
                spice_model="R{ref} {p1} {p2} {value}",
            ),
        ],
        net_template=[
            Net(
                name="VIN",
                connections=[NetConnection(component_ref="R1", pin_name="1")],
                is_power=True,
            ),
            Net(
                name="VMID",
                connections=[
                    NetConnection(component_ref="R1", pin_name="2"),
                    NetConnection(component_ref="R2", pin_name="1"),
                ],
            ),
            Net(
                name="GND",
                connections=[NetConnection(component_ref="R2", pin_name="2")],
                is_ground=True,
            ),
        ],
        layout_hints=[
            LayoutHint(component_ref="R1", position="right"),
            LayoutHint(component_ref="R2", position="down", relative_to="R1"),
        ],
        calculations={
            "ratio": "v_out / v_in",
            "r2_value": "r_total * 1000 * ratio",
            "r1_value": "r_total * 1000 - r2_value",
        },
    )
)


# ============================================================
# 模板2：LDO线性稳压电路
# ============================================================

LDO_REGULATOR = register_template(
    CircuitTemplate(
        name="ldo_regulator",
        display_name="LDO/稳压器电路",
        description="通用稳压器电路（LDO/Buck/LM78xx等），支持自定义IC型号",
        category="power",
        parameters={
            "v_in": ParameterDef(
                name="v_in",
                display_name="输入电压",
                type="float",
                unit="V",
                default="5",
                min_val=0.5,
                max_val=100.0,
                description="输入电压",
            ),
            "v_out": ParameterDef(
                name="v_out",
                display_name="输出电压",
                type="choice",
                unit="V",
                default="3.3",
                choices=["1.2", "1.8", "2.5", "3.3", "5.0"],
                description="输出电压",
            ),
            "ic_model": ParameterDef(
                name="ic_model",
                display_name="IC型号",
                type="string",
                unit="",
                default="AMS1117",
                description="稳压器IC型号（如AMS1117, TPS54202, LM7805, MP2359等）",
            ),
            "c_in": ParameterDef(
                name="c_in",
                display_name="输入电容",
                type="string",
                unit="",
                default="10μF",
                description="输入滤波电容",
            ),
            "c_out": ParameterDef(
                name="c_out",
                display_name="输出电容",
                type="string",
                unit="",
                default="22μF",
                description="输出滤波电容",
            ),
        },
        components=[
            ComponentDef(
                ref_prefix="U",
                name="{ic_model}",
                description="稳压器IC",
                pins=[
                    PinDef(
                        name="VIN", pin_type=PinType.POWER_IN, description="输入电压"
                    ),
                    PinDef(
                        name="VOUT", pin_type=PinType.POWER_OUT, description="输出电压"
                    ),
                    PinDef(name="GND", pin_type=PinType.GROUND, description="接地"),
                ],
                parameters={"model": "{ic_model}-{v_out}"},
                lcsc_part="",
                schemdraw_element="Ic",
                spice_model="XU{ref} {VIN} {VOUT} {GND} {ic_model}",
            ),
            ComponentDef(
                ref_prefix="C",
                name="电容",
                description="输入滤波电容",
                pins=[
                    PinDef(name="1", pin_type=PinType.PASSIVE, description="正极"),
                    PinDef(name="2", pin_type=PinType.PASSIVE, description="负极"),
                ],
                parameters={"value": "{c_in}"},
                lcsc_part="C15849",
                schemdraw_element="Capacitor",
                spice_model="C{ref} {p1} {p2} {value}",
            ),
            ComponentDef(
                ref_prefix="C",
                name="电容",
                description="输出滤波电容",
                pins=[
                    PinDef(name="1", pin_type=PinType.PASSIVE, description="正极"),
                    PinDef(name="2", pin_type=PinType.PASSIVE, description="负极"),
                ],
                parameters={"value": "{c_out}"},
                lcsc_part="C159801",
                schemdraw_element="Capacitor",
                spice_model="C{ref} {p1} {p2} {value}",
            ),
        ],
        net_template=[
            Net(
                name="VIN",
                connections=[
                    NetConnection(component_ref="U1", pin_name="VIN"),
                    NetConnection(component_ref="C1", pin_name="1"),
                ],
                is_power=True,
            ),
            Net(
                name="VOUT",
                connections=[
                    NetConnection(component_ref="U1", pin_name="VOUT"),
                    NetConnection(component_ref="C2", pin_name="1"),
                ],
                is_power=True,
            ),
            Net(
                name="GND",
                connections=[
                    NetConnection(component_ref="U1", pin_name="GND"),
                    NetConnection(component_ref="C1", pin_name="2"),
                    NetConnection(component_ref="C2", pin_name="2"),
                ],
                is_ground=True,
            ),
        ],
        layout_hints=[
            LayoutHint(component_ref="C1", position="down"),
            LayoutHint(component_ref="U1", position="right"),
            LayoutHint(component_ref="C2", position="down"),
        ],
        calculations={},
    )
)


# ============================================================
# 模板3：LED指示灯电路
# ============================================================

LED_INDICATOR = register_template(
    CircuitTemplate(
        name="led_indicator",
        display_name="LED指示灯电路",
        description="带限流电阻的LED指示灯电路，常用于电源指示",
        category="signal",
        parameters={
            "v_supply": ParameterDef(
                name="v_supply",
                display_name="电源电压",
                type="float",
                unit="V",
                default="3.3",
                min_val=1.8,
                max_val=24.0,
                description="电源电压",
            ),
            "led_color": ParameterDef(
                name="led_color",
                display_name="LED颜色",
                type="choice",
                default="green",
                choices=["red", "green", "blue", "white"],
                description="LED颜色，决定正向压降",
            ),
            "led_current": ParameterDef(
                name="led_current",
                display_name="LED电流",
                type="float",
                unit="mA",
                default="10",
                min_val=1.0,
                max_val=30.0,
                description="LED工作电流",
            ),
        },
        components=[
            ComponentDef(
                ref_prefix="R",
                name="电阻",
                description="限流电阻",
                pins=[
                    PinDef(name="1", pin_type=PinType.PASSIVE, description="引脚1"),
                    PinDef(name="2", pin_type=PinType.PASSIVE, description="引脚2"),
                ],
                parameters={"value": "{r_limit_value}"},
                lcsc_part="C25079",
                schemdraw_element="Resistor",
                spice_model="R{ref} {p1} {p2} {value}",
            ),
            ComponentDef(
                ref_prefix="D",
                name="LED",
                description="发光二极管",
                pins=[
                    PinDef(name="anode", pin_type=PinType.PASSIVE, description="阳极"),
                    PinDef(
                        name="cathode", pin_type=PinType.PASSIVE, description="阴极"
                    ),
                ],
                parameters={"color": "{led_color}"},
                lcsc_part="C2297",
                schemdraw_element="LED",
                spice_model="D{ref} {anode} {cathode} LED_{color}",
            ),
        ],
        net_template=[
            Net(
                name="VCC",
                connections=[NetConnection(component_ref="R1", pin_name="1")],
                is_power=True,
            ),
            Net(
                name="LED_ANODE",
                connections=[
                    NetConnection(component_ref="R1", pin_name="2"),
                    NetConnection(component_ref="D1", pin_name="anode"),
                ],
            ),
            Net(
                name="GND",
                connections=[NetConnection(component_ref="D1", pin_name="cathode")],
                is_ground=True,
            ),
        ],
        layout_hints=[
            LayoutHint(component_ref="R1", position="right"),
            LayoutHint(component_ref="D1", position="right", relative_to="R1"),
        ],
        calculations={
            "v_forward": "{'red': 2.0, 'green': 2.2, 'blue': 3.0, 'white': 3.0}[led_color]",
            "r_limit_value": "(v_supply - v_forward) / (led_current / 1000)",
        },
    )
)


# ============================================================
# 模板4：RC低通滤波器
# ============================================================

RC_LOWPASS = register_template(
    CircuitTemplate(
        name="rc_lowpass",
        display_name="RC低通滤波器",
        description="一阶RC低通滤波器，用于信号滤波",
        category="filter",
        parameters={
            "f_cutoff": ParameterDef(
                name="f_cutoff",
                display_name="截止频率",
                type="float",
                unit="Hz",
                default="1000",
                min_val=0.1,
                max_val=10000000.0,
                description="3dB截止频率",
            ),
            "r_value": ParameterDef(
                name="r_value",
                display_name="电阻值",
                type="float",
                unit="kΩ",
                default="10",
                min_val=0.001,
                max_val=10000.0,
                description="电阻值（默认10kΩ）",
            ),
        },
        components=[
            ComponentDef(
                ref_prefix="R",
                name="电阻",
                description="滤波电阻",
                pins=[
                    PinDef(name="1", pin_type=PinType.PASSIVE, description="引脚1"),
                    PinDef(name="2", pin_type=PinType.PASSIVE, description="引脚2"),
                ],
                parameters={"value": "{r_value_ohm}"},
                lcsc_part="C25879",
                schemdraw_element="Resistor",
                spice_model="R{ref} {p1} {p2} {value}",
            ),
            ComponentDef(
                ref_prefix="C",
                name="电容",
                description="滤波电容",
                pins=[
                    PinDef(name="1", pin_type=PinType.PASSIVE, description="引脚1"),
                    PinDef(name="2", pin_type=PinType.PASSIVE, description="引脚2"),
                ],
                parameters={"value": "{c_value}"},
                lcsc_part="C14663",
                schemdraw_element="Capacitor",
                spice_model="C{ref} {p1} {p2} {value}",
            ),
        ],
        net_template=[
            Net(
                name="IN",
                connections=[NetConnection(component_ref="R1", pin_name="1")],
            ),
            Net(
                name="OUT",
                connections=[
                    NetConnection(component_ref="R1", pin_name="2"),
                    NetConnection(component_ref="C1", pin_name="1"),
                ],
            ),
            Net(
                name="GND",
                connections=[NetConnection(component_ref="C1", pin_name="2")],
                is_ground=True,
            ),
        ],
        layout_hints=[
            LayoutHint(component_ref="R1", position="right"),
            LayoutHint(component_ref="C1", position="down", relative_to="R1"),
        ],
        calculations={
            "r_value_ohm": "r_value * 1000",
            "c_value": "1 / (2 * 3.14159265 * f_cutoff * r_value * 1000)",
        },
    )
)


# ============================================================
# 模板5：Buck降压转换器
# ============================================================

BUCK_CONVERTER = register_template(
    CircuitTemplate(
        name="buck_converter",
        display_name="Buck降压转换器",
        description="异步Buck降压DC-DC转换器，含电感、续流二极管和反馈网络",
        category="power",
        parameters={
            "v_in": ParameterDef(
                name="v_in",
                display_name="输入电压",
                type="float",
                unit="V",
                default="12",
                min_val=3.0,
                max_val=60.0,
                description="输入电压",
            ),
            "v_out": ParameterDef(
                name="v_out",
                display_name="输出电压",
                type="float",
                unit="V",
                default="3.3",
                min_val=0.8,
                max_val=55.0,
                description="目标输出电压",
            ),
            "i_out_max": ParameterDef(
                name="i_out_max",
                display_name="最大输出电流",
                type="float",
                unit="A",
                default="1",
                min_val=0.1,
                max_val=10.0,
                description="最大负载电流",
            ),
            "ic_model": ParameterDef(
                name="ic_model",
                display_name="IC型号",
                type="string",
                unit="",
                default="TPS5430",
                description="Buck转换器IC型号",
            ),
            "c_in": ParameterDef(
                name="c_in",
                display_name="输入电容",
                type="string",
                unit="",
                default="10μF/50V",
                description="输入滤波电容",
            ),
            "c_out": ParameterDef(
                name="c_out",
                display_name="输出电容",
                type="string",
                unit="",
                default="47μF/10V",
                description="输出滤波电容",
            ),
            "l_value": ParameterDef(
                name="l_value",
                display_name="电感值",
                type="string",
                unit="",
                default="22μH",
                description="功率电感",
            ),
        },
        components=[
            ComponentDef(
                ref_prefix="U",
                name="{ic_model}",
                description="Buck转换器IC",
                pins=[
                    PinDef(
                        name="VIN", pin_type=PinType.POWER_IN, description="输入电压"
                    ),
                    PinDef(
                        name="SW", pin_type=PinType.POWER_OUT, description="开关节点"
                    ),
                    PinDef(name="FB", pin_type=PinType.INPUT, description="反馈引脚"),
                    PinDef(name="EN", pin_type=PinType.INPUT, description="使能引脚"),
                    PinDef(
                        name="BST", pin_type=PinType.PASSIVE, description="自举引脚"
                    ),
                    PinDef(name="GND", pin_type=PinType.GROUND, description="接地"),
                ],
                parameters={"model": "{ic_model}"},
                lcsc_part="",
                schemdraw_element="Ic",
                spice_model="XU{ref} {VIN} {GND} {EN} {BST} {SW} {FB} {ic_model}",
            ),
            ComponentDef(
                ref_prefix="C",
                name="电容",
                description="输入滤波电容",
                pins=[
                    PinDef(name="1", pin_type=PinType.PASSIVE, description="正极"),
                    PinDef(name="2", pin_type=PinType.PASSIVE, description="负极"),
                ],
                parameters={"value": "{c_in}"},
                lcsc_part="C15849",
                schemdraw_element="Capacitor",
                spice_model="C{ref} {p1} {p2} {value}",
            ),
            ComponentDef(
                ref_prefix="L",
                name="电感",
                description="功率电感",
                pins=[
                    PinDef(name="1", pin_type=PinType.PASSIVE, description="引脚1"),
                    PinDef(name="2", pin_type=PinType.PASSIVE, description="引脚2"),
                ],
                parameters={"value": "{l_value}"},
                lcsc_part="C408412",
                schemdraw_element="Inductor2",
                spice_model="L{ref} {p1} {p2} {value}",
            ),
            ComponentDef(
                ref_prefix="D",
                name="二极管",
                description="续流Schottky二极管",
                pins=[
                    PinDef(name="anode", pin_type=PinType.PASSIVE, description="阳极"),
                    PinDef(
                        name="cathode", pin_type=PinType.PASSIVE, description="阴极"
                    ),
                ],
                parameters={"model": "SS34"},
                lcsc_part="C8678",
                schemdraw_element="Diode",
                spice_model="D{ref} {anode} {cathode} SS34",
            ),
            ComponentDef(
                ref_prefix="C",
                name="电容",
                description="输出滤波电容",
                pins=[
                    PinDef(name="1", pin_type=PinType.PASSIVE, description="正极"),
                    PinDef(name="2", pin_type=PinType.PASSIVE, description="负极"),
                ],
                parameters={"value": "{c_out}"},
                lcsc_part="C159801",
                schemdraw_element="Capacitor",
                spice_model="C{ref} {p1} {p2} {value}",
            ),
            ComponentDef(
                ref_prefix="R",
                name="电阻",
                description="反馈上电阻",
                pins=[
                    PinDef(name="1", pin_type=PinType.PASSIVE, description="引脚1"),
                    PinDef(name="2", pin_type=PinType.PASSIVE, description="引脚2"),
                ],
                parameters={"value": "{r_fb_upper}"},
                lcsc_part="C25879",
                schemdraw_element="Resistor",
                spice_model="R{ref} {p1} {p2} {value}",
            ),
            ComponentDef(
                ref_prefix="R",
                name="电阻",
                description="反馈下电阻",
                pins=[
                    PinDef(name="1", pin_type=PinType.PASSIVE, description="引脚1"),
                    PinDef(name="2", pin_type=PinType.PASSIVE, description="引脚2"),
                ],
                parameters={"value": "{r_fb_lower}"},
                lcsc_part="C25879",
                schemdraw_element="Resistor",
                spice_model="R{ref} {p1} {p2} {value}",
            ),
        ],
        net_template=[
            Net(
                name="VIN",
                connections=[
                    NetConnection(component_ref="U1", pin_name="VIN"),
                    NetConnection(component_ref="C1", pin_name="1"),
                ],
                is_power=True,
            ),
            Net(
                name="SW",
                connections=[
                    NetConnection(component_ref="U1", pin_name="SW"),
                    NetConnection(component_ref="L1", pin_name="1"),
                    NetConnection(component_ref="D1", pin_name="cathode"),
                ],
            ),
            Net(
                name="VOUT",
                connections=[
                    NetConnection(component_ref="L1", pin_name="2"),
                    NetConnection(component_ref="C2", pin_name="1"),
                    NetConnection(component_ref="R1", pin_name="1"),
                ],
                is_power=True,
            ),
            Net(
                name="FB",
                connections=[
                    NetConnection(component_ref="U1", pin_name="FB"),
                    NetConnection(component_ref="R1", pin_name="2"),
                    NetConnection(component_ref="R2", pin_name="1"),
                ],
            ),
            Net(
                name="GND",
                connections=[
                    NetConnection(component_ref="U1", pin_name="GND"),
                    NetConnection(component_ref="C1", pin_name="2"),
                    NetConnection(component_ref="C2", pin_name="2"),
                    NetConnection(component_ref="D1", pin_name="anode"),
                    NetConnection(component_ref="R2", pin_name="2"),
                ],
                is_ground=True,
            ),
        ],
        layout_hints=[
            LayoutHint(component_ref="C1", position="left"),
            LayoutHint(component_ref="U1", position="right", relative_to="C1"),
            LayoutHint(component_ref="L1", position="right", relative_to="U1"),
            LayoutHint(component_ref="D1", position="down", relative_to="U1"),
            LayoutHint(component_ref="C2", position="right", relative_to="L1"),
            LayoutHint(component_ref="R1", position="down", relative_to="C2"),
            LayoutHint(component_ref="R2", position="down", relative_to="R1"),
        ],
        calculations={
            "duty_cycle": "v_out / v_in",
            "r_fb_upper": "10000 * (v_out / 1.22 - 1)",
            "r_fb_lower": "10000",
        },
    )
)
