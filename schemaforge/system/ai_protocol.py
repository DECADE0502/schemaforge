"""系统级 AI 意图解析协议。

负责：
- 系统提示词定义（SYSTEM_PARSE_PROMPT）
- AI 输出 JSON schema 校验（AISystemParseResponse）
- AI JSON → SystemDesignRequest 归一化
- 歧义检测

AI 只做意图理解，所有工程决策通过本地确定性接口完成。
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from schemaforge.system.models import (
    ConnectionIntent,
    ConnectionSemantic,
    ModuleIntent,
    SignalType,
    SystemDesignRequest,
)

logger = logging.getLogger(__name__)

# ============================================================
# T021: 系统提示词
# ============================================================

SYSTEM_PARSE_PROMPT = """\
你是一个电子系统架构解析器。用户会用自然语言描述一个多器件电源/控制系统。

你的任务是从用户描述中提取：
1. 所有模块（器件）及其角色、型号、类别、电气目标
2. 模块间的连接意图（电源链、GPIO 控制、总线等）
3. 全局输入电压
4. 任何不确定或缺失的信息

## 严格规则
- 用户指定的精确型号必须原样保留到 part_number_hint，不得替换
- 不要输出电阻/电容/电感数值（由本地公式引擎计算）
- 不要输出原理图坐标或 SVG 结构
- 不要输出 BOM 编号或 SPICE 节点名
- 没把握的信息必须放入 ambiguities 列表
- 不确定的字段留空字符串，不要猜测

