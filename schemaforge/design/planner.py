"""AI 设计规划器

将用户自然语言需求解析为结构化的模块需求列表。

两种模式：
- AI 模式: 调用 kimi-k2.5 解析复杂需求
- Mock 模式: 关键字匹配，用于离线测试

用法::

    planner = DesignPlanner(use_mock=True)
    plan = planner.plan("5V转3.3V稳压电路，带LED指示灯")
    # plan.modules = [
    #   ModuleRequirement(role="main_regulator", category="ldo", ...),
    #   ModuleRequirement(role="power_led", category="led", ...),
    # ]
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from schemaforge.ai.client import call_llm_json, DEFAULT_MODEL
from schemaforge.design.retrieval import DeviceRequirement


# ============================================================
# 规划结果
# ============================================================

@dataclass
class ModuleRequirement:
    """单个模块需求 — 由规划器输出"""

    role: str                   # 角色标识 ("main_regulator", "power_led", ...)
    category: str = ""          # 器件分类 ("ldo", "buck", "led", ...)
    description: str = ""       # 功能描述
    part_number: str = ""       # 指定料号（可选）
    parameters: dict[str, str] = field(default_factory=dict)
    # 设计参数 (v_in, v_out, ...)
    connections_to: list[str] = field(default_factory=list)
    # 连接目标角色列表（如 ["main_regulator"]）

    # 拥有拓扑定义的分类 — 只有这些分类才要求 must_have_topology
    _TOPOLOGY_CATEGORIES: frozenset[str] = frozenset({
        "ldo", "buck", "led", "voltage_divider", "rc_filter",
    })

    def to_device_requirement(self) -> DeviceRequirement:
        """转换为检索器的需求格式"""
        # 从 parameters 中提取规格
        spec_keys = {"v_out", "v_in", "i_out_max", "v_dropout"}
        specs = {
            k: v for k, v in self.parameters.items()
            if k in spec_keys
        }

        # 只对已知拥有拓扑模板的分类要求 topology；
        # 用户导入的器件（category="other" / "memory" 等）通常没有拓扑定义
        need_topology = self.category.lower() in self._TOPOLOGY_CATEGORIES

        return DeviceRequirement(
            role=self.role,
            category=self.category,
            query=self.description,
            part_number=self.part_number,
            specs=specs,
            must_have_topology=need_topology,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "category": self.category,
            "description": self.description,
            "part_number": self.part_number,
            "parameters": self.parameters,
            "connections_to": self.connections_to,
        }


@dataclass
class DesignPlan:
    """设计规划结果"""

    name: str = ""              # 设计名称
    description: str = ""       # 设计描述
    modules: list[ModuleRequirement] = field(default_factory=list)
    notes: str = ""             # 设计备注
    raw_input: str = ""         # 原始用户输入

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "modules": [m.to_dict() for m in self.modules],
            "notes": self.notes,
        }


# ============================================================
# 设计规划器
# ============================================================

PLANNER_SYSTEM_PROMPT = """\
你是电路设计规划助手。用户会给你一个电路需求的自然语言描述，
你需要将其拆解为一个或多个模块需求。

请严格按以下 JSON 格式输出，不要输出其他内容：
{
  "name": "设计名称",
  "description": "设计描述",
  "modules": [
    {
      "role": "模块角色标识（英文，如 main_regulator）",
      "category": "器件分类（ldo/buck/led/voltage_divider/rc_filter/passive）",
      "description": "模块功能描述（中文）",
      "part_number": "指定料号（可选，用户没指定则留空）",
      "parameters": {
        "v_in": "输入电压",
        "v_out": "输出电压"
      },
      "connections_to": ["其他模块的role"]
    }
  ],
  "notes": "设计备注"
}

规则：
1. 每个独立功能模块拆为一个 module
2. 电源模块和负载模块分开
3. parameters 只填用户明确给出的参数
4. category 可选: ldo, buck, led, voltage_divider, rc_filter, passive, memory, mcu, sensor, connector, other
   如果用户指定了具体料号，category 可以填 other，料号填入 part_number
