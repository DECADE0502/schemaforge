"""AI 设计规划器

将用户自然语言需求解析为结构化的模块需求列表。

用法::

    planner = DesignPlanner()
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
5. **型号精确性**：如果用户明确指定了器件型号（如 TPS54202、AMS1117-3.3），
   你必须原样保留在 part_number 字段，**严禁替换为近似型号或其他型号**。
   TPS54202 就是 TPS54202，不是 TPS54200，不是 TPS5430。
   同时根据上下文推断 category（如 DCDC→buck，稳压→ldo）。
6. connections_to 描述模块间的电气连接关系
7. 当用户同时指定了型号和电路类型，应将两者合并到同一个 module 中，
   不要拆成"型号模块"和"电路模块"两个独立 module。
"""


class DesignPlanner:
    """设计规划器

    将自然语言需求解析为结构化的模块需求列表。
    """

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
    ) -> None:
        self.model = model

    def plan(self, user_input: str) -> DesignPlan:
        """规划设计

        Args:
            user_input: 用户自然语言需求

        Returns:
            DesignPlan 结构化规划结果
        """
        return self._plan_ai(user_input)

    # ----------------------------------------------------------
    # AI 规划
    # ----------------------------------------------------------

    def _plan_ai(self, user_input: str) -> DesignPlan:
        """调用 AI 进行设计规划"""
        try:
            result = call_llm_json(
                system_prompt=PLANNER_SYSTEM_PROMPT,
                user_message=user_input,
                model=self.model,
            )
        except Exception as exc:
            return DesignPlan(
                name="规划失败",
                description=f"AI 调用异常: {exc}",
                raw_input=user_input,
                notes="AI 规划失败（网络或接口异常），已回退为空规划",
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
