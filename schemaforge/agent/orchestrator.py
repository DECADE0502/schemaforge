"""SchemaForge AI 编排器

控制 AI 多轮对话循环（原生 function calling 版本）：
1. 发送 messages + tools 定义给 LLM
2. 如果 LLM 返回 tool_calls → 执行工具 → 把结果作为 tool message 追加 → 循环
3. 如果 LLM 返回纯文本 → 解析为 AgentStep 返回

核心原则：控制器掌握循环，AI 不能直接控制状态持久化。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable

from schemaforge.agent.protocol import (
    AgentAction,
    AgentStep,
)
from schemaforge.agent.tool_registry import ToolRegistry
from schemaforge.ai.client import (
    DEFAULT_MODEL,
    call_llm_with_tools,
    tool_defs_to_openai_tools,
)
from schemaforge.common.events import (
    EventType,
    LogEvent,
    WorkflowEvent,
)
from schemaforge.common.progress import ProgressTracker

logger = logging.getLogger(__name__)


class Orchestrator:
    """AI 编排器（原生 function calling）

    用法::

        orchestrator = Orchestrator(
            tool_registry=registry,
            system_prompt="你是器件库导入助手...",
            tracker=tracker,
        )

        # 启动对话
        step = orchestrator.run_turn("用户上传了 TPS54202.pdf")

        # step.action 决定后续：
        #   ASK_USER → GUI 显示问题，用户回答后 run_turn(answer)
        #   FINALIZE → 结束
    """

    MAX_TOOL_ROUNDS = 10  # 单轮最大工具调用轮次（防止死循环）

    def __init__(
        self,
        tool_registry: ToolRegistry,
        system_prompt: str,
        tracker: ProgressTracker | None = None,
        model: str = DEFAULT_MODEL,
        max_tokens: int = 98304,
        on_event: Callable[[WorkflowEvent], None] | None = None,
    ) -> None:
        self.registry = tool_registry
        self.system_prompt = system_prompt
        self.tracker = tracker
        self.model = model
        self.max_tokens = max_tokens
        self._on_event = on_event
        # 正式的 role-based 消息数组
        self.messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt}
        ]
        self._tool_round_count = 0

    # ----------------------------------------------------------
    # 事件 & 日志
    # ----------------------------------------------------------

    def _emit(self, event: WorkflowEvent) -> None:
        if self._on_event:
            self._on_event(event)

    def _log(self, message: str, level: str = "info") -> None:
        if self.tracker:
            self.tracker.log(message, level)

    # ----------------------------------------------------------
    # 核心循环
    # ----------------------------------------------------------

    def run_turn(self, user_input: str) -> AgentStep:
        """执行一轮 AI 对话。

        使用原生 function calling：
        1. 发送 messages + tools 定义给 LLM
        2. 如果 LLM 返回 tool_calls → 执行工具 → 把结果作为 tool message 追加 → 循环
        3. 如果 LLM 返回纯文本 → 解析为 AgentStep 返回

        Args:
            user_input: 用户消息或工具结果

        Returns:
            AgentStep — 需要 GUI 响应的动作
        """
        self.messages.append({"role": "user", "content": user_input})
        self._tool_round_count = 0

        # 把注册表中的工具转为 OpenAI function calling 格式
        openai_tools = tool_defs_to_openai_tools(
            self.registry.get_tool_descriptions()
        )
        tool_names = [t["function"]["name"] for t in openai_tools]
        logger.info("[Orchestrator] 注册工具: %s", tool_names)

        while True:
            self._log("正在调用 AI...")
            msg_count = len(self.messages)
            logger.info(
                "[Orchestrator] 调用 AI (round=%d, messages=%d, model=%s)",
                self._tool_round_count, msg_count, self.model,
            )
            try:
                response = call_llm_with_tools(
                    messages=self.messages,
                    tools=openai_tools if openai_tools else None,
                    model=self.model,
                    max_tokens=self.max_tokens,
                )
                logger.info("[Orchestrator] AI 响应到达")
            except Exception as exc:
                logger.exception("[Orchestrator] AI 调用异常")
                self._log(f"AI 调用失败: {exc}", "error")
                return AgentStep.fail(f"AI 调用失败: {exc}")

            choice = response.choices[0]
            assistant_msg = choice.message

            # --------------------------------------------------
            # 将 assistant 消息追加到历史（包含可能的 tool_calls）
            # --------------------------------------------------
            msg_dict: dict[str, Any] = {"role": "assistant"}
            if assistant_msg.content:
                msg_dict["content"] = assistant_msg.content
            if assistant_msg.tool_calls:
                msg_dict["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in assistant_msg.tool_calls
                ]
            self.messages.append(msg_dict)

            # --------------------------------------------------
            # Case 1: AI 发起 tool_calls → 执行并循环
            # --------------------------------------------------
            if assistant_msg.tool_calls:
                self._tool_round_count += 1
                tc_names = [tc.function.name for tc in assistant_msg.tool_calls]
                logger.info(
                    "[Orchestrator] AI 发起 tool_calls (round=%d): %s",
                    self._tool_round_count, tc_names,
                )
                if self._tool_round_count > self.MAX_TOOL_ROUNDS:
                    self._log("工具调用轮次超限，强制终止", "warning")
                    return AgentStep.fail("工具调用轮次超过上限")

                for tc in assistant_msg.tool_calls:
                    tool_name = tc.function.name
                    try:
                        arguments = json.loads(tc.function.arguments)
                    except json.JSONDecodeError:
                        arguments = {}

                    logger.info("[Orchestrator] 执行工具: %s(%s)", tool_name, list(arguments.keys()))
                    self._log(f"调用工具: {tool_name}")
                    self._emit(
                        LogEvent(
                            event_type=EventType.AI_TOOL_CALL,
                            message=f"调用 {tool_name}({arguments})",
                            source="orchestrator",
                        )
                    )

                    result = self.registry.execute(tool_name, arguments)
                    logger.info(
                        "[Orchestrator] 工具结果: %s → %s (data_keys=%s)",
                        tool_name,
                        "成功" if result.success else "失败",
                        list(result.data.keys()) if result.data else "N/A",
                    )

                    self._emit(
                        LogEvent(
                            event_type=EventType.AI_TOOL_RESULT,
                            message=(
                                f"{tool_name} → "
                                f"{'成功' if result.success else '失败'}"
                            ),
                            source="orchestrator",
                        )
                    )

                    # 追加 tool result 消息
                    self.messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": json.dumps(
                                result.to_dict(), ensure_ascii=False
                            ),
                        }
                    )

                continue  # 带着 tool results 继续下一轮 AI 调用

            # --------------------------------------------------
            # Case 2: AI 纯文本回复（无 tool_calls）
            # --------------------------------------------------
            content = assistant_msg.content or ""
            logger.info("[Orchestrator] AI 纯文本回复 (%d chars): %s", len(content), content[:200])
            self._log(f"AI 回复: {content[:100]}")

            # 尝试解析为结构化 AgentStep JSON
            try:
                data = json.loads(content)
                return AgentStep.model_validate(data)
            except Exception:
                # 纯文本回复 — 包装为 FINALIZE
                return AgentStep(
                    action=AgentAction.FINALIZE,
                    message=content,
                )

    # ----------------------------------------------------------
    # 上下文注入 & 重置
    # ----------------------------------------------------------

    def inject_context(self, role: str, content: str) -> None:
        """注入上下文消息（不触发 AI 调用）。

        用于在对话开始前注入背景信息、工具描述等。
        role 通常为 "system" 或 "user"。
        """
        self.messages.append({"role": role, "content": content})

    def reset(self) -> None:
        """重置对话历史。

        保留 system prompt 作为首条消息。
        """
        self.messages = [{"role": "system", "content": self.system_prompt}]
        self._tool_round_count = 0
