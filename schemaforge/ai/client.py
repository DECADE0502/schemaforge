"""SchemaForge LLM API客户端

封装OpenAI兼容接口调用，默认使用DashScope的kimi-k2.5模型。
"""

from __future__ import annotations

import json
import os
from typing import Any

from openai import OpenAI

# === 默认配置（写死kimi-k2.5） ===
DEFAULT_API_KEY = "sk-sp-396701e02c95411783e01557524e4366"
DEFAULT_BASE_URL = "https://coding.dashscope.aliyuncs.com/v1"
DEFAULT_MODEL = "kimi-k2.5"


def get_client(
    api_key: str | None = None,
    base_url: str | None = None,
) -> OpenAI:
    """获取OpenAI客户端实例

    Args:
        api_key: API密钥，None则使用默认值
        base_url: API基地址，None则使用默认值

    Returns:
        OpenAI客户端
    """
    key = api_key or os.environ.get("DASHSCOPE_API_KEY", DEFAULT_API_KEY)
    url = base_url or os.environ.get("DASHSCOPE_BASE_URL", DEFAULT_BASE_URL)
    return OpenAI(api_key=key, base_url=url)


def call_llm(
    system_prompt: str,
    user_message: str,
    model: str = DEFAULT_MODEL,
    temperature: float = 0.3,
    max_tokens: int = 2048,
    api_key: str | None = None,
    base_url: str | None = None,
) -> str:
    """调用LLM获取响应

    Args:
        system_prompt: 系统prompt
        user_message: 用户消息
        model: 模型名称
        temperature: 温度参数
        max_tokens: 最大token数
        api_key: API密钥
        base_url: API基地址

    Returns:
        LLM的文本响应
    """
    client = get_client(api_key, base_url)

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        temperature=temperature,
        max_tokens=max_tokens,
    )

    content = response.choices[0].message.content
    return content or ""


def call_llm_json(
    system_prompt: str,
    user_message: str,
    model: str = DEFAULT_MODEL,
    temperature: float = 0.1,
    max_retries: int = 3,
    api_key: str | None = None,
    base_url: str | None = None,
) -> dict[str, Any] | None:
    """调用LLM并解析JSON响应

    自动重试：如果响应不是合法JSON，会提示LLM修正并重试。

    Args:
        system_prompt: 系统prompt
        user_message: 用户消息
        model: 模型名称
        temperature: 温度参数
        max_retries: 最大重试次数
        api_key: API密钥
        base_url: API基地址

    Returns:
        解析后的JSON字典，失败返回None
    """
    client = get_client(api_key, base_url)

    messages: list[dict[str, str]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]

    for attempt in range(max_retries):
        response = client.chat.completions.create(
            model=model,
            messages=messages,  # type: ignore[arg-type]
            temperature=temperature,
            max_tokens=2048,
        )

        raw = response.choices[0].message.content or ""

        # 尝试解析JSON
        parsed = _extract_json(raw)
        if parsed is not None:
            return parsed

        # 解析失败，追加对话要求修正
        messages.append({"role": "assistant", "content": raw})
        messages.append({
            "role": "user",
            "content": (
                "你的输出不是合法的JSON格式。请严格按照要求，只输出JSON，"
                "不要包含任何markdown代码块标记或其他文本。"
                f"这是第{attempt + 2}次尝试。"
            ),
        })

    return None


def _extract_json(text: str) -> dict[str, Any] | None:
    """从文本中提取JSON

    支持：
    1. 纯JSON文本
    2. markdown代码块包裹的JSON
    3. JSON前后有少量文本
    """
    text = text.strip()

    # 尝试直接解析
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 尝试移除markdown代码块
    if "```" in text:
        lines = text.split("\n")
        json_lines: list[str] = []
        in_block = False
        for line in lines:
            if line.strip().startswith("```"):
                if in_block:
                    break
                in_block = True
                continue
            if in_block:
                json_lines.append(line)
        if json_lines:
            try:
                return json.loads("\n".join(json_lines))
            except json.JSONDecodeError:
                pass

    # 尝试找到第一个{和最后一个}
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass

    return None


# ============================================================
# 离线Fallback（Mock LLM）
# ============================================================

