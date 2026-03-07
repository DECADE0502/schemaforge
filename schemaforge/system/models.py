"""系统级设计数据模型。

三层分离：
- 意图层（AI 输出）：ModuleIntent / ConnectionIntent / SystemDesignRequest
- 解析层（本地规则）：ModuleInstance / PortRef / ResolvedConnection / SystemNet
- 产物层（最终输出）：SystemDesignIR / SystemBundle

AI 只产出意图层。解析层和产物层全部由本地确定性代码生成。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ============================================================
# 枚举
# ============================================================


class ModuleStatus(str, Enum):
    """模块实例状态。"""

    PENDING = "pending"              # 尚未解析
    RESOLVED = "resolved"            # 器件已命中，实例已创建
    NEEDS_ASSET = "needs_asset"      # 器件库缺失，需要导入
    SYNTHESIZED = "synthesized"      # 参数已综合
    ERROR = "error"                  # 解析/综合失败


class NetType(str, Enum):
    """网络类型。"""

    POWER = "power"
    GROUND = "ground"
    SIGNAL = "signal"
    CONTROL = "control"


class SignalType(str, Enum):
    """连接信号类型。"""

    POWER_SUPPLY = "power_supply"    # 电源供电链
    GROUND = "ground"                # 地网络
    GPIO = "gpio"                    # GPIO 控制
    SPI = "spi"                      # SPI 总线
    I2C = "i2c"                      # I2C 总线
    UART = "uart"                    # UART 串口
    ANALOG = "analog"                # 模拟信号
    ENABLE = "enable"                # 使能信号
    FEEDBACK = "feedback"            # 反馈信号
    OTHER = "other"


class ConnectionSemantic(str, Enum):
    """连接语义。"""

    SUPPLY_CHAIN = "supply_chain"              # 电源链：VOUT → VIN
    GPIO_DRIVE = "gpio_drive"                  # GPIO 驱动外设
    BUS_CONNECT = "bus_connect"                # 总线连接
    FEEDBACK_LOOP = "feedback_loop"            # 反馈环路
    ENABLE_CONTROL = "enable_control"          # 使能控制
    GROUND_TIE = "ground_tie"                  # 共地
    UNKNOWN = "unknown"


# ============================================================
# 意图层（AI 输出）
# ============================================================


@dataclass
class ModuleIntent:
    """AI 解析出的单个模块意图。

    AI 只填这个结构，不填最终参数值。
    """

    intent_id: str                          # "buck1", "ldo1", "mcu1", "led1"
    role: str                               # "第一级降压", "二级稳压", "主控", "指示灯"
    part_number_hint: str = ""              # 用户指定的型号（必须精确保留）
    category_hint: str = ""                 # AI 推断的类别
    electrical_targets: dict[str, str] = field(default_factory=dict)
    # {"v_in": "20", "v_out": "5", "i_out": "2"}
    control_targets: dict[str, str] = field(default_factory=dict)
    # {"gpio_pin": "PA1", "drive_mode": "push_pull"}
    placement_hint: str = ""                # "power_chain" / "control_side"
    priority: int = 0                       # 越小越优先


@dataclass
class ConnectionIntent:
    """AI 解析出的模块间连接意图。

    AI 只需指定模块级连接和信号类型，具体引脚映射由本地规则推导。
    """

    connection_id: str
    src_module_intent: str                  # 源模块 intent_id
    src_port_hint: str = ""                 # "VOUT" / "PA1" / "" (让规则引擎推导)
    dst_module_intent: str = ""             # 目标模块 intent_id
    dst_port_hint: str = ""                 # "VIN" / "ANODE" / ""
    signal_type: SignalType = SignalType.OTHER
    connection_semantics: ConnectionSemantic = ConnectionSemantic.UNKNOWN
    confidence: float = 1.0                 # AI 置信度


@dataclass
class SystemDesignRequest:
    """系统级设计请求（AI 解析后的完整意图）。"""

    raw_text: str                           # 用户原始输入
    modules: list[ModuleIntent] = field(default_factory=list)
    connections: list[ConnectionIntent] = field(default_factory=list)
    global_v_in: str = ""                   # 系统总输入电压
    user_constraints: dict[str, str] = field(default_factory=dict)
    ambiguities: list[str] = field(default_factory=list)
    design_notes: str = ""                  # AI 提取的额外信息


# ============================================================
# 解析层（本地规则产出）
# ============================================================


@dataclass
class PortRef:
    """引脚级端口引用。"""

    module_id: str
    port_role: str                          # "power_in", "power_out", "gpio", "ground"
    pin_name: str = ""                      # "VIN", "PA1", "GND"
    pin_number: str = ""                    # "1", "3"
    net_class: NetType = NetType.SIGNAL


@dataclass
class ModuleInstance:
    """已解析的模块实例（器件已命中，端口已映射）。"""

    module_id: str                          # 唯一 ID，与 intent_id 对应
    role: str
    device: Any = None                      # DeviceModel（避免循环导入用 Any）
    resolved_category: str = ""
    resolved_ports: dict[str, PortRef] = field(default_factory=dict)
    # port_role → PortRef
    parameters: dict[str, str] = field(default_factory=dict)
    # 综合后的参数 {"v_in": "20", "v_out": "5", "l_value": "10uH", ...}
    external_components: list[dict[str, Any]] = field(default_factory=list)
    # 外围元件实例 [{"ref": "C1", "role": "input_cap", "value": "10uF"}, ...]
    status: ModuleStatus = ModuleStatus.PENDING
    missing_part_number: str = ""           # 当 status=NEEDS_ASSET 时记录
    warnings: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)


@dataclass
class ResolvedConnection:
    """已解析的引脚级连接（由规则引擎产出）。"""

    resolved_connection_id: str
    src_port: PortRef
    dst_port: PortRef
    net_name: str = ""                      # 全局网络名 "NET_5V", "GND"
    rule_id: str = ""                       # 产出此连接的规则 ID
    evidence: str = ""                      # 规则解释
    status: str = "resolved"                # "resolved" / "unresolved" / "user_confirmed"


@dataclass
class SystemNet:
    """系统级网络。"""

    net_id: str
    net_name: str                           # "VIN_20V", "NET_5V", "NET_3V3", "GND"
    members: list[PortRef] = field(default_factory=list)
    net_type: NetType = NetType.SIGNAL
    voltage_domain: str = ""                # "20V", "5V", "3.3V", "0V"
    is_global: bool = False                 # GND 是全局网络


# ============================================================
# 产物层（最终输出）
# ============================================================


@dataclass
class SystemDesignIR:
    """系统级设计中间表示（唯一真值）。

    所有下游操作（渲染、BOM、SPICE、审查）都从这个结构读取。
    """

    request: SystemDesignRequest
    module_instances: dict[str, ModuleInstance] = field(default_factory=dict)
    # module_id → ModuleInstance
    connections: list[ResolvedConnection] = field(default_factory=list)
    nets: dict[str, SystemNet] = field(default_factory=dict)
    # net_id → SystemNet
    warnings: list[str] = field(default_factory=list)
    unresolved_items: list[dict[str, str]] = field(default_factory=list)
    # [{"type": "missing_device", "module_id": "mcu1", "detail": "..."}]
    evidence_map: dict[str, list[str]] = field(default_factory=dict)
    # module_id → [evidence strings]

    def get_module(self, module_id: str) -> ModuleInstance | None:
        return self.module_instances.get(module_id)

    def get_resolved_modules(self) -> list[ModuleInstance]:
        return [
            m for m in self.module_instances.values()
            if m.status in (ModuleStatus.RESOLVED, ModuleStatus.SYNTHESIZED)
        ]

    def get_unresolved_modules(self) -> list[ModuleInstance]:
        return [
            m for m in self.module_instances.values()
            if m.status in (ModuleStatus.PENDING, ModuleStatus.NEEDS_ASSET, ModuleStatus.ERROR)
        ]

    def get_nets_for_module(self, module_id: str) -> list[SystemNet]:
        return [
            net for net in self.nets.values()
            if any(m.module_id == module_id for m in net.members)
        ]

    def to_summary(self) -> dict[str, Any]:
        """生成用户可读的设计摘要。"""
        return {
            "total_modules": len(self.module_instances),
            "resolved_modules": len(self.get_resolved_modules()),
            "unresolved_modules": len(self.get_unresolved_modules()),
            "connections": len(self.connections),
            "nets": len(self.nets),
            "warnings": len(self.warnings),
            "unresolved_items": len(self.unresolved_items),
        }


@dataclass
class RenderMetadata:
    """渲染元数据。"""

    module_bboxes: dict[str, tuple[float, float, float, float]] = field(
        default_factory=dict,
    )
    # module_id → (x, y, width, height)
    anchor_points: dict[str, dict[str, tuple[float, float]]] = field(
        default_factory=dict,
    )
    # module_id → {port_role: (x, y)}


@dataclass
class SystemBundle:
    """系统级设计最终产物。"""

    design_ir: SystemDesignIR
    svg_path: str = ""
    bom_text: str = ""
    bom_csv: str = ""
    spice_text: str = ""
    review_report: list[str] = field(default_factory=list)
    render_metadata: RenderMetadata = field(default_factory=RenderMetadata)

    def to_dict(self) -> dict[str, Any]:
        summary = self.design_ir.to_summary()
        return {
            "summary": summary,
            "svg_path": self.svg_path,
            "bom_text": self.bom_text,
            "spice_text": self.spice_text,
            "review_issues": len(self.review_report),
        }