## 输出格式
严格输出以下 JSON，不要包含任何其他文本：
{
  "modules": [
    {
      "intent_id": "唯一标识，如 buck1, ldo1, mcu1, led1",
      "role": "模块角色描述（中文）",
      "part_number_hint": "用户指定的精确型号，未指定则为空字符串",
      "category_hint": "类别: buck/ldo/boost/flyback/sepic/opamp/mcu/led/sensor/other",
      "electrical_targets": {
        "v_in": "输入电压（纯数字字符串，单位V）",
        "v_out": "输出电压（纯数字字符串，单位V）",
        "i_out": "输出电流（纯数字字符串，单位A，可选）"
      },
      "placement_hint": "power_chain / control_side / sensor_side / other",
      "priority": 0
    }
  ],
  "connections": [
    {
      "connection_id": "c1",
      "src_module_intent": "源模块 intent_id",
      "src_port_hint": "VOUT / PA1 / SCL 等，不确定留空",
      "dst_module_intent": "目标模块 intent_id",
      "dst_port_hint": "VIN / ANODE / SDA 等，不确定留空",
      "signal_type": "power_supply / ground / gpio / spi / i2c / uart / analog / enable / feedback / other",
      "connection_semantics": "supply_chain / gpio_drive / bus_connect / feedback_loop / enable_control / ground_tie / unknown"
    }
  ],
  "global_v_in": "系统总输入电压（纯数字字符串）",
  "ambiguities": ["不确定项1", "不确定项2"],
  "design_notes": "额外的设计说明"
}
"""

# ============================================================
# T026: Pydantic schema
# ============================================================


class AIModuleSchema(BaseModel):
    """AI 输出的单个模块 schema。"""

    intent_id: str
    role: str = ""
    part_number_hint: str = ""
    category_hint: str = ""
    electrical_targets: dict[str, str] = Field(default_factory=dict)
    control_targets: dict[str, str] = Field(default_factory=dict)
    placement_hint: str = ""
    priority: int = 0


class AIConnectionSchema(BaseModel):
    """AI 输出的单个连接 schema。"""

    connection_id: str
    src_module_intent: str = ""
    src_port_hint: str = ""
    dst_module_intent: str = ""
    dst_port_hint: str = ""
    signal_type: str = "other"
    connection_semantics: str = "unknown"


class AISystemParseResponse(BaseModel):
    """AI 系统级解析完整输出 schema。

    用于校验 AI 返回的 JSON 是否合法。
    """

    modules: list[AIModuleSchema] = Field(default_factory=list)
    connections: list[AIConnectionSchema] = Field(default_factory=list)
    global_v_in: str = ""
    ambiguities: list[str] = Field(default_factory=list)
    design_notes: str = ""


# ============================================================
# T027: validate_ai_schema
# ============================================================


def validate_ai_schema(raw_json: dict[str, Any]) -> list[str]:
    """校验 AI 原始 JSON 是否符合 schema。

    Args:
        raw_json: AI 返回的原始字典

    Returns:
        错误列表，空列表表示校验通过
    """
    errors: list[str] = []
    try:
        AISystemParseResponse.model_validate(raw_json)
    except ValidationError as exc:
        for err in exc.errors():
            loc = ".".join(str(p) for p in err["loc"])
            errors.append(f"{loc}: {err['msg']}")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"schema 校验异常: {exc}")
    return errors


# ============================================================
# T028: normalize_ai_intents
# ============================================================

_VALID_SIGNAL_TYPES = {e.value for e in SignalType}
_VALID_SEMANTICS = {e.value for e in ConnectionSemantic}


def _normalize_str(value: Any) -> str:
    """将任意值归一化为字符串。"""
    if value is None:
        return ""
    return str(value).strip()


def _normalize_signal_type(raw: str) -> SignalType:
    """归一化信号类型字符串为枚举。"""
    cleaned = raw.strip().lower()
    if cleaned in _VALID_SIGNAL_TYPES:
        return SignalType(cleaned)
    return SignalType.OTHER


def _normalize_semantic(raw: str) -> ConnectionSemantic:
    """归一化连接语义字符串为枚举。"""
    cleaned = raw.strip().lower()
    if cleaned in _VALID_SEMANTICS:
        return ConnectionSemantic(cleaned)
    return ConnectionSemantic.UNKNOWN


def normalize_ai_intents(
    raw_json: dict[str, Any],
    raw_text: str = "",
) -> SystemDesignRequest:
    """将已校验的 AI JSON 转换为 SystemDesignRequest。

    处理缺失字段、类型归一化、字符串清洗。

    Args:
        raw_json: AI 返回的原始字典（应已通过 validate_ai_schema 校验）
        raw_text: 用户原始输入文本

    Returns:
        SystemDesignRequest 实例
    """
    try:
        parsed = AISystemParseResponse.model_validate(raw_json)
    except ValidationError:
        # 尽力解析，即使部分字段无效
        parsed = AISystemParseResponse()

    modules: list[ModuleIntent] = []
    for m in parsed.modules:
        # 归一化 electrical_targets：确保值都是字符串
        elec = {k: _normalize_str(v) for k, v in m.electrical_targets.items()}
        ctrl = {k: _normalize_str(v) for k, v in m.control_targets.items()}

        modules.append(ModuleIntent(
            intent_id=_normalize_str(m.intent_id),
            role=_normalize_str(m.role),
            part_number_hint=_normalize_str(m.part_number_hint),
            category_hint=_normalize_str(m.category_hint).lower(),
            electrical_targets=elec,
            control_targets=ctrl,
            placement_hint=_normalize_str(m.placement_hint),
            priority=m.priority,
        ))

    connections: list[ConnectionIntent] = []
    for c in parsed.connections:
        connections.append(ConnectionIntent(
            connection_id=_normalize_str(c.connection_id),
            src_module_intent=_normalize_str(c.src_module_intent),
            src_port_hint=_normalize_str(c.src_port_hint),
            dst_module_intent=_normalize_str(c.dst_module_intent),
            dst_port_hint=_normalize_str(c.dst_port_hint),
            signal_type=_normalize_signal_type(c.signal_type),
            connection_semantics=_normalize_semantic(c.connection_semantics),
        ))

    return SystemDesignRequest(
        raw_text=raw_text,
        modules=modules,
        connections=connections,
        global_v_in=_normalize_str(parsed.global_v_in),
        ambiguities=list(parsed.ambiguities),
        design_notes=_normalize_str(parsed.design_notes),
    )


# ============================================================
# T029: detect_ambiguities
# ============================================================


def detect_ambiguities(request: SystemDesignRequest) -> list[str]:
    """检测 SystemDesignRequest 中的歧义和缺失信息。

    检查规则：
    - 模块既没有 part_number_hint 也没有 category_hint → 歧义
    - 连接缺少 src 或 dst → 歧义
    - 电气目标缺少 v_in 或 v_out（对于电源类模块）→ 歧义

    Args:
        request: 已归一化的 SystemDesignRequest

    Returns:
        歧义描述列表
    """
    ambiguities: list[str] = []

    _POWER_CATEGORIES = {"buck", "ldo", "boost", "flyback", "sepic", "opamp"}

    for m in request.modules:
        # 既没有型号也没有类别
        if not m.part_number_hint and not m.category_hint:
            ambiguities.append(
                f"模块 '{m.intent_id}' 缺少 part_number_hint 和 category_hint，"
                f"无法确定器件类型"
            )

        # 电源类模块缺少关键电气目标
        if m.category_hint in _POWER_CATEGORIES:
            elec = m.electrical_targets
            if not elec.get("v_in"):
                ambiguities.append(
                    f"模块 '{m.intent_id}' ({m.category_hint}) 缺少 v_in 电气目标"
                )
            if not elec.get("v_out"):
                ambiguities.append(
                    f"模块 '{m.intent_id}' ({m.category_hint}) 缺少 v_out 电气目标"
                )

    for c in request.connections:
        if not c.src_module_intent:
            ambiguities.append(
                f"连接 '{c.connection_id}' 缺少源模块 (src_module_intent)"
            )
        if not c.dst_module_intent:
            ambiguities.append(
                f"连接 '{c.connection_id}' 缺少目标模块 (dst_module_intent)"
            )

    return ambiguities


# ============================================================
# 正则 fallback 解析（当 AI 调用失败时）
# ============================================================

# 使用 lookaround 而非 \b（\b 在中文文本中不匹配）
_PART_NUMBER_RE = re.compile(
    r"(?<![A-Za-z0-9])([A-Z][A-Z0-9]{2,}[-]?[A-Z0-9]*\d[A-Z0-9]*)(?![A-Za-z0-9])",
    re.IGNORECASE,
)
_VOLTAGE_INPUT_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*[Vv]\s*输入|输入\s*(\d+(?:\.\d+)?)\s*[Vv]",
)
# 电压转换链：允许中间有少量中文字符（如"降压到"）
_VOLTAGE_CHAIN_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*[Vv]\s*[\u4e00-\u9fff]{0,6}\s*(?:转|到|→|->)\s*(\d+(?:\.\d+)?)\s*[Vv]",
)
# 所有电压值
_ALL_VOLTAGE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*[Vv]")

_KEYWORD_ABBRS = {
    "LDO", "LED", "MCU", "ADC", "DAC", "USB", "SPI",
    "I2C", "CAN", "PWM", "GPIO", "UART", "BUCK", "GND",
    "VCC", "VIN", "SDA", "SCL",
}

_CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "buck": ["buck", "降压", "开关电源", "dcdc", "dc-dc", "step-down"],
    "ldo": ["ldo", "稳压", "线性稳压", "low dropout"],
    "boost": ["boost", "升压", "step-up"],
    "flyback": ["flyback", "反激"],
    "sepic": ["sepic"],
    "opamp": ["opamp", "运放", "运算放大"],
    "mcu": ["mcu", "单片机", "微控制器", "stm32", "esp32", "arduino"],
    "led": ["led", "指示灯", "发光二极管"],
    "sensor": ["sensor", "传感器"],
}


def _detect_category(text: str) -> str:
    """从文本中推断电路类别。"""
    lower = text.lower()
    for cat, keywords in _CATEGORY_KEYWORDS.items():
        if any(kw in lower for kw in keywords):
            return cat
    return ""


def _extract_part_numbers(text: str) -> list[str]:
    """从文本中提取所有可能的器件型号。"""
    matches = _PART_NUMBER_RE.findall(text)
    return [m for m in matches if m.upper() not in _KEYWORD_ABBRS]


def _extract_voltage_chain(text: str) -> list[tuple[str, str]]:
    """提取电压转换链，如 '20V到5V' → [('20', '5')]。"""
    return _VOLTAGE_CHAIN_RE.findall(text)


def _extract_global_v_in(raw_text: str) -> str:
    """提取全局输入电压。

    优先级：
    1. 显式 "NV输入" / "输入NV"
    2. 电压链中的第一个源电压
    3. 所有电压值中的最大值
    """
    # 显式输入电压
    m = _VOLTAGE_INPUT_RE.search(raw_text)
    if m:
        return m.group(1) or m.group(2) or ""

    # 电压链
    chains = _VOLTAGE_CHAIN_RE.findall(raw_text)
    if chains:
        return chains[0][0]

    # 最大电压值
    all_v = _ALL_VOLTAGE_RE.findall(raw_text)
    if all_v:
        return max(all_v, key=float)

    return ""


def regex_fallback_parse(raw_text: str) -> SystemDesignRequest:
    """正则 fallback 解析器。

    当 AI 调用失败时，使用正则表达式从用户文本中提取基本信息。
    精度有限但保证不抛异常。

    Args:
        raw_text: 用户原始输入

    Returns:
        SystemDesignRequest（尽力填充）
    """
    part_numbers = _extract_part_numbers(raw_text)
    voltage_chains = _extract_voltage_chain(raw_text)
    all_voltages = _ALL_VOLTAGE_RE.findall(raw_text)
    category = _detect_category(raw_text)

    modules: list[ModuleIntent] = []
    connections: list[ConnectionIntent] = []
    ambiguities: list[str] = []

    # 从所有电压值构建有序电压列表（降序，用于推断电源链）
    voltage_list = sorted(set(all_voltages), key=float, reverse=True)

    # 为每个检测到的器件型号创建模块
    for i, pn in enumerate(part_numbers):
        cat = _detect_category(raw_text) if i == 0 else ""
        elec: dict[str, str] = {}
        if i < len(voltage_chains):
            elec["v_in"] = voltage_chains[i][0]
            elec["v_out"] = voltage_chains[i][1]
        elif len(voltage_list) > i + 1:
            # 从电压列表推断：第 i 个模块的输入是第 i 大电压
            elec["v_in"] = voltage_list[i]
            elec["v_out"] = voltage_list[i + 1]
        elif voltage_chains and i > 0:
            # 尝试从前一个电压链的输出推断
            elec["v_in"] = voltage_chains[-1][1]

        modules.append(ModuleIntent(
            intent_id=f"module{i + 1}",
            role=f"器件 {pn}",
            part_number_hint=pn,
            category_hint=cat,
            electrical_targets=elec,
            priority=i,
        ))

    # 如果没有检测到型号但有类别关键字，创建通用模块
    if not modules and category:
        elec = {}
        if voltage_chains:
            elec["v_in"] = voltage_chains[0][0]
            elec["v_out"] = voltage_chains[0][1]
        elif len(voltage_list) >= 2:
            elec["v_in"] = voltage_list[0]
            elec["v_out"] = voltage_list[1]
        modules.append(ModuleIntent(
            intent_id=f"{category}1",
            role=f"{category} 模块",
            category_hint=category,
            electrical_targets=elec,
            priority=0,
        ))

    # 建立相邻模块之间的连接
    for i in range(len(modules) - 1):
        connections.append(ConnectionIntent(
            connection_id=f"c{i + 1}",
            src_module_intent=modules[i].intent_id,
            src_port_hint="VOUT",
            dst_module_intent=modules[i + 1].intent_id,
            dst_port_hint="VIN",
            signal_type=SignalType.POWER_SUPPLY,
            connection_semantics=ConnectionSemantic.SUPPLY_CHAIN,
        ))

    if not modules:
        ambiguities.append("无法从文本中提取任何模块信息")

    # 全局输入电压
    global_v_in = _extract_global_v_in(raw_text)

    return SystemDesignRequest(
        raw_text=raw_text,
        modules=modules,
        connections=connections,
        global_v_in=global_v_in,
        ambiguities=ambiguities,
        design_notes="正则 fallback 解析",
    )


# ============================================================
# 完整解析流程
# ============================================================


def _should_skip_ai_parse() -> bool:
    """检查是否跳过 AI 解析（测试环境自动跳过）。"""
    raw = os.environ.get("SCHEMAFORGE_SKIP_AI_PARSE", "")
    return raw.strip().lower() not in {"", "0", "false", "no", "off"}


def parse_system_intent(raw_text: str) -> SystemDesignRequest:
    """完整的系统级意图解析流程。

    流程：
    1. 尝试 AI 解析（调用 LLM）
    2. 校验 AI 输出 schema
    3. 归一化为 SystemDesignRequest
    4. 检测歧义并合并
    5. AI 失败时回退到正则解析

    Args:
        raw_text: 用户自然语言描述

    Returns:
        SystemDesignRequest
    """
    if _should_skip_ai_parse():
        logger.info("AI 解析已跳过（SCHEMAFORGE_SKIP_AI_PARSE），使用正则 fallback")
        result = regex_fallback_parse(raw_text)
        result.ambiguities.extend(detect_ambiguities(result))
        return result

    try:
        from schemaforge.ai.client import call_llm_json

        ai_result = call_llm_json(
            system_prompt=SYSTEM_PARSE_PROMPT,
            user_message=raw_text,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("AI 调用失败: %s，使用正则 fallback", exc)
        result = regex_fallback_parse(raw_text)
        result.ambiguities.extend(detect_ambiguities(result))
        return result

    if ai_result is None:
        logger.warning("AI 返回 None，使用正则 fallback")
        result = regex_fallback_parse(raw_text)
        result.ambiguities.extend(detect_ambiguities(result))
        return result

    # 校验 schema
    errors = validate_ai_schema(ai_result)
    if errors:
        logger.warning("AI 输出 schema 校验失败: %s，使用正则 fallback", errors)
        result = regex_fallback_parse(raw_text)
        result.ambiguities.extend(detect_ambiguities(result))
        return result

    # 归一化
    request = normalize_ai_intents(ai_result, raw_text=raw_text)

    # 检测歧义并合并
    detected = detect_ambiguities(request)
    request.ambiguities.extend(detected)

    return request
