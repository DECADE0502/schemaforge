"""器件解析器：ModuleIntent → ModuleInstance。

将 AI 产出的意图层（ModuleIntent）解析为确定性的解析层（ModuleInstance），
包含器件命中、端口映射、参数继承。

约束遵循:
- C16 精确型号优先于类别猜测
- C17 alias 命中必须可追溯
- C18 缺件不得静默换型
- C26 端口角色必须可本地推导
"""

from __future__ import annotations

import logging
import re

from schemaforge.library.models import DeviceModel
from schemaforge.library.store import ComponentStore
from schemaforge.system.models import (
    ModuleInstance,
    ModuleIntent,
    ModuleStatus,
    NetType,
    PortRef,
)

logger = logging.getLogger(__name__)


# ============================================================
# 引脚名称 → 端口角色映射模式
# ============================================================

# 电源输入
_POWER_IN_PATTERNS: set[str] = {
    "VIN", "VCC", "VDD", "V+", "VBAT", "VREF",
    "AVDD", "DVDD", "IOVDD", "PVDD", "VDDIO",
    "VDDA",
}

# 电源输出
_POWER_OUT_PATTERNS: set[str] = {
    "VOUT", "VO", "VOUT1", "VOUT2",
}

# 接地
_GROUND_PATTERNS: set[str] = {
    "GND", "VSS", "V-", "AGND", "DGND", "PGND",
    "GNDA", "GNDD", "EP", "PAD", "EPAD",
    "VSSA",
}

# 开关节点
_SWITCH_PATTERNS: set[str] = {
    "SW", "PH", "PHASE", "LX",
}

# 反馈/补偿
_FEEDBACK_PATTERNS: set[str] = {
    "FB", "COMP", "SS",
}

# 使能
_ENABLE_PATTERNS: set[str] = {
    "EN", "SHDN", "ENABLE", "SHUTDOWN", "CE",
}

# 自举
_BOOTSTRAP_PATTERNS: set[str] = {
    "BST", "BOOT", "BOOTSTRAP",
}

# GPIO 模式 (PA0-PA15, PB0-PB15, PC0-PC15, PD0-PD15 等)
_GPIO_PATTERN = re.compile(r"^P[A-Z]\d+", re.IGNORECASE)

# 总线引脚模式
_SPI_PATTERN = re.compile(
    r"^(SPI\d*_)?(MOSI|MISO|SCK|SCLK|NSS|CS|SS)$", re.IGNORECASE,
)
_I2C_PATTERN = re.compile(
    r"^(I2C\d*_)?(SCL|SDA|SMBA?)$", re.IGNORECASE,
)
_UART_PATTERN = re.compile(
    r"^(U[S]?ART\d*_)?(TX|RX|RTS|CTS)$", re.IGNORECASE,
)

_TOPOLOGY_PORT_NAME_ALIASES: dict[str, str] = {
    "LED_ANODE": "ANODE",
    "LED_CATHODE": "CATHODE",
}


# ============================================================
# T032: 精确型号查找
# ============================================================


def resolve_exact_part(
    store: ComponentStore,
    part_number: str,
) -> DeviceModel | None:
    """精确型号查找（C16: 精确型号优先于类别猜测）。

    Args:
        store: 器件库存储实例
        part_number: 完整料号字符串

    Returns:
        匹配的 DeviceModel，未命中返回 None
    """
    if not part_number:
        return None
    return store.get_device(part_number)


# ============================================================
# T033: 别名查找
# ============================================================


def resolve_alias_part(
    store: ComponentStore,
    alias: str,
) -> DeviceModel | None:
    """按别名查找器件（C17: alias 命中必须可追溯）。

    遍历库中所有器件，检查 aliases 字段是否包含匹配项。
    匹配为大小写不敏感。

    Args:
        store: 器件库存储实例
        alias: 别名字符串

    Returns:
        匹配的 DeviceModel，未命中返回 None
    """
    if not alias:
        return None

    alias_upper = alias.strip().upper()

    for part_number in store.list_devices():
        device = store.get_device(part_number)
        if device is None:
            continue
        for dev_alias in device.aliases:
            if dev_alias.strip().upper() == alias_upper:
                logger.info(
                    "别名命中: '%s' → '%s' (alias: '%s')",
                    alias,
                    device.part_number,
                    dev_alias,
                )
                return device

    return None


