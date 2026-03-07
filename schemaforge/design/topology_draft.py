"""拓扑草稿生成器

当器件库中没有预设拓扑时，AI 自动生成拓扑草稿。
核心流程：DeviceModel（无 topology）→ TopologyDraft → TopologyDef

Phase 5 Task 4 — AI auto-generate topology for SchemaForge.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from schemaforge.library.models import (
    DeviceModel,
    ExternalComponent,
    TopologyConnection,
    TopologyDef,
)


# ============================================================
# 草稿数据模型
# ============================================================


@dataclass
class NetDraft:
    """单条网络草稿

    Attributes:
        name: 网络名，如 "VIN"、"GND"
        pin_connections: 连接在本网络上的引脚列表，格式 "RefDes.PinName"，
                        如 ["U1.VIN", "C1.1"]
        is_power: 是否为电源网络
        is_ground: 是否为地网络
    """

    name: str
    pin_connections: list[str] = field(default_factory=list)
    is_power: bool = False
    is_ground: bool = False


@dataclass
class TopologyDraft:
    """拓扑草稿

    AI 生成的中间格式，尚未验证。
    需要经过 validate_draft() + draft_to_topology() 才能使用。

    Attributes:
        name: 电路类型名，如 "ldo"、"led_driver"
        description: 人类可读描述
        nets: 网络草稿列表
        components: 外部元件列表，每项为 dict，包含 role/ref_prefix/default_value 等字段
        layout_hints: 布局建议，供渲染器参考
        confidence: 生成置信度 0-1
        evidence: 证据引用列表（ai_inferred 来源）
    """

    name: str
    description: str = ""
    nets: list[NetDraft] = field(default_factory=list)
    components: list[dict[str, Any]] = field(default_factory=list)
    layout_hints: list[str] = field(default_factory=list)
    confidence: float = 0.8
    evidence: list[dict[str, Any]] = field(default_factory=list)


# ============================================================
# Mock 预设数据
# ============================================================


def _mock_ldo_draft(device: DeviceModel) -> TopologyDraft:
    """LDO 线性稳压器 mock 拓扑草稿"""
    return TopologyDraft(
        name="ldo",
        description=f"{device.part_number} LDO 线性稳压器推荐应用电路",
        nets=[
            NetDraft(
                name="VIN",
                pin_connections=["U1.VIN", "C_in.1"],
                is_power=True,
            ),
            NetDraft(
                name="VOUT",
                pin_connections=["U1.VOUT", "C_out.1"],
                is_power=True,
            ),
            NetDraft(
                name="GND",
                pin_connections=["U1.GND", "C_in.2", "C_out.2"],
                is_ground=True,
            ),
        ],
        components=[
            {
                "role": "input_cap",
                "ref_prefix": "C",
                "ref_alias": "C_in",
                "required": True,
                "default_value": "10uF",
                "schemdraw_element": "Capacitor",
            },
            {
                "role": "output_cap",
                "ref_prefix": "C",
                "ref_alias": "C_out",
                "required": True,
                "default_value": "22uF",
                "schemdraw_element": "Capacitor",
            },
        ],
        layout_hints=["C_in 靠近 U1 的 VIN 引脚", "C_out 靠近 U1 的 VOUT 引脚"],
        confidence=0.95,
        evidence=[
            {
                "source_type": "ai_inferred",
                "summary": "LDO 标准应用电路：输入/输出各一电容，GND 接地",
                "confidence": 0.95,
            }
        ],
    )


def _mock_led_draft(device: DeviceModel) -> TopologyDraft:
    """LED 驱动 mock 拓扑草稿"""
    return TopologyDraft(
        name="led_driver",
        description=f"{device.part_number} LED 驱动电路（限流电阻）",
        nets=[
            NetDraft(
                name="VCC",
                pin_connections=["U1.ANODE", "R_limit.1"],
                is_power=True,
            ),
            NetDraft(
                name="GND",
                pin_connections=["U1.CATHODE", "R_limit.2"],
                is_ground=True,
            ),
        ],
        components=[
            {
                "role": "limit_resistor",
                "ref_prefix": "R",
                "ref_alias": "R_limit",
                "required": True,
                "default_value": "330",
                "schemdraw_element": "Resistor",
            },
        ],
        layout_hints=["R_limit 串联在 LED 阳极"],
        confidence=0.90,
        evidence=[
            {
                "source_type": "ai_inferred",
                "summary": "LED 标准驱动：串联限流电阻保护 LED",
                "confidence": 0.90,
            }
        ],
    )


def _mock_voltage_divider_draft(device: DeviceModel) -> TopologyDraft:
    """分压器 mock 拓扑草稿"""
    return TopologyDraft(
        name="voltage_divider",
        description=f"{device.part_number} 电阻分压器",
        nets=[
            NetDraft(
                name="VIN",
                pin_connections=["R1.1"],
                is_power=True,
            ),
            NetDraft(
                name="VMID",
                pin_connections=["R1.2", "R2.1"],
                is_power=False,
            ),
            NetDraft(
                name="GND",
                pin_connections=["R2.2"],
                is_ground=True,
            ),
        ],
        components=[
            {
                "role": "upper_resistor",
                "ref_prefix": "R",
                "ref_alias": "R1",
                "required": True,
                "default_value": "10k",
                "schemdraw_element": "Resistor",
            },
            {
                "role": "lower_resistor",
                "ref_prefix": "R",
                "ref_alias": "R2",
                "required": True,
                "default_value": "3.3k",
                "schemdraw_element": "Resistor",
            },
        ],
        layout_hints=["R1 接 VIN，R2 接 GND，中点为 VMID 输出"],
        confidence=0.92,
        evidence=[
            {
                "source_type": "ai_inferred",
                "summary": "分压器：R1 上拉，R2 下拉，中点分压",
                "confidence": 0.92,
            }
        ],
    )


def _mock_buck_draft(device: DeviceModel) -> TopologyDraft:
    """Buck 降压转换器 mock 拓扑草稿"""
    return TopologyDraft(
        name="buck",
        description=f"{device.part_number} Buck 降压转换器推荐电路",
        nets=[
            NetDraft(
                name="VIN",
                pin_connections=["U1.VIN", "C_in.1"],
                is_power=True,
            ),
            NetDraft(
                name="SW",
                pin_connections=["U1.SW", "L1.1"],
                is_power=False,
            ),
            NetDraft(
                name="VOUT",
                pin_connections=["U1.VOUT", "L1.2", "C_out.1"],
                is_power=True,
            ),
            NetDraft(
                name="BOOT",
                pin_connections=["U1.BOOT", "C_boot.1"],
                is_power=False,
            ),
            NetDraft(
                name="GND",
                pin_connections=["U1.GND", "C_in.2", "C_out.2", "C_boot.2"],
                is_ground=True,
            ),
        ],
        components=[
            {
                "role": "input_cap",
                "ref_prefix": "C",
                "ref_alias": "C_in",
                "required": True,
                "default_value": "10uF",
                "schemdraw_element": "Capacitor",
            },
            {
                "role": "inductor",
                "ref_prefix": "L",
                "ref_alias": "L1",
                "required": True,
                "default_value": "4.7uH",
                "schemdraw_element": "Inductor",
            },
            {
                "role": "output_cap",
                "ref_prefix": "C",
                "ref_alias": "C_out",
                "required": True,
                "default_value": "22uF",
                "schemdraw_element": "Capacitor",
            },
            {
                "role": "boot_cap",
                "ref_prefix": "C",
                "ref_alias": "C_boot",
                "required": True,
                "default_value": "100nF",
                "schemdraw_element": "Capacitor",
            },
        ],
        layout_hints=[
            "C_in 靠近 VIN",
            "L1 靠近 SW",
            "C_out 靠近 VOUT",
            "C_boot 靠近 BOOT 和 SW",
        ],
        confidence=0.88,
        evidence=[
            {
                "source_type": "ai_inferred",
                "summary": "Buck 标准拓扑：VIN→C_in，SW→L1→VOUT→C_out，BOOT→C_boot",
                "confidence": 0.88,
            }
        ],
    )


# Mock 关键词到生成函数的映射
_MOCK_REGISTRY: dict[str, Any] = {
    "ldo": _mock_ldo_draft,
    "led": _mock_led_draft,
    "led_driver": _mock_led_draft,
    "voltage_divider": _mock_voltage_divider_draft,
    "divider": _mock_voltage_divider_draft,
    "buck": _mock_buck_draft,
}


# ============================================================
# 拓扑草稿生成器
# ============================================================


class TopologyDraftGenerator:
    """拓扑草稿生成器

    当器件库中没有预置拓扑时，利用 AI（或 mock）自动生成拓扑草稿。

    Args:
        use_mock: 是否使用 mock 预设（True=离线/测试，False=调用真实 LLM）
    """

    def __init__(self, use_mock: bool = True) -> None:
        self.use_mock = use_mock

    # ----------------------------------------------------------
    # 公开接口
    # ----------------------------------------------------------

    def generate(
        self,
        device: DeviceModel,
        context: dict[str, Any] | None = None,
    ) -> TopologyDraft:
        """为器件生成拓扑草稿

        Args:
            device: 目标器件（topology 应为 None）
            context: 额外上下文信息（参数、设计意图等）

        Returns:
            TopologyDraft 草稿

        Raises:
            ValueError: 无法识别器件类型，且 use_mock=True 也没有对应预设
        """
        if self.use_mock:
            return self._mock_generate(device, context)
        else:
            return self._llm_generate(device, context)

    def validate_draft(
        self,
        draft: TopologyDraft,
        device: DeviceModel,
    ) -> list[str]:
        """验证拓扑草稿合理性

        检查规则：
        1. 所有必须连接的 IC 引脚（按 symbol.pins 中的 required 逻辑）都已出现在某个网络
        2. 没有只有一个连接的非 power/ground 浮空网络（最少 2 个端点）
        3. 同一引脚不能出现在两个不同网络中
        4. power 网络标记与 is_ground 互斥

        Args:
            draft: 待验证的草稿
            device: 器件模型（用于获取引脚列表）

        Returns:
            错误列表，空列表表示验证通过
        """
        errors: list[str] = []

        # --- 规则 3：同一引脚只能在一个网络 ---
        seen_pins: dict[str, str] = {}  # pin_id → net_name
        for net in draft.nets:
            for pin in net.pin_connections:
                if pin in seen_pins:
                    errors.append(
                        f"引脚 {pin!r} 重复出现在网络 {seen_pins[pin]!r} 和 {net.name!r}"
                    )
                else:
                    seen_pins[pin] = net.name

        # --- 规则 2：浮空单端网络 ---
        for net in draft.nets:
            if len(net.pin_connections) < 2:
                errors.append(
                    f"网络 {net.name!r} 只有 {len(net.pin_connections)} 个连接点，"
                    f"可能为浮空网络"
                )

        # --- 规则 4：power/ground 互斥 ---
        for net in draft.nets:
            if net.is_power and net.is_ground:
                errors.append(
                    f"网络 {net.name!r} 同时标记为 is_power 和 is_ground，逻辑矛盾"
                )

        # --- 规则 1：IC 必要引脚连接检查（仅当 device 有 symbol 时） ---
        if device.symbol:
            all_connected_pins: set[str] = set()
            for net in draft.nets:
                for pin_ref in net.pin_connections:
                    # 格式 "U1.VIN" 或 "C1.1"
                    if pin_ref.startswith("U1."):
                        all_connected_pins.add(pin_ref[3:])  # 去掉 "U1."

            for sym_pin in device.symbol.pins:
                if sym_pin.name not in all_connected_pins:
                    errors.append(f"IC 引脚 {sym_pin.name!r} 未在任何网络中连接")

        return errors

    def draft_to_topology(
        self,
        draft: TopologyDraft,
        device: DeviceModel,
    ) -> TopologyDef:
        """将草稿转换为标准 TopologyDef

        转换规则：
        - components → ExternalComponent 列表
        - nets → TopologyConnection 列表
            * "U1.XYZ" → device_pin="XYZ"
            * "C_in.1" → external_refs 中对应 role 的 ref_alias

        Args:
            draft: 已通过 validate_draft 的草稿
            device: 器件模型

        Returns:
            可直接挂到 DeviceModel.topology 的 TopologyDef
        """
        # 构建 ref_alias → role 映射
        alias_to_role: dict[str, str] = {}
        for comp in draft.components:
            alias = comp.get("ref_alias", "")
            role = comp.get("role", "")
            if alias and role:
                alias_to_role[alias] = role

        # 构建外部元件列表
        external_components: list[ExternalComponent] = []
        for comp in draft.components:
            external_components.append(
                ExternalComponent(
                    role=comp.get("role", "unknown"),
                    ref_prefix=comp.get("ref_prefix", "X"),
                    required=comp.get("required", True),
                    default_value=comp.get("default_value", ""),
                    schemdraw_element=comp.get("schemdraw_element", ""),
                )
            )

        # 构建连接列表
        connections: list[TopologyConnection] = []
        for net in draft.nets:
            device_pin = ""
            external_refs: list[str] = []

            for pin_ref in net.pin_connections:
                if "." not in pin_ref:
                    continue
                ref_des, pin_name = pin_ref.split(".", 1)

                if ref_des == "U1":
                    device_pin = pin_name
                else:
                    # ref_des 是 alias（如 "C_in"），查找对应 role
                    role = alias_to_role.get(ref_des, ref_des)
                    external_refs.append(f"{role}.{pin_name}")

            connections.append(
                TopologyConnection(
                    net_name=net.name,
                    device_pin=device_pin,
                    external_refs=external_refs,
                    is_power=net.is_power,
                    is_ground=net.is_ground,
                )
            )

        return TopologyDef(
            circuit_type=draft.name,
            external_components=external_components,
            connections=connections,
        )

    # ----------------------------------------------------------
    # 内部实现
    # ----------------------------------------------------------

    def _mock_generate(
        self,
        device: DeviceModel,
        context: dict[str, Any] | None = None,
    ) -> TopologyDraft:
        """基于 mock 预设生成草稿"""
        # 1. 优先匹配 category
        category = device.category.lower() if device.category else ""
        if category in _MOCK_REGISTRY:
            return _MOCK_REGISTRY[category](device)

        # 2. 尝试从 part_number / description 中模糊匹配
        search_text = f"{device.part_number} {device.description}".lower()
        for keyword, fn in _MOCK_REGISTRY.items():
            if keyword in search_text:
                return fn(device)

        # 3. 兜底：抛出异常（可以由调用方降级处理）
        raise ValueError(
            f"无法为器件 {device.part_number!r}（category={device.category!r}）"
            f"自动生成拓扑草稿，没有匹配的 mock 预设。"
            f"可用预设类型：{list(_MOCK_REGISTRY.keys())}"
        )

    def _llm_generate(
        self,
        device: DeviceModel,
        context: dict[str, Any] | None = None,
    ) -> TopologyDraft:
        """调用真实 LLM 生成拓扑草稿。

        构建 prompt 描述器件引脚和用途，让 AI 输出 JSON 格式的
        网络连接 + 外部元件列表，然后转化为 TopologyDraft。
        如果 AI 调用失败或返回无效数据，降级到 mock 生成。
        """
        from schemaforge.ai.client import call_llm_json

        pins_desc = ""
        if device.symbol and device.symbol.pins:
            pins_desc = "\n".join(
                f"  - {p.name} (Pin {p.pin_number}): {p.pin_type.value}, {p.side.value}"
                for p in device.symbol.pins
            )

        ctx_desc = ""
        if context:
            ctx_desc = "\n".join(f"  {k}: {v}" for k, v in context.items())

        system_prompt = """\
