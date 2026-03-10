"""SchemaForge 器件库数据模型

定义器件库的核心模型：
- SymbolPin / SymbolDef -- 符号引脚与符号定义
- ExternalComponent -- 拓扑中的外部器件
- TopologyConnection / TopologyDef -- 推荐应用电路拓扑
- DeviceModel -- 完整器件模型（器件库的核心单元）
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from schemaforge.core.models import ParameterDef, PinType


# ============================================================
# 符号引脚方位
# ============================================================


class PinSide(str, Enum):
    """引脚在IC符号上的方位"""

    LEFT = "left"
    RIGHT = "right"
    TOP = "top"
    BOTTOM = "bottom"


# ============================================================
# 符号引脚定义
# ============================================================


class SymbolPin(BaseModel):
    """符号引脚定义 -- 用于 schemdraw Ic/IcPin 重建"""

    name: str  # "VIN", "VOUT", "GND"
    pin_number: str = ""  # 物理引脚编号
    side: PinSide = PinSide.LEFT
    pin_type: PinType = PinType.PASSIVE
    slot: str = ""  # "2/3" 位置格式
    inverted: bool = False
    anchor_name: str = ""  # 覆盖锚点名（用于特殊字符）
    description: str = ""


# ============================================================
# 符号定义
# ============================================================


class SymbolDef(BaseModel):
    """符号定义 -- 序列化后可重建 elm.Ic()"""

    pins: list[SymbolPin]
    size: tuple[float, float] | None = None
    edge_pad_w: float = 0.5
    edge_pad_h: float = 0.5
    pin_spacing: float = 1.0
    lead_len: float = 0.5
    label_position: str = "top"  # IC名称标签位置


# ============================================================
# 外部器件
# ============================================================


class ExternalComponent(BaseModel):
    """拓扑中的外部器件"""

    role: str  # "input_cap", "output_cap", "inductor" 等
    ref_prefix: str  # "C", "R", "L"
    required: bool = True
    default_value: str = ""  # "10uF", "4.7uH"
    value_expression: str = ""  # "{c_out}" -- 从参数解析
    constraints: dict[str, str] = Field(default_factory=dict)
    schemdraw_element: str = ""  # "Capacitor", "Resistor", "Inductor", "LED"


# ============================================================
# 拓扑连接
# ============================================================


class TopologyConnection(BaseModel):
    """拓扑连接 -- 一根导线"""

    net_name: str  # "VIN", "VOUT", "SW", "GND"
    device_pin: str = ""  # 主IC上的引脚名（无源专用网络为空）
    external_refs: list[str] = Field(default_factory=list)  # ["input_cap.1", "L1.1"]
    is_power: bool = False
    is_ground: bool = False


# ============================================================
# 拓扑定义
# ============================================================


class TopologyDef(BaseModel):
    """推荐应用电路拓扑"""

    circuit_type: str  # "ldo", "buck", "voltage_divider", "led_driver", "rc_filter"
    external_components: list[ExternalComponent] = Field(default_factory=list)
    connections: list[TopologyConnection] = Field(default_factory=list)
    parameters: dict[str, ParameterDef] = Field(default_factory=dict)
    calculations: dict[str, str] = Field(default_factory=dict)
    design_rules: list[str] = Field(default_factory=list)


class RecipeEvidence(BaseModel):
    """设计结论的证据摘要。"""

    source_type: str = "ai_inferred"
    summary: str = ""
    source_ref: str = ""
    page: int | None = None
    confidence: float = 1.0


class RecipeComponent(BaseModel):
    """外围器件选型条目。"""

    role: str
    value: str = ""
    formula: str = ""
    rationale: str = ""


class RecipeFormula(BaseModel):
    """计算公式及结果。"""

    name: str
    expression: str
    value: str = ""
    rationale: str = ""


class DesignRecipe(BaseModel):
    """器件级设计 recipe。"""

    topology_family: str = ""
    summary: str = ""
    pin_roles: dict[str, str] = Field(default_factory=dict)
    default_parameters: dict[str, str] = Field(default_factory=dict)
    sizing_components: list[RecipeComponent] = Field(default_factory=list)
    formulas: list[RecipeFormula] = Field(default_factory=list)
    evidence: list[RecipeEvidence] = Field(default_factory=list)


# ============================================================
# 完整器件模型
# ============================================================


class DeviceModel(BaseModel):
    """完整器件模型 -- 器件库的核心"""

    # --- 身份信息 ---
    part_number: str
    aliases: list[str] = Field(default_factory=list)
    manufacturer: str = ""
    description: str = ""
    category: str = (
        ""  # "ldo", "buck", "mcu", "passive", "led", "resistor", "capacitor"
    )

    # --- 电气参数 ---
    specs: dict[str, str] = Field(default_factory=dict)

    # --- 符号 ---
    symbol: SymbolDef | None = None  # None 表示基础无源器件（使用 schemdraw 内置）

    # --- 推荐电路 ---
    topology: TopologyDef | None = None  # None 表示独立无源器件
    design_recipe: DesignRecipe | None = None

    # --- SPICE ---
    spice_model: str = ""
    spice_model_ref: str = ""

    # --- 采购信息 ---
    lcsc_part: str = ""
    datasheet_url: str = ""
    easyeda_id: str = ""
    package: str = ""

    # --- Datasheet 文件 ---
    datasheet_path: str = ""
    """入库时保存的 PDF datasheet 相对路径（如 datasheets/TPS54202.pdf）"""

    # --- 元数据 ---
    source: str = "manual"  # "manual", "pdf_parsed", "easyeda", "digikey", "migrated"
    confidence: float = 1.0
    notes: str = ""

    # --- 设计知识 ---
    design_roles: list[str] = Field(default_factory=list)
    """可扮演的设计角色列表 (e.g. ["main_regulator", "aux_regulator"])"""

    selection_hints: list[str] = Field(default_factory=list)
    """适用场景描述 (e.g. ["低压差应用", "电池供电设备"])"""

    anti_patterns: list[str] = Field(default_factory=list)
    """不适用场景 (e.g. ["输入输出压差小于1.1V", "高效率要求场景"])"""

    required_companions: list[str] = Field(default_factory=list)
    """必需外围件描述 (e.g. ["输入电容10uF", "输出电容22uF"])"""

    operating_constraints: dict[str, str] = Field(default_factory=dict)
    """关键工作约束 (e.g. {"min_v_dropout": "1.1V", "max_power_dissipation": "1W"})"""

    layout_hints: list[str] = Field(default_factory=list)
    """布局注意事项 (e.g. ["输入输出电容紧贴IC", "散热焊盘需要过孔"])"""

    failure_modes: list[str] = Field(default_factory=list)
    """常见误用模式 (e.g. ["输入电压过低导致掉压", "输出电容ESR过高导致振荡"])"""

    review_rules: list[str] = Field(default_factory=list)
    """该器件的审查规则引用 (e.g. ["check_ldo_dropout", "check_thermal_dissipation"])"""