def _normalize_spec_text(value: object) -> str:
    text = str(value).strip().lower()
    return text.removesuffix("v").removesuffix("a")


def resolve_family_variant_part(
    store: ComponentStore,
    intent: ModuleIntent,
) -> DeviceModel | None:
    """按料号家族前缀解析唯一可证明的变体。

    只允许：
    - 库中存在同一 family 前缀的器件；且
    - 候选唯一，或被 electrical_targets 唯一消歧。

    不允许跨 family 或类别近似代换。
    """
    family = intent.part_number_hint.strip().upper()
    if not family:
        return None

    candidates: list[DeviceModel] = []
    for part_number in store.list_devices():
        device = store.get_device(part_number)
        if device is None:
            continue
        part_upper = device.part_number.strip().upper()
        if part_upper.startswith(family + "-") or part_upper.startswith(family + "_"):
            candidates.append(device)

    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    filtered = candidates
    for key, target_value in intent.electrical_targets.items():
        normalized_target = _normalize_spec_text(target_value)
        exact_matches = [
            device
            for device in filtered
            if key in device.specs
            and _normalize_spec_text(device.specs.get(key, "")) == normalized_target
        ]
        if len(exact_matches) == 1:
            return exact_matches[0]
        if exact_matches:
            filtered = exact_matches

    if len(filtered) == 1:
        return filtered[0]
    return None


# ============================================================
# T034: 候选器件搜索
# ============================================================


def resolve_part_candidates(
    store: ComponentStore,
    intent: ModuleIntent,
) -> list[DeviceModel]:
    """按类别和电气目标搜索候选器件并评分排序。

    优先级:
    1. 精确型号命中 → 单元素列表
    2. 别名命中 → 单元素列表
    3. 按 category_hint 搜索 → 评分排序

    Args:
        store: 器件库存储实例
        intent: 模块意图

    Returns:
        按匹配度降序排列的 DeviceModel 列表（可能为空）
    """
    # 优先精确型号
    if intent.part_number_hint:
        exact = resolve_exact_part(store, intent.part_number_hint)
        if exact is not None:
            return [exact]

        # 尝试别名
        alias_hit = resolve_alias_part(store, intent.part_number_hint)
        if alias_hit is not None:
            return [alias_hit]

        family_variant = resolve_family_variant_part(store, intent)
        if family_variant is not None:
            return [family_variant]

        # 用户显式给了料号但库里没有精确/别名命中时，必须停止，
        # 不能再按 category_hint 模糊回退到“相近器件”。
        return []

    # 按类别搜索
    if not intent.category_hint:
        return []

    candidates = store.search_devices(category=intent.category_hint)
    if not candidates:
        return []

    # 评分排序：匹配 electrical_targets 中的 spec 项越多越靠前
    def _score(device: DeviceModel) -> int:
        score = 0
        for key in intent.electrical_targets:
            if key in device.specs:
                score += 1
        # 有 design_recipe 加分
        if device.design_recipe is not None:
            score += 1
        # 有 topology 加分
        if device.topology is not None:
            score += 1
        return score

    candidates.sort(key=_score, reverse=True)
    return candidates


# ============================================================
# T035: 器件引脚 → 语义端口映射
# ============================================================