# 预定义的示例响应，用于离线演示
DEMO_RESPONSES: dict[str, dict[str, Any]] = {
    "ldo_led": {
        "design_name": "5V-3.3V稳压电源（带LED指示）",
        "description": "使用AMS1117-3.3 LDO线性稳压器将5V转为3.3V，并用绿色LED指示电源状态。",
        "modules": [
            {
                "template": "ldo_regulator",
                "instance_name": "main_ldo",
                "parameters": {
                    "v_in": "5",
                    "v_out": "3.3",
                    "c_in": "10μF",
                    "c_out": "22μF",
                },
            },
            {
                "template": "led_indicator",
                "instance_name": "power_led",
                "parameters": {
                    "v_supply": "3.3",
                    "led_color": "green",
                    "led_current": "10",
                },
            },
        ],
        "connections": [
            {
                "from_module": "main_ldo",
                "from_net": "VOUT",
                "to_module": "power_led",
                "to_net": "VCC",
                "merged_net_name": "VOUT_3V3",
            },
            {
                "from_module": "main_ldo",
                "from_net": "GND",
                "to_module": "power_led",
                "to_net": "GND",
                "merged_net_name": "GND",
            },
        ],
        "notes": "AMS1117-3.3最大输入电压15V，压差约1.2V。输入电容建议靠近IC放置。LED限流电阻计算：(3.3V-2.2V)/10mA=110Ω，取标准值120Ω。",
    },
    "divider": {
        "design_name": "12V-3.3V分压采样",
        "description": "使用电阻分压器将12V输入分压为3.3V采样电压。",
        "modules": [
            {
                "template": "voltage_divider",
                "instance_name": "adc_divider",
                "parameters": {
                    "v_in": "12",
                    "v_out": "3.3",
                    "r_total": "20",
                },
            },
        ],
        "connections": [],
        "notes": "分压电阻功耗约7.2mW，适合ADC采样。建议在VMID点加100nF滤波电容。",
    },
    "rc_filter": {
        "design_name": "1kHz低通滤波器",
        "description": "一阶RC低通滤波器，截止频率1kHz。",
        "modules": [
            {
                "template": "rc_lowpass",
                "instance_name": "input_filter",
                "parameters": {
                    "f_cutoff": "1000",
                    "r_value": "10",
                },
            },
        ],
        "connections": [],
        "notes": "一阶RC滤波器衰减率为-20dB/decade。如需更陡峭的滚降，可级联多级。",
    },
    "ldo": {
        "design_name": "5V-3.3V稳压电源",
        "description": "使用AMS1117-3.3 LDO线性稳压器将5V转为3.3V。",
        "modules": [
            {
                "template": "ldo_regulator",
                "instance_name": "main_ldo",
                "parameters": {
                    "v_in": "5",
                    "v_out": "3.3",
                    "c_in": "10μF",
                    "c_out": "22μF",
                },
            },
        ],
        "connections": [],
        "notes": "AMS1117-3.3最大输入电压15V，压差约1.2V。输入电容建议靠近IC放置。",
    },
}


def call_llm_mock(user_input: str) -> dict[str, Any]:
    """Mock LLM调用，根据关键词返回预定义响应

    用于离线演示和测试。

    Args:
        user_input: 用户输入文本

    Returns:
        预定义的设计规格字典
    """
    text = user_input.lower()

    has_ldo = "ldo" in text or "稳压" in text
    has_led = "led" in text or "指示" in text

    # 优先匹配组合场景
    if has_ldo and has_led:
        return DEMO_RESPONSES["ldo_led"]
    elif has_ldo:
        return DEMO_RESPONSES["ldo"]
    elif "分压" in text or "divider" in text or "采样" in text:
        return DEMO_RESPONSES["divider"]
    elif "滤波" in text or "filter" in text or "rc" in text:
        return DEMO_RESPONSES["rc_filter"]
    elif has_led:
        # 单独LED
        return {
            "design_name": "LED电源指示灯",
            "description": "简单的LED指示灯电路。",
            "modules": [
                {
                    "template": "led_indicator",
                    "instance_name": "indicator",
                    "parameters": {
                        "v_supply": "3.3",
                        "led_color": "green",
                        "led_current": "10",
                    },
                },
            ],
            "connections": [],
            "notes": "绿色LED正向压降约2.2V。",
        }
    else:
        # 默认返回LDO+LED组合
        return DEMO_RESPONSES["ldo_led"]