你是电路拓扑生成助手。根据器件信息，输出该器件的典型应用电路拓扑。

## 输出格式（严格 JSON）
{
  "name": "拓扑名（如 ldo / buck / boost）",
  "description": "中文描述",
  "components": [
    {
      "role": "元件角色（如 input_cap / output_cap / inductor / fb_upper / fb_lower）",
      "ref_prefix": "参考标号前缀（C/R/L）",
      "ref_alias": "图中标号（如 C_in / C_out）",
      "default_value": "默认值（如 10uF / 10kΩ）",
      "schemdraw_element": "Capacitor / Resistor / Inductor2",
      "required": true
    }
  ],
  "nets": [
    {
      "name": "网络名（如 VIN / VOUT / GND / SW / FB）",
      "pin_connections": ["U1.引脚名", "C_in.1", ...],
      "is_power": false,
      "is_ground": false
    }
  ]
}

## 规则
1. IC 器件标号固定为 U1
2. 外部元件用 ref_alias 标号
3. pin_connections 格式为 "标号.引脚名"
4. 必须包含 VIN、VOUT（或等效）和 GND 网络
5. 只输出 JSON，不要其他内容
"""

        user_msg = f"""\
器件型号: {device.part_number}
类别: {device.category or "未知"}
描述: {device.description or "无"}
引脚:
{pins_desc or "  无引脚信息"}
设计上下文:
{ctx_desc or "  无额外上下文"}

