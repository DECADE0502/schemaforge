"""SchemaForge AI 编排器

控制 AI 多轮对话循环：
1. 构建消息 → 调用 AI → 解析 AgentStep
2. 根据 action 执行工具 / 提问用户 / 展示草稿
3. 循环直到 finalize 或 fail

核心原则：控制器掌握循环，AI 不能直接控制状态持久化。
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Callable

from schemaforge.agent.protocol import (
    AgentAction,
    AgentStep,
    ToolCallRequest,
)
from schemaforge.agent.tool_registry import ToolRegistry
from schemaforge.ai.client import call_llm, DEFAULT_MODEL
from schemaforge.common.events import (
    EventType,
    LogEvent,
    WorkflowEvent,
)
from schemaforge.common.progress import ProgressTracker


# AgentStep 解析的 JSON 格式说明（注入 system prompt）
AGENT_STEP_SCHEMA_HINT = """\
你必须严格按以下 JSON 格式输出，不要输出其他内容：
{
  "action": "call_tools" | "ask_user" | "present_draft" | "apply_patch" | "finalize" | "fail",
  "message": "给用户看的中文说明",
  "tool_calls": [{"tool_name": "...", "arguments": {...}, "call_id": "..."}],
  "questions": [{"question_id": "...", "text": "...", "field_path": "...", "answer_type": "text|choice|confirm|number", "choices": [...], "required": true, "default": "", "evidence": "..."}],
  "proposal": {},
  "patch_ops": [{"op": "set|add|remove|replace", "path": "...", "value": ..., "reason": "..."}],
  "checks": [{"rule_id": "...", "severity": "info|warning|error", "message": "...", "suggestion": "...", "evidence_refs": [...]}],
  "next_state": ""
}
只输出 JSON，不要包含 markdown 代码块标记。
"""


def _parse_agent_step(raw_text: str) -> AgentStep:
    """从 AI 原始输出解析 AgentStep

    尝试策略：
    1. 直接 JSON 解析
    2. 去掉 markdown 代码块后解析
    3. 提取第一个 { 到最后一个 } 之间的内容
    4. 全部失败则返回 FAIL step
    """
    text = raw_text.strip()

    # 策略1: 直接解析
    try:
        data = json.loads(text)
        return AgentStep.model_validate(data)
    except Exception:
        pass

    # 策略2: 去掉 markdown 代码块
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
                data = json.loads("\n".join(json_lines))
                return AgentStep.model_validate(data)
            except Exception:
                pass

    # 策略3: 找 { ... }
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        try:
            data = json.loads(text[start:end + 1])
            return AgentStep.model_validate(data)
        except Exception:
            pass

    # 全部失败
    return AgentStep.fail(f"AI 输出解析失败，原始内容: {text[:200]}")


class Orchestrator:
    """AI 编排器

    用法::

        orchestrator = Orchestrator(
            tool_registry=registry,
            system_prompt="你是器件库导入助手...",
            tracker=tracker,
        )

        # 启动对话
        step = orchestrator.run_turn("用户上传了 TPS54202.pdf")

        # step.action 决定后续：
        #   CALL_TOOLS → 控制器已自动执行，继续 run_turn
        #   ASK_USER → GUI 显示问题，用户回答后 run_turn(answer)
        #   FINALIZE → 结束
    """

    MAX_TOOL_ROUNDS = 10  # 单轮最大工具调用次数（防止死循环）

    def __init__(
        self,
        tool_registry: ToolRegistry,
        system_prompt: str,
        tracker: ProgressTracker | None = None,
        model: str = DEFAULT_MODEL,
        on_event: Callable[[WorkflowEvent], None] | None = None,
    ) -> None:
        self.registry = tool_registry
        self.system_prompt = system_prompt + "\n\n" + AGENT_STEP_SCHEMA_HINT
        self.tracker = tracker
        self.model = model
        self._on_event = on_event
        self.messages: list[dict[str, str]] = []
        self._tool_round_count = 0

    def _emit(self, event: WorkflowEvent) -> None:
        if self._on_event:
            self._on_event(event)

    def _log(self, message: str, level: str = "info") -> None:
        if self.tracker:
            self.tracker.log(message, level)

    def run_turn(self, user_input: str) -> AgentStep:
        """执行一轮 AI 对话

        如果 AI 返回 CALL_TOOLS，自动执行工具并继续对话，
        直到 AI 返回非 CALL_TOOLS 的动作。

        Args:
            user_input: 用户消息或工具结果

        Returns:
            AgentStep — 需要 GUI 响应的动作
        """
        self.messages.append({"role": "user", "content": user_input})
        self._tool_round_count = 0

        while True:
            # 调用 AI
            self._log("正在调用 AI...")
            try:
                raw_response = call_llm(
                    system_prompt=self.system_prompt,
                    user_message=self._build_user_context(),
                    model=self.model,
                )
            except Exception as exc:
                self._log(f"AI 调用失败: {exc}", "error")
                return AgentStep.fail(f"AI 调用失败: {exc}")

            self.messages.append({"role": "assistant", "content": raw_response})

            # 解析 AgentStep
            step = _parse_agent_step(raw_response)
            self._log(f"AI 动作: {step.action.value}")

            if step.action == AgentAction.CALL_TOOLS:
                # 自动执行工具调用
                self._tool_round_count += 1
                if self._tool_round_count > self.MAX_TOOL_ROUNDS:
                    self._log("工具调用轮次超限，强制终止", "warning")
                    return AgentStep.fail("工具调用轮次超过上限")

                tool_results = self._execute_tool_calls(step.tool_calls)
                # 将工具结果追加到消息
                result_text = json.dumps(tool_results, ensure_ascii=False, indent=2)
                self.messages.append({
                    "role": "user",
                    "content": f"工具执行结果:\n{result_text}",
                })
                continue  # 继续下一轮 AI 调用

            # 非 CALL_TOOLS 动作，返回给调用者处理
            return step

    def _build_user_context(self) -> str:
        """构建发给 AI 的完整上下文"""
        # 只取最近的消息（控制上下文长度）
        recent = self.messages[-20:]
        parts: list[str] = []
        for msg in recent:
            role = msg["role"]
            content = msg["content"]
            if role == "user":
                parts.append(f"[用户] {content}")
            elif role == "assistant":
                parts.append(f"[AI] {content}")
        return "\n\n".join(parts)

    def _execute_tool_calls(
        self,
        tool_calls: list[ToolCallRequest],
    ) -> list[dict[str, Any]]:
        """执行一批工具调用"""
        results: list[dict[str, Any]] = []
        for call in tool_calls:
            self._log(f"调用工具: {call.tool_name}")
            self._emit(LogEvent(
                event_type=EventType.AI_TOOL_CALL,
                message=f"调用 {call.tool_name}({call.arguments})",
                source="orchestrator",
            ))

            result = self.registry.execute(call.tool_name, call.arguments)

            self._emit(LogEvent(
                event_type=EventType.AI_TOOL_RESULT,
                message=f"{call.tool_name} → {'成功' if result.success else '失败'}",
                source="orchestrator",
            ))

            results.append({
                "call_id": call.call_id or uuid.uuid4().hex[:8],
                "tool_name": call.tool_name,
                **result.to_dict(),
            })

        return results

    def inject_context(self, role: str, content: str) -> None:
        """注入上下文消息（不触发 AI 调用）

        用于在对话开始前注入背景信息、工具描述等。
        """
        self.messages.append({"role": role, "content": content})

    def reset(self) -> None:
        """重置对话历史"""
        self.messages.clear()
        self._tool_round_count = 0