def _classify_pin(pin_name: str) -> tuple[str, NetType]:
    """将引脚名称映射到 (port_role, net_class)。

    引脚名称匹配为大小写不敏感。对于带后缀编号的引脚
    （如 VDD_1, VSS_2），先尝试去掉 _N 后缀再匹配。

    Returns:
        (port_role, net_class) 元组
    """
    name_upper = pin_name.strip().upper()

    # 去除 _N 后缀（如 VDD_1, VSS_2, VDD_3）用于模式匹配
    base_name = re.sub(r"_\d+$", "", name_upper)

    # 1. 接地
    if base_name in _GROUND_PATTERNS:
        return "ground", NetType.GROUND

    # 2. 电源输入
    if base_name in _POWER_IN_PATTERNS:
        return "power_in", NetType.POWER

    # 3. 电源输出
    if base_name in _POWER_OUT_PATTERNS:
        return "power_out", NetType.POWER

    # 4. 开关节点
    if base_name in _SWITCH_PATTERNS:
        return "switch", NetType.POWER

    # 5. 反馈/补偿
    if base_name in _FEEDBACK_PATTERNS:
        return "feedback", NetType.SIGNAL

    # 6. 使能
    if base_name in _ENABLE_PATTERNS:
        return "enable", NetType.CONTROL

    # 7. 自举
    if base_name in _BOOTSTRAP_PATTERNS:
        return "bootstrap", NetType.SIGNAL

    # 8. SPI 总线
    if _SPI_PATTERN.match(name_upper):
        return "spi", NetType.SIGNAL

    # 9. I2C 总线
    if _I2C_PATTERN.match(name_upper):
        return "i2c", NetType.SIGNAL

    # 10. UART 串口
    if _UART_PATTERN.match(name_upper):
        return "uart", NetType.SIGNAL

    # 11. GPIO（含复合名称如 PA0-WKUP、PB10 等）
    # 提取前导 Pxx 部分
    if _GPIO_PATTERN.match(name_upper):
        return "gpio", NetType.SIGNAL

    # 12. 兜底
    return "other", NetType.SIGNAL


def get_device_ports(device: DeviceModel) -> dict[str, PortRef]:
    """将器件引脚映射为语义端口字典。

    对于同角色多引脚（如 VDD_1, VDD_2, GND 等），
    使用 pin_name 作为 key 保证唯一性。

    Args:
        device: 完整器件模型

    Returns:
        {pin_name: PortRef} 字典
    """
    ports: dict[str, PortRef] = {}

    if device.symbol is not None:
        for pin in device.symbol.pins:
            port_role, net_class = _classify_pin(pin.name)

            ports[pin.name] = PortRef(
                module_id="",  # 实例化时填充
                port_role=port_role,
                pin_name=pin.name,
                pin_number=pin.pin_number,
                net_class=net_class,
            )

    if device.topology is not None:
        for index, connection in enumerate(device.topology.connections, start=1):
            if connection.device_pin:
                continue
            raw_name = (connection.net_name or "").strip().upper()
            if not raw_name:
                continue
            pin_name = _TOPOLOGY_PORT_NAME_ALIASES.get(raw_name, raw_name)
            if pin_name in ports:
                continue
            port_role, net_class = _classify_pin(pin_name)
            ports[pin_name] = PortRef(
                module_id="",
                port_role=port_role,
                pin_name=pin_name,
                pin_number=f"TOPO{index}",
                net_class=net_class,
            )

    return ports


# ============================================================
# T036: 电源端口提取
# ============================================================


def get_power_ports(device: DeviceModel) -> dict[str, PortRef]:
    """提取电源相关端口（VIN/VCC, VOUT, GND）。

    Args:
        device: 完整器件模型

    Returns:
        仅含电源端口的 {pin_name: PortRef} 字典
    """
    all_ports = get_device_ports(device)
    power_roles = {"power_in", "power_out", "ground"}
    return {
        name: port
        for name, port in all_ports.items()
        if port.port_role in power_roles
    }


# ============================================================
# T037: 信号端口提取
# ============================================================


