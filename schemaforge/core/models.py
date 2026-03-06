"""SchemaForge 核心数据模型

定义所有电路设计相关的Pydantic数据模型：
- 引脚模型（PinType, PinDef）
- 元器件模型（ComponentDef）
- 网络模型（NetConnection, Net）
- 电路实例模型（ComponentInstance, CircuitInstance）
- 电路模板模型（ParameterDef, LayoutHint, CircuitTemplate）
- AI输出模型（ModuleSpec, ConnectionSpec, DesignSpec）
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ============================================================
# 引脚模型
# ============================================================

class PinType(str, Enum):
    """引脚电气类型——决定ERC规则"""
    POWER_IN = "power_in"        # 电源输入（VIN, VCC）
    POWER_OUT = "power_out"      # 电源输出（VOUT）
    GROUND = "ground"            # 地
    INPUT = "input"              # 信号输入
    OUTPUT = "output"            # 信号输出
    PASSIVE = "passive"          # 无源（电阻/电容的引脚）
    BIDIRECTIONAL = "bidirectional"
    NO_CONNECT = "no_connect"


class PinDef(BaseModel):
    """引脚定义——模板中每个元器件引脚的静态描述"""
    name: str                    # 引脚名（如 "VIN", "VOUT", "GND", "1", "2"）
    pin_type: PinType            # 电气类型
    required: bool = True        # 是否必须连接（NC引脚为False）
    description: str = ""        # 描述


# ============================================================
# 元器件模型
# ============================================================

class ComponentDef(BaseModel):
    """元器件定义——模板中使用的元器件类型"""
    ref_prefix: str              # 参考标号前缀（R, C, U, D, LED）
    name: str                    # 器件名（如 "AMS1117-3.3", "电阻", "LED"）
    description: str = ""        # 描述
    pins: list[PinDef] = Field(default_factory=list)
    parameters: dict[str, str] = Field(default_factory=dict)
    lcsc_part: str = ""          # LCSC器件编号
    schemdraw_element: str = ""  # 对应的schemdraw元素名
    spice_model: str = ""        # SPICE模型模板


# ============================================================
# 网络（Net）模型
# ============================================================

class NetConnection(BaseModel):
    """网络连接点——标识"哪个器件的哪个引脚" """
    component_ref: str           # 器件参考标号（如 "U1", "R1", "C1"）
    pin_name: str                # 引脚名（如 "VIN", "1", "GND"）


class Net(BaseModel):
    """网络——一组电气相连的引脚

    每个Net代表一根"电气导线"，Net内所有引脚互相连通。
    """
    name: str                    # 网络名（如 "VCC_5V", "GND", "VOUT_3V3"）
    connections: list[NetConnection] = Field(default_factory=list)
    is_power: bool = False       # 是否为电源网络
    is_ground: bool = False      # 是否为地网络


# ============================================================
# 电路实例模型
# ============================================================

class ComponentInstance(BaseModel):
    """电路中的一个器件实例"""
    ref: str                     # 参考标号（如 "U1", "R1"）
    component_type: str          # 引用ComponentDef的name
    parameters: dict[str, str] = Field(default_factory=dict)


class CircuitInstance(BaseModel):
    """完整的电路实例——从模板实例化后的结果"""
    name: str
    description: str = ""
    components: list[ComponentInstance] = Field(default_factory=list)
    nets: list[Net] = Field(default_factory=list)
    template_name: str = ""
    input_parameters: dict[str, Any] = Field(default_factory=dict)
    calculated_values: dict[str, Any] = Field(default_factory=dict)


# ============================================================
# 电路模板模型
# ============================================================

class ParameterDef(BaseModel):
    """模板参数定义"""
    name: str
    display_name: str = ""
    type: str = "float"          # "float", "int", "string", "choice"
    unit: str = ""               # 单位（V, A, Ω, F）
    default: str = ""
    min_val: float | None = None
    max_val: float | None = None
    choices: list[str] = Field(default_factory=list)
    description: str = ""


class LayoutHint(BaseModel):
    """布局提示——告诉渲染器器件的相对位置"""
    component_ref: str
    position: str = "right"      # "right", "down", "left", "up"
    relative_to: str = ""
    at_pin: str = ""


class CircuitTemplate(BaseModel):
    """电路模板——预定义的电路拓扑

    关键设计：模板定义了所有合法的连接关系。
    AI只能"选择模板+填参数"，不能自己发明连接。
    """
    name: str                    # 模板名（如 "ldo_regulator"）
    display_name: str = ""       # 显示名（如 "LDO线性稳压电路"）
    description: str = ""
    category: str = ""           # 分类（power, signal, interface, filter）

    parameters: dict[str, ParameterDef] = Field(default_factory=dict)
    components: list[ComponentDef] = Field(default_factory=list)
    net_template: list[Net] = Field(default_factory=list)
    layout_hints: list[LayoutHint] = Field(default_factory=list)
    calculations: dict[str, str] = Field(default_factory=dict)


# ============================================================
# AI 输出模型（用于验证LLM返回的JSON）
# ============================================================

class ModuleSpec(BaseModel):
    """AI输出中的一个模块规格"""
    template: str
    instance_name: str
    parameters: dict[str, str] = Field(default_factory=dict)


class ConnectionSpec(BaseModel):
    """AI输出中的模块间连接"""
    from_module: str
    from_net: str
    to_module: str
    to_net: str
    merged_net_name: str = ""


class DesignSpec(BaseModel):
    """AI输出的完整设计规格"""
    design_name: str
    description: str = ""
    modules: list[ModuleSpec] = Field(default_factory=list)
    connections: list[ConnectionSpec] = Field(default_factory=list)
    notes: str = ""


# ============================================================
# ERC 错误模型
# ============================================================

class ERCSeverity(str, Enum):
    """ERC错误严重级别"""
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class ERCError(BaseModel):
    """ERC检查发现的一个问题"""
    rule: str                    # 规则名
    severity: ERCSeverity = ERCSeverity.ERROR
    message: str = ""
    component_ref: str = ""
    net_name: str = ""
    pin_name: str = ""
