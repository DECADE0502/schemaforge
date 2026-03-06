"""AI 对话面板 (NiceGUI版)

支持多轮对话:
- 普通文本消息 (user / assistant / system)
- AI 提问待回答卡片 (text / choice / confirm)
- 实时滚动到最新消息

所有 UI 文案为中文。
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable

from nicegui import ui

ui.add_css("""
.sf-bubble-user {
    background: #094771;
    border: 1px solid #0a5a9e;
    border-radius: 8px;
    padding: 8px;
    margin: 4px;
}
.sf-bubble-assistant {
    background: #2d2d30;
    border: 1px solid #3e3e42;
    border-radius: 8px;
    padding: 8px;
    margin: 4px;
}
.sf-bubble-system {
    background: #3a3a1a;
    border: 1px solid #5a5a2a;
    border-radius: 8px;
    padding: 8px;
    margin: 4px;
}
.sf-question-card {
    background: #3a2e00;
    border: 1px solid #7a6000;
    border-radius: 8px;
    padding: 10px;
    margin: 4px;
}
""")

_ROLE_MAP = {
    "user": "👤 用户",
    "assistant": "🤖 AI",
    "system": "⚙️ 系统",
    "tool": "🔧 工具",
}

_BUBBLE_CLASS = {
    "user": "sf-bubble-user",
    "assistant": "sf-bubble-assistant",
    "system": "sf-bubble-system",
}


def build_chat_panel() -> dict:
    """构建 AI 对话面板。

    Returns:
        dict with keys:
            - add_message: callable(role, content)
            - add_question: callable(question_id, text, answer_type, choices, default, evidence)
            - clear: callable()
            - on_message_sent: callable to set message-sent callback
            - on_question_answered: callable to set question-answered callback
    """

    _on_message_sent_cb: Callable | None = None
    _on_question_answered_cb: Callable | None = None

    with ui.column().classes("w-full h-full gap-0 p-0"):
        ui.label("💬 AI 对话").classes("text-lg font-bold text-blue-300 px-3 pt-3 pb-1")

        msg_scroll = (
            ui.scroll_area()
            .classes("flex-1 w-full")
            .style("min-height: 200px; max-height: 480px;")
        )

        with msg_scroll:
            msg_col = ui.column().classes("w-full gap-1 p-2")

        with ui.row().classes("w-full gap-2 p-2 items-end"):
            input_area = (
                ui.textarea(placeholder="输入消息...")
                .classes("flex-1")
                .props("rows=2 autogrow")
            )
            send_btn = ui.button("发送", icon="send").classes(
                "bg-blue-700 text-white self-end"
            )

    def _add_bubble(role: str, content: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        bubble_cls = _BUBBLE_CLASS.get(role, "sf-bubble-system")
        role_label = _ROLE_MAP.get(role, role)
        with msg_col:
            with ui.element("div").classes(bubble_cls + " w-full"):
                with ui.row().classes("w-full items-center gap-2 mb-1"):
                    ui.label(role_label).classes("text-xs font-semibold text-gray-300")
                    ui.label(ts).classes("text-xs text-gray-500 ml-auto")
                ui.label(content).classes(
                    "text-sm text-gray-100 break-words whitespace-pre-wrap"
                )
        msg_scroll.scroll_to(percent=1.0)

    def add_message(role: str, content: str) -> None:
        _add_bubble(role, content)

    def add_question(
        question_id: str,
        text: str,
        answer_type: str = "text",
        choices: list[str] | None = None,
        default: str = "",
        evidence: str = "",
    ) -> None:
        with msg_col:
            with ui.element("div").classes("sf-question-card w-full"):
                ui.label("❓ AI 需要确认").classes(
                    "text-xs font-bold text-yellow-300 mb-1"
                )
                ui.label(text).classes("text-sm text-gray-200 break-words mb-1")
                if evidence:
                    ui.label(f"💡 依据: {evidence}").classes(
                        "text-xs text-gray-400 mb-1"
                    )

                with ui.row().classes("w-full gap-2 items-center"):
                    if answer_type == "choice" and choices:
                        sel = ui.select(choices, value=default or choices[0]).classes(
                            "flex-1"
                        )

                        def _submit_choice(qid=question_id, widget=sel):
                            val = widget.value
                            if _on_question_answered_cb:
                                _on_question_answered_cb(qid, val)
                            ui.notify(f"已提交: {val}", type="positive")

                        ui.button("提交", icon="check").classes(
                            "bg-green-700 text-white text-xs"
                        ).on_click(_submit_choice)

                    elif answer_type == "confirm":

                        def _submit_yes(qid=question_id):
                            if _on_question_answered_cb:
                                _on_question_answered_cb(qid, "是")
                            ui.notify("已提交: 是", type="positive")

                        def _submit_no(qid=question_id):
                            if _on_question_answered_cb:
                                _on_question_answered_cb(qid, "否")
                            ui.notify("已提交: 否", type="info")

                        ui.button("是", icon="check").classes(
                            "bg-green-700 text-white text-xs"
                        ).on_click(_submit_yes)
                        ui.button("否", icon="close").classes(
                            "bg-red-700 text-white text-xs"
                        ).on_click(_submit_no)

                    else:
                        inp = ui.input(
                            placeholder="请输入...",
                            value=default,
                        ).classes("flex-1")

                        def _submit_text(qid=question_id, widget=inp):
                            val = widget.value.strip()
                            if val and _on_question_answered_cb:
                                _on_question_answered_cb(qid, val)
                                ui.notify(f"已提交: {val}", type="positive")

                        ui.button("提交", icon="check").classes(
                            "bg-green-700 text-white text-xs"
                        ).on_click(_submit_text)

        msg_scroll.scroll_to(percent=1.0)

    def clear() -> None:
        msg_col.clear()

    def _on_send():
        text = input_area.value.strip()
        if not text:
            return
        add_message("user", text)
        if _on_message_sent_cb:
            _on_message_sent_cb(text)
        input_area.set_value("")

    send_btn.on_click(_on_send)

    def set_on_message_sent(cb: Callable) -> None:
        nonlocal _on_message_sent_cb
        _on_message_sent_cb = cb

    def set_on_question_answered(cb: Callable) -> None:
        nonlocal _on_question_answered_cb
        _on_question_answered_cb = cb

    return {
        "add_message": add_message,
        "add_question": add_question,
        "clear": clear,
        "on_message_sent": set_on_message_sent,
        "on_question_answered": set_on_question_answered,
    }
