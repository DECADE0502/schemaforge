"""SchemaForge LLM API客户端

封装OpenAI兼容接口调用，默认使用DashScope的kimi-k2.5模型。
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from openai import OpenAI

logger = logging.getLogger(__name__)

# === 默认配置（写死kimi-k2.5） ===
DEFAULT_API_KEY = "sk-sp-396701e02c95411783e01557524e4366"
DEFAULT_BASE_URL = "https://coding.dashscope.aliyuncs.com/v1"
DEFAULT_MODEL = "kimi-k2.5"


# 模型 → base_url 路由
# coding.dashscope 端点支持: kimi-k2.5, qwen3-coder-plus
# 其他模型如需支持，需配置 DASHSCOPE_API_KEY 环境变量并走通用端点
_MODEL_BASE_URL: dict[str, str] = {
    "kimi-k2.5": "https://coding.dashscope.aliyuncs.com/v1",
    "qwen3-coder-plus": "https://coding.dashscope.aliyuncs.com/v1",
}


def get_base_url_for_model(model: str) -> str:
    """根据模型名返回对应的 API base_url。"""
    return _MODEL_BASE_URL.get(model, DEFAULT_BASE_URL)


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


# ---------------------------------------------------------------------------
# Native function calling support
# ---------------------------------------------------------------------------

def call_llm_with_tools(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    model: str = DEFAULT_MODEL,
    temperature: float = 0.3,
    max_tokens: int = 4096,
    api_key: str | None = None,
    base_url: str | None = None,
) -> Any:
    """调用 LLM，支持原生 function calling。

    与 call_llm 不同：
    - 接受完整 messages 数组（支持多轮 + tool results）
    - 接受 tools 定义（OpenAI function calling schema）
    - 返回完整 ChatCompletion 对象（调用方检查 tool_calls）

    Args:
        messages: 完整消息数组 [{"role": "system", "content": ...}, ...]
        tools: OpenAI function calling tools 定义列表
        model: 模型名称
        temperature: 温度
        max_tokens: 最大 token
        api_key: API密钥
        base_url: API基地址

    Returns:
        openai.types.chat.ChatCompletion
    """
    # 自动路由 base_url：调用方未指定时按模型名查路由表
    effective_base_url = base_url or get_base_url_for_model(model)
    client = get_client(api_key, effective_base_url)

    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if tools is not None:
        kwargs["tools"] = tools

    tool_count = len(tools) if tools else 0
    logger.info(
        "[LLM] 请求 model=%s, messages=%d, tools=%d, max_tokens=%d, base_url=%s",
        model, len(messages), tool_count, max_tokens, effective_base_url,
    )
    response = client.chat.completions.create(**kwargs)
    # 记录响应摘要
    choice = response.choices[0] if response.choices else None
    if choice:
        has_tc = bool(choice.message.tool_calls)
        content_len = len(choice.message.content or "")
        tc_count = len(choice.message.tool_calls) if has_tc else 0
        logger.info(
            "[LLM] 响应: finish_reason=%s, has_tool_calls=%s (count=%d), content_len=%d",
            choice.finish_reason, has_tc, tc_count, content_len,
        )
    return response


# ---------------------------------------------------------------------------
# Vision API support (带图片的 AI 调用)
# ---------------------------------------------------------------------------


def call_llm_vision(
    system_prompt: str,
    user_text: str,
    image_base64: str,
    model: str = DEFAULT_MODEL,
    temperature: float = 0.3,
    max_tokens: int = 4096,
    api_key: str | None = None,
    base_url: str | None = None,
) -> str:
    """调用 LLM vision API — 同时传入文本和 base64 图片。

    Args:
        system_prompt: 系统 prompt
        user_text: 用户文本消息
        image_base64: PNG 图片的 base64 编码（不含 data:image 前缀）
        model: 模型名称
        temperature: 温度
        max_tokens: 最大 token
        api_key: API密钥
        base_url: API基地址

    Returns:
        LLM 文本回复
    """
    effective_base_url = base_url or get_base_url_for_model(model)
    client = get_client(api_key, effective_base_url)

    user_content: list[dict[str, Any]] = [
        {"type": "text", "text": user_text},
        {
            "type": "image_url",
            "image_url": {
                "url": f"data:image/png;base64,{image_base64}",
            },
        },
    ]

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        temperature=temperature,
        max_tokens=max_tokens,
    )

    return response.choices[0].message.content or ""


def tool_defs_to_openai_tools(
    tool_descriptions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """把 ToolRegistry.get_tool_descriptions() 的输出转为 OpenAI tools 格式。

    Input:  [{"name": "foo", "description": "...", "parameters": {...}}]
    Output: [{"type": "function", "function": {"name": "foo", "description": "...", "parameters": {...}}}]
    """
    openai_tools: list[dict[str, Any]] = []
    for desc in tool_descriptions:
        openai_tools.append({
            "type": "function",
            "function": {
                "name": desc["name"],
                "description": desc.get("description", ""),
                "parameters": desc.get("parameters", {}),
            },
        })
    return openai_tools