def get_signal_ports(device: DeviceModel) -> dict[str, PortRef]:
    """提取信号端口（GPIO, SPI, UART, I2C 等）。

    Args:
        device: 完整器件模型

    Returns:
        仅含信号端口的 {pin_name: PortRef} 字典
    """
    all_ports = get_device_ports(device)
    signal_roles = {"gpio", "spi", "i2c", "uart"}
    return {
        name: port
        for name, port in all_ports.items()
        if port.port_role in signal_roles
    }


# ============================================================
# T038: 模块实例化
# ============================================================


def instantiate_module_from_device(
    intent: ModuleIntent,
    device: DeviceModel,
) -> ModuleInstance:
    """从意图和器件创建完全解析的模块实例。

    - 复制意图字段到实例
    - 使用 get_device_ports 映射端口
    - 设置 status 为 RESOLVED
    - 将 electrical_targets 复制到 parameters

    Args:
        intent: AI 产出的模块意图
        device: 已命中的器件模型

    Returns:
        状态为 RESOLVED 的 ModuleInstance
    """
    raw_ports = get_device_ports(device)

    # 填充 module_id 到每个 PortRef
    resolved_ports: dict[str, PortRef] = {}
    for pin_name, port in raw_ports.items():
        resolved_ports[pin_name] = PortRef(
            module_id=intent.intent_id,
            port_role=port.port_role,
            pin_name=port.pin_name,
            pin_number=port.pin_number,
            net_class=port.net_class,
        )

    return ModuleInstance(
        module_id=intent.intent_id,
        role=intent.role,
        device=device,
        resolved_category=device.category or intent.category_hint,
        resolved_ports=resolved_ports,
        parameters=dict(intent.electrical_targets),
        status=ModuleStatus.RESOLVED,
        evidence=[
            f"器件命中: {device.part_number}",
            f"类别: {device.category}",
        ],
    )


# ============================================================
# T039: 模块实例校验
# ============================================================


def validate_module_instance(instance: ModuleInstance) -> list[str]:
    """校验已解析的模块实例。

    检查项:
    1. 至少有一个电源端口（power_in 或 power_out）
    2. 必须有 GND 端口（C34）
    3. 所有 electrical_targets key 合法
    4. 器件类别匹配意图类别

    Args:
        instance: 已解析的模块实例

    Returns:
        错误消息列表（空列表表示通过）
    """
    errors: list[str] = []

    if not instance.resolved_ports:
        errors.append(f"[{instance.module_id}] 无端口映射")
        return errors

    # 1. 至少一个电源端口
    power_roles = {"power_in", "power_out"}
    has_power = any(
        p.port_role in power_roles for p in instance.resolved_ports.values()
    )
    if not has_power:
        errors.append(
            f"[{instance.module_id}] 缺少电源端口 (power_in/power_out)"
        )

    # 2. 必须有 GND（C34: 所有 active 模块必须有 GND）
    has_gnd = any(
        p.port_role == "ground" for p in instance.resolved_ports.values()
    )
    if not has_gnd:
        errors.append(
            f"[{instance.module_id}] 缺少接地端口 (GND)"
        )

    # 3. electrical_targets key 合法性
    _VALID_TARGET_KEYS = {
        "v_in", "v_out", "i_out", "i_out_max", "v_in_min", "v_in_max",
        "v_dropout", "fsw", "efficiency", "v_supply", "led_current",
        "led_color", "v_forward", "r_value", "c_value", "l_value",
        "v_ref", "i_bias",
    }
    for key in instance.parameters:
        if key not in _VALID_TARGET_KEYS:
            errors.append(
                f"[{instance.module_id}] 未知 electrical_target: '{key}'"
            )

    # 4. 器件类别匹配
    if instance.device is not None:
        device_cat = getattr(instance.device, "category", "")
        if device_cat and instance.resolved_category:
            if device_cat != instance.resolved_category:
                errors.append(
                    f"[{instance.module_id}] 类别不匹配: "
                    f"器件={device_cat}, 意图={instance.resolved_category}"
                )

    return errors