5. connections_to 描述模块间的电气连接关系
"""


class DesignPlanner:
    """设计规划器

    将自然语言需求解析为结构化的模块需求列表。
    """

    def __init__(
        self,
        use_mock: bool = True,
        model: str = DEFAULT_MODEL,
    ) -> None:
        self.use_mock = use_mock
        self.model = model

    def plan(self, user_input: str) -> DesignPlan:
        """规划设计

        Args:
            user_input: 用户自然语言需求

        Returns:
            DesignPlan 结构化规划结果
        """
        if self.use_mock:
            return self._plan_mock(user_input)
        return self._plan_ai(user_input)

    # ----------------------------------------------------------
    # AI 规划
    # ----------------------------------------------------------

    def _plan_ai(self, user_input: str) -> DesignPlan:
        """调用 AI 进行设计规划"""
        result = call_llm_json(
            system_prompt=PLANNER_SYSTEM_PROMPT,
            user_message=user_input,
            model=self.model,
        )
        if result is None:
            return DesignPlan(
                name="规划失败",
                description="AI 返回内容无法解析",
                raw_input=user_input,
                notes="AI 规划失败，请重试",
            )
        return self._parse_plan(result, user_input)

    # ----------------------------------------------------------
    # Mock 规划
    # ----------------------------------------------------------

    def _plan_mock(self, user_input: str) -> DesignPlan:
        """基于关键字的 Mock 规划"""
        text = user_input.lower()
        modules: list[ModuleRequirement] = []

        # 提取电压参数
        v_in, v_out = _extract_voltages(user_input)

        # ── 料号直接引用检测 ──
        # 匹配真实料号：必须同时包含字母和数字，且长度 >= 4
        # 排除纯缩写关键字 (LDO, LED, MCU, ADC, DAC, etc.)
        import re
        _KEYWORD_ABBRS = {"LDO", "LED", "MCU", "ADC", "DAC", "USB", "SPI",
                          "I2C", "CAN", "PWM", "GPIO", "UART", "BUCK"}
        pn_match = re.search(
            r"([A-Z][A-Z0-9]{2,}[-]?[A-Z0-9]*\d[A-Z0-9]*)", user_input,
        )
        if pn_match and pn_match.group(1) not in _KEYWORD_ABBRS:
            pn = pn_match.group(1)
            modules.append(ModuleRequirement(
                role="user_specified",
                category="other",
                description=f"用户指定器件 {pn}",
                part_number=pn,
            ))

        # LDO / 稳压
        has_ldo = any(kw in text for kw in ["ldo", "稳压", "线性稳压"])
        if has_ldo:
            params: dict[str, str] = {}
            if v_in:
                params["v_in"] = v_in
            if v_out:
                params["v_out"] = v_out
            modules.append(ModuleRequirement(
                role="main_regulator",
                category="ldo",
                description=f"LDO稳压器 {v_in or '?'}V→{v_out or '?'}V",
                parameters=params,
            ))

        # Buck / 降压
        has_buck = any(kw in text for kw in ["buck", "降压", "开关电源"])
        if has_buck and not has_ldo:
            params = {}
            if v_in:
                params["v_in"] = v_in
            if v_out:
                params["v_out"] = v_out
            modules.append(ModuleRequirement(
                role="main_regulator",
                category="buck",
                description=f"Buck降压转换器 {v_in or '?'}V→{v_out or '?'}V",
                parameters=params,
            ))

        # LED
        has_led = any(kw in text for kw in ["led", "指示灯", "指示"])
        if has_led:
            led_params: dict[str, str] = {}
            # LED 电源来自稳压输出
            if v_out:
                led_params["v_supply"] = v_out
            elif v_in:
                led_params["v_supply"] = v_in

            # 颜色
            for color_cn, color_en in [
                ("红", "red"), ("绿", "green"),
                ("蓝", "blue"), ("白", "white"),
            ]:
                if color_cn in text or color_en in text:
                    led_params["led_color"] = color_en
                    break
            else:
                led_params["led_color"] = "green"

            connections = []
            if modules:
                connections.append(modules[0].role)

            modules.append(ModuleRequirement(
                role="power_led",
                category="led",
                description="LED电源指示灯",
                parameters=led_params,
                connections_to=connections,
            ))

        # 分压器
        has_divider = any(kw in text for kw in ["分压", "divider", "采样"])
        if has_divider and not has_ldo and not has_buck:
            params = {}
            if v_in:
                params["v_in"] = v_in
            if v_out:
                params["v_out"] = v_out
            modules.append(ModuleRequirement(
                role="voltage_sampler",
                category="voltage_divider",
                description=f"电压分压采样 {v_in or '?'}V→{v_out or '?'}V",
                parameters=params,
            ))

        # RC 滤波
        has_filter = any(kw in text for kw in ["滤波", "filter", "rc"])
        if has_filter and not has_ldo and not has_buck:
            modules.append(ModuleRequirement(
                role="input_filter",
                category="rc_filter",
                description="RC低通滤波器",
                parameters={"f_cutoff": "1000"},
            ))

        # 回退：如果没识别出任何模块，给一个通用 LDO
        if not modules:
            modules.append(ModuleRequirement(
                role="main_regulator",
                category="ldo",
                description="默认LDO稳压电路",
                parameters={"v_in": v_in or "5", "v_out": v_out or "3.3"},
            ))

        # 生成设计名
        name_parts: list[str] = []
        if v_in and v_out:
            name_parts.append(f"{v_in}V→{v_out}V")
        role_names = {
            "main_regulator": "稳压电源",
            "power_led": "LED指示",
            "voltage_sampler": "分压采样",
            "input_filter": "滤波器",
        }
        for m in modules:
            rn = role_names.get(m.role, m.role)
            if rn not in name_parts:
                name_parts.append(rn)

        design_name = " + ".join(name_parts) if name_parts else "电路设计"

        return DesignPlan(
            name=design_name,
            description=user_input,
            modules=modules,
            raw_input=user_input,
        )

    # ----------------------------------------------------------
    # 解析 AI 响应
    # ----------------------------------------------------------

    def _parse_plan(self, data: dict[str, Any], raw_input: str) -> DesignPlan:
        """解析 AI 返回的 JSON 为 DesignPlan"""
        modules: list[ModuleRequirement] = []
        for mod_data in data.get("modules", []):
            modules.append(ModuleRequirement(
                role=mod_data.get("role", "unknown"),
                category=mod_data.get("category", ""),
                description=mod_data.get("description", ""),
                part_number=mod_data.get("part_number", ""),
                parameters=mod_data.get("parameters", {}),
                connections_to=mod_data.get("connections_to", []),
            ))

        return DesignPlan(
            name=data.get("name", "未命名设计"),
            description=data.get("description", ""),
            modules=modules,
            notes=data.get("notes", ""),
            raw_input=raw_input,
        )


# ============================================================
# 工具函数
# ============================================================

def _extract_voltages(text: str) -> tuple[str, str]:
    """从文本中提取输入/输出电压

    支持格式：
    - "5V转3.3V"、"5V到3.3V"、"5V→3.3V"
    - "输入5V 输出3.3V"
    - "12v to 3.3v"

    Returns:
        (v_in, v_out) 字符串元组，无法提取则为空串
    """
    import re

    # 模式1: XVY转/到/→ZV
    pattern1 = re.compile(
        r"(\d+(?:\.\d+)?)\s*[Vv]\s*(?:转|到|→|->|to)\s*"
        r"(\d+(?:\.\d+)?)\s*[Vv]?"
    )
    m = pattern1.search(text)
    if m:
        return m.group(1), m.group(2)

    # 模式2: 输入XV ... 输出YV
    v_in = ""
    v_out = ""
    in_match = re.search(r"输入\s*(\d+(?:\.\d+)?)\s*[Vv]?", text)
    out_match = re.search(r"输出\s*(\d+(?:\.\d+)?)\s*[Vv]?", text)
    if in_match:
        v_in = in_match.group(1)
    if out_match:
        v_out = out_match.group(1)

    return v_in, v_out
