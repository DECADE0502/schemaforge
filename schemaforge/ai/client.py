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