请为该器件生成典型应用电路的拓扑连接。"""

        try:
            data = call_llm_json(
                system_prompt=system_prompt,
                user_message=user_msg,
                temperature=0.2,
                max_retries=2,
            )
        except Exception:
            data = None

        if data is None:
            # AI 失败，降级到 mock
            return self._mock_generate(device, context)

        # 解析 AI 输出为 TopologyDraft
        try:
            nets: list[NetDraft] = []
            for net_data in data.get("nets", []):
                nets.append(NetDraft(
                    name=net_data.get("name", ""),
                    pin_connections=net_data.get("pin_connections", []),
                    is_power=net_data.get("is_power", False),
                    is_ground=net_data.get("is_ground", False),
                ))

            components: list[dict[str, Any]] = []
            for comp_data in data.get("components", []):
                components.append({
                    "role": comp_data.get("role", "unknown"),
                    "ref_prefix": comp_data.get("ref_prefix", "X"),
                    "ref_alias": comp_data.get("ref_alias", ""),
                    "default_value": comp_data.get("default_value", ""),
                    "schemdraw_element": comp_data.get("schemdraw_element", ""),
                    "required": comp_data.get("required", True),
                })

            draft = TopologyDraft(
                name=data.get("name", device.category or "unknown"),
                description=data.get("description", ""),
                nets=nets,
                components=components,
                confidence=0.7,
                evidence=[{
                    "source_type": "ai_inferred",
                    "summary": "AI 自动生成的拓扑草稿",
                }],
            )

            # 基本验证: 至少有 1 个网络和 1 个元件
            if not draft.nets or not draft.components:
                return self._mock_generate(device, context)

            return draft

        except (KeyError, TypeError, ValueError):
            # 解析失败，降级到 mock
            return self._mock_generate(device, context)
