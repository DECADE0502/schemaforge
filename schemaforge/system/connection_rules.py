"""连接规则引擎：ConnectionIntent → ResolvedConnection + SystemNet。

将 AI 产出的连接意图通过确定性规则解析为引脚级连接，
包含自动外围元件生成（如 GPIO→LED 的限流电阻）。

约束遵循:
- C31 电源输出只能接兼容输入端口
- C32 输出对输出默认禁止
- C33 GPIO 驱动 LED 必须自动串联限流电阻
- C34 所有 active 模块必须有 GND
- C35 GND 允许用同名网络符号表示
- C36 FB/COMP/BOOT/EN 等特殊引脚必须单独规则处理
- C37 规则命中必须记录 rule_id
- C38 一条连接不能同时被两个冲突规则接管
- C39 高层意图到 pin-level 映射必须解释得出来
- C40 规则失败时必须产出 unresolved，不得瞎接
- C41 规则引擎必须支持优先级
- C44 共享网络必须统一 net_name
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from schemaforge.system.models import (
    ConnectionIntent,
    ConnectionSemantic,
    ModuleInstance,
    NetType,
    PortRef,
    ResolvedConnection,
    SignalType,
    SystemNet,
)

logger = logging.getLogger(__name__)


# ============================================================
# T052: Rule data format
# ============================================================


@dataclass
class ConnectionRule:
    """连接规则定义。

    每条规则描述一个 src→dst 匹配模式及其产出语义。
    priority 越小优先级越高。
    """

    rule_id: str
    name: str
    priority: int  # lower = higher priority
    src_category: str  # "buck", "ldo", "*"
    src_port_role: str  # "power_out", "*"
    dst_category: str
    dst_port_role: str
    signal_type: SignalType
    semantic: ConnectionSemantic
    auto_components: list[dict[str, Any]] = field(default_factory=list)
    # e.g. [{"type": "resistor", "role": "led_limit", "formula": "..."}]


# ============================================================
# T053: Power chain rules
# ============================================================

RULE_POWER_SUPPLY = ConnectionRule(
    rule_id="RULE_POWER_SUPPLY",
    name="电源供电链",
    priority=10,
    src_category="*",
    src_port_role="power_out",
    dst_category="*",
    dst_port_role="power_in",
    signal_type=SignalType.POWER_SUPPLY,
    semantic=ConnectionSemantic.SUPPLY_CHAIN,
)


# ============================================================
# T054: GPIO→LED rule
# ============================================================

RULE_GPIO_LED = ConnectionRule(
    rule_id="RULE_GPIO_LED",
    name="GPIO 驱动 LED（自动串联限流电阻）",
    priority=20,
    src_category="*",
    src_port_role="gpio",
    dst_category="led",
    dst_port_role="*",
    signal_type=SignalType.GPIO,
    semantic=ConnectionSemantic.GPIO_DRIVE,
    auto_components=[
        {
            "type": "resistor",
            "role": "led_limit",
            "formula": "(V_supply - V_led) / I_led",
        },
    ],
)


# ============================================================
# T055: SPI→Flash skeleton
# ============================================================

RULE_SPI_BUS = ConnectionRule(
    rule_id="RULE_SPI_BUS",
    name="SPI 总线连接",
    priority=30,
    src_category="*",
    src_port_role="spi",
    dst_category="*",
    dst_port_role="spi",
    signal_type=SignalType.SPI,
    semantic=ConnectionSemantic.BUS_CONNECT,
)

# SPI 信号名映射：MCU 引脚角色 → Flash 引脚角色
_SPI_PIN_MAP: dict[str, str] = {
    "MOSI": "DI",
    "MISO": "DO",
    "SCK": "CLK",
    "SCLK": "CLK",
    "CS": "CS",
    "NSS": "CS",
    "SS": "CS",
}


# ============================================================
# T057: EN/BOOT/FB special rules
# ============================================================

RULE_ENABLE_PULLUP = ConnectionRule(
    rule_id="RULE_ENABLE_PULLUP",
    name="EN 引脚默认上拉到 VIN",
    priority=40,
    src_category="*",
    src_port_role="enable",
    dst_category="*",
    dst_port_role="*",
    signal_type=SignalType.ENABLE,
    semantic=ConnectionSemantic.ENABLE_CONTROL,
    auto_components=[
        {
            "type": "resistor",
            "role": "en_pullup",
            "formula": "100k to VIN",
        },
    ],
)

RULE_BOOTSTRAP_CAP = ConnectionRule(
    rule_id="RULE_BOOTSTRAP_CAP",
    name="BST 自举电容 (BST → SW)",
    priority=40,
    src_category="*",
    src_port_role="bootstrap",
    dst_category="*",
    dst_port_role="switch",
    signal_type=SignalType.OTHER,
    semantic=ConnectionSemantic.UNKNOWN,
    auto_components=[
        {
            "type": "capacitor",
            "role": "boot_cap",
            "formula": "100nF between BST and SW",
        },
    ],
)

RULE_FEEDBACK_DIVIDER = ConnectionRule(
    rule_id="RULE_FEEDBACK_DIVIDER",
    name="FB 连接到反馈分压器",
    priority=40,
    src_category="*",
    src_port_role="feedback",
    dst_category="*",
    dst_port_role="*",
    signal_type=SignalType.FEEDBACK,
    semantic=ConnectionSemantic.FEEDBACK_LOOP,
    auto_components=[
        {
            "type": "resistor_divider",
            "role": "fb_divider",
            "formula": "V_out = V_ref * (1 + R1/R2)",
        },
    ],
)

# ============================================================
# 规则注册表（按优先级排序）
# ============================================================

_ALL_RULES: list[ConnectionRule] = sorted(
    [
        RULE_POWER_SUPPLY,
        RULE_GPIO_LED,
        RULE_SPI_BUS,
        RULE_ENABLE_PULLUP,
        RULE_BOOTSTRAP_CAP,
        RULE_FEEDBACK_DIVIDER,
    ],
    key=lambda r: r.priority,
)


def get_all_rules() -> list[ConnectionRule]:
    """返回所有已注册规则（按优先级排序）。"""
    return list(_ALL_RULES)


# ============================================================
# 内部匹配辅助
# ============================================================


def _category_matches(pattern: str, actual: str) -> bool:
    """类别模式匹配：'*' 匹配任意，否则精确匹配。"""
    if pattern == "*":
        return True
    return pattern == actual


def _port_role_matches(pattern: str, actual: str) -> bool:
    """端口角色模式匹配：'*' 匹配任意，否则精确匹配。"""
    if pattern == "*":
        return True
    return pattern == actual


def _find_port_by_role(
    instance: ModuleInstance,
    role: str,
    pin_hint: str = "",
) -> PortRef | None:
    """在模块实例中按角色查找端口。

    如果 pin_hint 非空且匹配，优先返回。
    否则返回第一个匹配角色的端口。
    """
    # 先尝试精确 pin_hint 匹配
    if pin_hint:
        pin_upper = pin_hint.strip().upper()
        for pin_name, port in instance.resolved_ports.items():
            if pin_name.upper() == pin_upper:
                return port
        for pin_name, port in instance.resolved_ports.items():
            base_name = pin_name.upper().split("-")[0]
            base_name = base_name.split("_")[0]
            if base_name == pin_upper:
                return port

    # 按角色查找
    for port in instance.resolved_ports.values():
        if port.port_role == role:
            return port

    return None


def _find_ports_by_role(
    instance: ModuleInstance,
    role: str,
) -> list[PortRef]:
    """返回模块实例中指定角色的所有端口。"""
    return [p for p in instance.resolved_ports.values() if p.port_role == role]


def _match_rule(
    rule: ConnectionRule,
    src_instance: ModuleInstance,
    dst_instance: ModuleInstance,
    intent: ConnectionIntent,
) -> bool:
    """检查一条规则是否匹配给定的连接意图。"""
    # 信号类型匹配（如果意图指定了信号类型且非 OTHER）
    if intent.signal_type != SignalType.OTHER:
        if intent.signal_type != rule.signal_type:
            return False

    # 类别匹配
    if not _category_matches(rule.src_category, src_instance.resolved_category):
        return False
    if not _category_matches(rule.dst_category, dst_instance.resolved_category):
        return False

    # 端口角色匹配
    src_port = _find_port_by_role(
        src_instance, rule.src_port_role, intent.src_port_hint,
    )
    dst_port = _find_port_by_role(
        dst_instance, rule.dst_port_role, intent.dst_port_hint,
    )

    if rule.src_port_role != "*" and src_port is None:
        return False
    if rule.dst_port_role != "*" and dst_port is None:
        return False

    return True


# ============================================================
# T053: 电源网络命名
# ============================================================


def _make_power_net_name(
    src_instance: ModuleInstance,
    dst_instance: ModuleInstance,
) -> str:
    """根据源模块输出电压生成电源网络名。

    优先使用 v_out 参数，否则使用模块 ID。
    """
    v_out = src_instance.parameters.get("v_out", "")
    if v_out:
        # 规范化：去除小数点后的 0，加 V 后缀
        try:
            v_float = float(v_out)
            if v_float == int(v_float):
                return f"NET_{int(v_float)}V"
            return f"NET_{v_out}V"
        except ValueError:
            pass
    return f"NET_{src_instance.module_id}_OUT"


# ============================================================
# T054: GPIO→LED 自动电阻计算
# ============================================================


def _compute_led_resistor(
    src_instance: ModuleInstance,
    dst_instance: ModuleInstance,
) -> dict[str, Any]:
    """计算 GPIO→LED 限流电阻值。

    公式: R = (V_supply - V_led) / I_led
    C33: GPIO 驱动 LED 必须自动串联限流电阻
    C54: LED 电阻必须由驱动电压和目标电流算
    """
    # 默认参数
    _LED_VF: dict[str, float] = {
        "red": 1.8,
        "green": 2.2,
        "blue": 3.0,
        "white": 3.0,
        "yellow": 2.0,
        "orange": 2.0,
    }
    default_i_led = 0.010  # 10mA

    # 从目标模块（LED）获取参数
    led_color = dst_instance.parameters.get("led_color", "green")
    v_led = _LED_VF.get(led_color, 2.0)

    # V_forward 覆盖
    v_fwd_str = dst_instance.parameters.get("v_forward", "")
    if v_fwd_str:
        try:
            v_led = float(v_fwd_str)
        except ValueError:
            pass

    # I_led 覆盖
    i_led = default_i_led
    i_led_str = dst_instance.parameters.get("led_current", "")
    if i_led_str:
        try:
            i_led = float(i_led_str) / 1000.0  # mA → A
        except ValueError:
            pass

    # V_supply: 优先 dst 参数，其次 src v_out
    v_supply = 3.3  # 默认
    v_supply_str = dst_instance.parameters.get(
        "v_supply",
        src_instance.parameters.get("v_out", ""),
    )
    if v_supply_str:
        try:
            v_supply = float(v_supply_str)
        except ValueError:
            pass

    # 计算电阻值
    if v_supply <= v_led:
        r_value = 0.0
        warning = f"V_supply({v_supply}V) <= V_led({v_led}V), 无法计算限流电阻"
    elif i_led <= 0:
        r_value = 0.0
        warning = "I_led <= 0, 无法计算限流电阻"
    else:
        r_value = (v_supply - v_led) / i_led
        warning = ""

    return {
        "type": "resistor",
        "role": "led_limit",
        "ref": "R_LED",
        "value": f"{r_value:.0f}",
        "unit": "ohm",
        "formula": f"({v_supply} - {v_led}) / {i_led}",
        "v_supply": str(v_supply),
        "v_led": str(v_led),
        "i_led": str(i_led),
        "warning": warning,
    }


# ============================================================
# T055: SPI 引脚映射
# ============================================================


def _resolve_spi_connections(
    src_instance: ModuleInstance,
    dst_instance: ModuleInstance,
    intent: ConnectionIntent,
) -> list[ResolvedConnection]:
    """解析 SPI 总线连接（CLK↔CLK, MOSI↔DI, MISO↔DO, CS↔CS）。"""
    connections: list[ResolvedConnection] = []

    src_spi_ports = _find_ports_by_role(src_instance, "spi")
    dst_spi_ports = _find_ports_by_role(dst_instance, "spi")

    # 构建 dst 侧的 pin_name → PortRef 映射
    dst_by_name: dict[str, PortRef] = {
        p.pin_name.upper(): p for p in dst_spi_ports
    }

    for src_port in src_spi_ports:
        src_pin = src_port.pin_name.upper()
        # 尝试通过映射表找到对应的 dst 引脚
        for src_suffix, dst_suffix in _SPI_PIN_MAP.items():
            if src_pin.endswith(src_suffix):
                # 在 dst 侧查找匹配引脚
                target_port = dst_by_name.get(dst_suffix)
                if target_port is None:
                    # 尝试直接同名
                    target_port = dst_by_name.get(src_suffix)
                if target_port is not None:
                    conn_id = f"spi_{src_port.pin_name}_{target_port.pin_name}"
                    net_name = f"SPI_{src_suffix}"
                    connections.append(
                        ResolvedConnection(
                            resolved_connection_id=conn_id,
                            src_port=src_port,
                            dst_port=target_port,
                            net_name=net_name,
                            rule_id=RULE_SPI_BUS.rule_id,
                            evidence=(
                                f"SPI 总线映射: {src_port.pin_name} → "
                                f"{target_port.pin_name} (net: {net_name})"
                            ),
                        ),
                    )
                break

    return connections


# ============================================================
# T056: GND merge
# ============================================================


def resolve_ground_strategy(
    module_instances: dict[str, ModuleInstance],
) -> SystemNet:
    """合并所有模块的 GND 端口为单一全局 GND 网络。

    C34: 所有 active 模块必须有 GND
    C35: GND 允许用同名网络符号表示
    """
    gnd_net = SystemNet(
        net_id="GND",
        net_name="GND",
        net_type=NetType.GROUND,
        voltage_domain="0V",
        is_global=True,
    )

    for instance in module_instances.values():
        for port in instance.resolved_ports.values():
            if port.port_role == "ground":
                gnd_net.members.append(port)

    return gnd_net


# ============================================================
# T057: 特殊引脚内部连接
# ============================================================


def _resolve_special_pins(
    instance: ModuleInstance,
) -> tuple[list[ResolvedConnection], list[dict[str, Any]]]:
    """解析模块内部特殊引脚连接（EN 上拉、BST 电容、FB 分压器）。

    C36: FB/COMP/BOOT/EN 等特殊引脚必须单独规则处理
    """
    connections: list[ResolvedConnection] = []
    auto_components: list[dict[str, Any]] = []

    en_port = _find_port_by_role(instance, "enable")
    vin_port = _find_port_by_role(instance, "power_in")
    bst_port = _find_port_by_role(instance, "bootstrap")
    sw_port = _find_port_by_role(instance, "switch")
    fb_port = _find_port_by_role(instance, "feedback")

    # EN → VIN via pullup resistor
    if en_port is not None and vin_port is not None:
        conn = ResolvedConnection(
            resolved_connection_id=f"{instance.module_id}_en_pullup",
            src_port=en_port,
            dst_port=vin_port,
            net_name=f"{instance.module_id}_EN",
            rule_id=RULE_ENABLE_PULLUP.rule_id,
            evidence="EN 引脚通过上拉电阻连接到 VIN（默认使能）",
        )
        connections.append(conn)
        auto_components.append({
            "type": "resistor",
            "role": "en_pullup",
            "ref": f"R_{instance.module_id}_EN",
            "value": "100000",
            "unit": "ohm",
            "formula": "100k pullup to VIN",
        })

    # BST → SW via bootstrap capacitor
    # 注意: boot_cap 元件由 synthesis 产出，这里只产出连接关系，不重复追加元件
    if bst_port is not None and sw_port is not None:
        conn = ResolvedConnection(
            resolved_connection_id=f"{instance.module_id}_boot_cap",
            src_port=bst_port,
            dst_port=sw_port,
            net_name=f"{instance.module_id}_BST",
            rule_id=RULE_BOOTSTRAP_CAP.rule_id,
            evidence="BST 与 SW 之间连接自举电容",
        )
        connections.append(conn)
        # boot_cap 元件已由 synthesize_buck_module() 产出，不再重复追加

    # FB → feedback divider
    # 注意: fb_upper/fb_lower 由 synthesis 产出，这里不再追加 fb_divider
    # 仅记录证据
    if fb_port is not None:
        instance.evidence.append(
            "FB 分压网络由 synthesis 计算 (fb_upper + fb_lower)"
        )

    return connections, auto_components


# ============================================================
# T058: Unresolved mechanism
# ============================================================


def _make_unresolved(
    intent: ConnectionIntent,
    reason: str,
) -> dict[str, str]:
    """为无法匹配规则的连接意图生成 unresolved 记录。

    C40: 规则失败时必须产出 unresolved，不得瞎接
    """
    return {
        "type": "unresolved_connection",
        "connection_id": intent.connection_id,
        "src_module": intent.src_module_intent,
        "dst_module": intent.dst_module_intent or "",
        "signal_type": intent.signal_type.value,
        "reason": reason,
    }


# ============================================================
# T059: explain_connection_rule
# ============================================================


def explain_connection_rule(conn: ResolvedConnection) -> str:
    """返回已解析连接的人文可读解释。

    C39: 高层意图到 pin-level 映射必须解释得出来
    """
    if not conn.rule_id:
        return (
            f"连接 {conn.resolved_connection_id}: "
            f"{conn.src_port.module_id}.{conn.src_port.pin_name} → "
            f"{conn.dst_port.module_id}.{conn.dst_port.pin_name} "
            f"(net: {conn.net_name}) — 无规则关联"
        )

    # 查找规则名称
    rule_name = conn.rule_id
    for rule in _ALL_RULES:
        if rule.rule_id == conn.rule_id:
            rule_name = rule.name
            break

    explanation = (
        f"连接 {conn.resolved_connection_id}: "
        f"{conn.src_port.module_id}.{conn.src_port.pin_name} → "
        f"{conn.dst_port.module_id}.{conn.dst_port.pin_name} "
        f"(net: {conn.net_name})\n"
        f"  规则: [{conn.rule_id}] {rule_name}\n"
        f"  依据: {conn.evidence}"
    )
    return explanation


# ============================================================
# 单条意图解析
# ============================================================


def _resolve_single_intent(
    intent: ConnectionIntent,
    module_instances: dict[str, ModuleInstance],
    nets: dict[str, SystemNet],
) -> tuple[list[ResolvedConnection], list[dict[str, Any]], list[dict[str, str]]]:
    """解析单条连接意图。

    Returns:
        (connections, auto_components, unresolved_items)
    """
    connections: list[ResolvedConnection] = []
    auto_components: list[dict[str, Any]] = []
    unresolved: list[dict[str, str]] = []

    src_instance = module_instances.get(intent.src_module_intent)
    if src_instance is None:
        unresolved.append(_make_unresolved(
            intent,
            f"源模块 '{intent.src_module_intent}' 不存在",
        ))
        return connections, auto_components, unresolved

    # 目标模块可选（某些意图可能只指定源）
    dst_instance = module_instances.get(intent.dst_module_intent or "")
    if dst_instance is None:
        unresolved.append(_make_unresolved(
            intent,
            f"目标模块 '{intent.dst_module_intent}' 不存在",
        ))
        return connections, auto_components, unresolved

    # 按优先级遍历规则（C41: 支持优先级）
    matched_rule: ConnectionRule | None = None
    for rule in _ALL_RULES:
        if _match_rule(rule, src_instance, dst_instance, intent):
            matched_rule = rule
            break  # C38: 一条连接只能被一个规则接管

    if matched_rule is None:
        unresolved.append(_make_unresolved(
            intent,
            f"无匹配规则: {src_instance.resolved_category}.{intent.src_port_hint} → "
            f"{dst_instance.resolved_category}.{intent.dst_port_hint} "
            f"(signal_type={intent.signal_type.value})",
        ))
        return connections, auto_components, unresolved

    # ---- 按规则类型分派解析 ----

    if matched_rule.rule_id == RULE_SPI_BUS.rule_id:
        # SPI 总线：多引脚映射
        spi_conns = _resolve_spi_connections(
            src_instance, dst_instance, intent,
        )
        if spi_conns:
            connections.extend(spi_conns)
        else:
            unresolved.append(_make_unresolved(
                intent, "SPI 总线引脚映射失败",
            ))
        return connections, auto_components, unresolved

    # 通用单连接解析
    src_port = _find_port_by_role(
        src_instance, matched_rule.src_port_role, intent.src_port_hint,
    )
    dst_port = _find_port_by_role(
        dst_instance, matched_rule.dst_port_role, intent.dst_port_hint,
    )

    # 对于 dst_port_role="*" 的规则，尝试合理推断
    if dst_port is None and matched_rule.dst_port_role == "*":
        # 对于 LED，查找 "other" 角色端口（通常是 ANODE）
        if dst_instance.resolved_category == "led":
            for port in dst_instance.resolved_ports.values():
                if port.port_role not in ("ground",):
                    dst_port = port
                    break

    if src_port is None or dst_port is None:
        unresolved.append(_make_unresolved(
            intent,
            f"规则 {matched_rule.rule_id} 匹配但端口解析失败: "
            f"src_port={'found' if src_port else 'missing'}, "
            f"dst_port={'found' if dst_port else 'missing'}",
        ))
        return connections, auto_components, unresolved

    # 生成网络名
    if matched_rule.semantic == ConnectionSemantic.SUPPLY_CHAIN:
        net_name = _make_power_net_name(src_instance, dst_instance)
    elif matched_rule.semantic == ConnectionSemantic.GPIO_DRIVE:
        net_name = f"NET_{src_port.pin_name}_{dst_instance.module_id}"
    else:
        net_name = f"NET_{intent.connection_id}"

    # 创建连接
    evidence = (
        f"规则 [{matched_rule.rule_id}] {matched_rule.name}: "
        f"{src_port.module_id}.{src_port.pin_name}({src_port.port_role}) → "
        f"{dst_port.module_id}.{dst_port.pin_name}({dst_port.port_role})"
    )

    conn = ResolvedConnection(
        resolved_connection_id=intent.connection_id,
        src_port=src_port,
        dst_port=dst_port,
        net_name=net_name,
        rule_id=matched_rule.rule_id,
        evidence=evidence,
    )
    connections.append(conn)

    # 自动外围元件（C33: GPIO→LED 必须自动限流电阻）
    if matched_rule.rule_id == RULE_GPIO_LED.rule_id:
        resistor = _compute_led_resistor(src_instance, dst_instance)
        auto_components.append(resistor)

    # 更新网络表
    if net_name not in nets:
        net_type = NetType.POWER if matched_rule.signal_type == SignalType.POWER_SUPPLY else NetType.SIGNAL
        nets[net_name] = SystemNet(
            net_id=net_name,
            net_name=net_name,
            net_type=net_type,
            voltage_domain=src_instance.parameters.get("v_out", ""),
        )

    nets[net_name].members.append(src_port)
    nets[net_name].members.append(dst_port)

    return connections, auto_components, unresolved


# ============================================================
# T052-T060: 主入口
# ============================================================


def resolve_all_connections(
    module_instances: dict[str, ModuleInstance],
    connection_intents: list[ConnectionIntent],
) -> tuple[list[ResolvedConnection], dict[str, SystemNet], list[dict[str, str]]]:
    """解析所有连接意图。

    流程:
    1. 解析 GND 全局网络（T056）
    2. 解析各模块特殊引脚（T057: EN/BST/FB）
    3. 按优先级逐条匹配规则（T053-T055）
    4. 未匹配的产出 unresolved（T058）

    Args:
        module_instances: 已解析的模块实例字典
        connection_intents: AI 产出的连接意图列表

    Returns:
        (resolved_connections, nets, unresolved_items)
    """
    all_connections: list[ResolvedConnection] = []
    nets: dict[str, SystemNet] = {}
    all_unresolved: list[dict[str, str]] = []

    # Step 1: GND 全局网络
    gnd_net = resolve_ground_strategy(module_instances)
    if gnd_net.members:
        nets["GND"] = gnd_net

    # Step 2: 各模块特殊引脚
    for instance in module_instances.values():
        special_conns, special_comps = _resolve_special_pins(instance)
        all_connections.extend(special_conns)
        # 将自动元件附加到模块实例
        instance.external_components.extend(special_comps)

    # Step 3: 解析连接意图
    for intent in connection_intents:
        conns, auto_comps, unresolved = _resolve_single_intent(
            intent, module_instances, nets,
        )
        all_connections.extend(conns)
        all_unresolved.extend(unresolved)

        # 将自动元件附加到目标模块（如 LED 限流电阻）
        if auto_comps:
            dst = module_instances.get(intent.dst_module_intent or "")
            if dst is not None:
                dst.external_components.extend(auto_comps)

    return all_connections, nets, all_unresolved
