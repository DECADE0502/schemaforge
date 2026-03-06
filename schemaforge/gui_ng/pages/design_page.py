"""原理图设计页面 (NiceGUI版, Tab 1)

对应 PySide6 MainWindow 的设计标签页逻辑:
- 进度头部 (阶段 + 消息 + 进度条 + 取消)
- 左侧输入面板
- 中央 SVG 预览
- 右侧 AI 对话面板
- 底部结果标签 (BOM / SPICE / ERC / 设计概要 / 运行日志)
"""

from __future__ import annotations

import asyncio
import datetime
import os
from pathlib import Path
from typing import Any

from nicegui import ui

from schemaforge.gui_ng.widgets.chat_panel import build_chat_panel


PRESET_INPUTS: dict[str, str] = {
    "voltage_divider": "12V到3.3V的分压采样电路",
    "ldo_regulator": "5V转3.3V稳压电路",
    "led_indicator": "3.3V绿色LED电源指示灯",
    "rc_lowpass": "1kHz低通滤波器",
}

_STEP_NAMES = ["AI调用", "验证", "实例化", "ERC检查", "渲染", "导出"]
_STEP_THRESHOLDS = [5, 25, 40, 55, 70, 85]


def build_design_page() -> None:
    """构建原理图设计页面 (Tab 1).

    在调用方的 ui.tab_panel 或容器上下文中调用此函数。
    """
    from schemaforge.core.engine import EngineResult, SchemaForgeEngine
    from schemaforge.core.templates import get_template, list_templates
    from schemaforge.workflows.design_session import DesignSession, DesignSessionResult

    last_result: EngineResult | None = None
    last_session_result: DesignSessionResult | None = None
    _cancelled = False

    with ui.column().classes("w-full h-full gap-0 p-0"):
        progress_stage_label = ui.label("就绪").classes(
            "text-xs text-gray-400 px-3 pt-2"
        )

        with ui.row().classes("w-full items-center gap-3 px-3 pb-1"):
            progress_msg = ui.label("输入电路需求后点击「生成原理图」").classes(
                "text-sm text-gray-300 flex-1"
            )
            progress_bar = (
                ui.linear_progress(value=0)
                .classes("flex-1")
                .props("color=blue instant-feedback")
            )
            progress_bar.visible = False

            def _on_cancel():
                nonlocal _cancelled
                _cancelled = True
                ui.notify("已请求取消", type="warning")

            cancel_btn = (
                ui.button("取消", icon="stop")
                .classes("bg-red-700 text-white text-xs")
                .props("size=sm")
            )
            cancel_btn.on_click(_on_cancel)
            cancel_btn.visible = False

        with (
            ui.splitter(value=25)
            .classes("w-full flex-1")
            .style("min-height: 500px;") as outer_splitter
        ):
            with outer_splitter.before:
                with ui.column().classes("w-full h-full gap-2 p-3"):
                    ui.label("电路需求输入").classes(
                        "text-base font-bold text-blue-300"
                    )

                    input_area = (
                        ui.textarea(
                            placeholder=(
                                "请输入电路需求，例如：\n"
                                "• 5V转3.3V稳压电路，带LED指示灯\n"
                                "• 12V到3.3V的分压采样电路\n"
                                "• 1kHz低通滤波器\n"
                                "• LED电源指示灯"
                            )
                        )
                        .classes("w-full")
                        .props("rows=6 autogrow")
                    )

                    ui.label("快捷模板").classes("text-xs text-gray-400 mt-1")
                    template_options = ["-- 自定义输入 --"]
                    template_keys = [""]
                    for name in list_templates():
                        t = get_template(name)
                        if t:
                            template_options.append(f"{t.display_name} ({t.name})")
                            template_keys.append(name)

                    template_select = ui.select(
                        template_options, value="-- 自定义输入 --"
                    ).classes("w-full")

                    def _on_template_change(e):
                        idx = (
                            template_options.index(e.value)
                            if e.value in template_options
                            else 0
                        )
                        key = template_keys[idx] if idx < len(template_keys) else ""
                        if key and key in PRESET_INPUTS:
                            input_area.set_value(PRESET_INPUTS[key])

                    template_select.on("update:model-value", _on_template_change)

                    ui.label("运行模式").classes("text-xs text-gray-400 mt-1")
                    mode_select = ui.select(
                        {
                            "mock": "离线Mock（无需网络）",
                            "online": "在线LLM（kimi-k2.5）",
                        },
                        value="mock",
                    ).classes("w-full")

                    ui.label("后端链路").classes("text-xs text-gray-400")
                    chain_select = ui.select(
                        {
                            "classic": "经典链（模板驱动）",
                            "new": "新主链（库驱动+IR+审查）",
                        },
                        value="classic",
                    ).classes("w-full")

                    with ui.row().classes("w-full gap-2 mt-2"):
                        generate_btn = ui.button("生成原理图", icon="bolt").classes(
                            "flex-1 bg-blue-700 text-white font-bold"
                        )
                        demo_btn = ui.button("运行Demo", icon="play_arrow").classes(
                            "flex-1 bg-indigo-700 text-white"
                        )

                    quick_status = ui.label("").classes(
                        "text-xs text-gray-400 break-words"
                    )

            with outer_splitter.after:
                with ui.splitter(value=60).classes("w-full h-full") as inner_splitter:
                    with inner_splitter.before:
                        with ui.column().classes("w-full h-full gap-0 p-2"):
                            ui.label("原理图预览").classes("text-sm text-gray-400 mb-1")
                            svg_empty = ui.label(
                                "原理图预览区\n\n输入电路需求后点击「生成」"
                            ).classes(
                                "text-center text-gray-500 whitespace-pre-line m-auto"
                            )
                            svg_container = (
                                ui.scroll_area()
                                .classes("w-full flex-1")
                                .style("min-height: 300px;")
                            )
                            svg_container.visible = False
                            with svg_container:
                                svg_html = ui.html("").style(
                                    "width: 100%; height: 100%;"
                                )

                    with inner_splitter.after:
                        chat = build_chat_panel()

        with ui.tabs().classes("w-full bg-gray-800") as result_tabs:
            tab_bom = ui.tab("BOM 清单")
            tab_spice = ui.tab("SPICE 网表")
            tab_erc = ui.tab("ERC 检查")
            tab_summary = ui.tab("设计概要")
            tab_log = ui.tab("运行日志")

        with (
            ui.tab_panels(result_tabs, value=tab_bom)
            .classes("w-full")
            .style("height: 180px; overflow-y: auto;")
        ):
            with ui.tab_panel(tab_bom):
                bom_text = (
                    ui.textarea("")
                    .props("readonly")
                    .classes("w-full font-mono text-xs")
                    .style("height: 140px;")
                )
            with ui.tab_panel(tab_spice):
                spice_text = (
                    ui.textarea("")
                    .props("readonly")
                    .classes("w-full font-mono text-xs")
                    .style("height: 140px;")
                )
            with ui.tab_panel(tab_erc):
                erc_text = (
                    ui.textarea("")
                    .props("readonly")
                    .classes("w-full font-mono text-xs")
                    .style("height: 140px;")
                )
            with ui.tab_panel(tab_summary):
                summary_text = (
                    ui.textarea("")
                    .props("readonly")
                    .classes("w-full font-mono text-xs")
                    .style("height: 140px;")
                )
            with ui.tab_panel(tab_log):
                with ui.column().classes("w-full gap-1"):
                    with ui.row().classes("w-full gap-2 flex-wrap"):
                        step_labels: list[ui.label] = []
                        for sname in _STEP_NAMES:
                            lbl = ui.label(f"○ {sname}").classes(
                                "text-xs text-gray-400 border border-gray-600 px-2 py-0.5 rounded"
                            )
                            step_labels.append(lbl)
                    log_textarea = (
                        ui.textarea("")
                        .props("readonly")
                        .classes("w-full font-mono text-xs")
                        .style("height: 110px;")
                    )

    def _append_log(message: str) -> None:
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        existing = log_textarea.value or ""
        lines = existing.split("\n") if existing else []
        lines.append(f"[{ts}] {message}")
        if len(lines) > 500:
            lines = lines[-500:]
        log_textarea.set_value("\n".join(lines))

    def _update_steps(percentage: int) -> None:
        for i, threshold in enumerate(_STEP_THRESHOLDS):
            lbl = step_labels[i]
            if percentage >= threshold:
                if percentage > threshold + 10 or percentage >= 100:
                    lbl.set_text(f"✓ {_STEP_NAMES[i]}")
                    lbl.classes(
                        remove="text-gray-400 text-blue-400",
                        add="text-green-400",
                    )
                else:
                    lbl.set_text(f"▸ {_STEP_NAMES[i]}")
                    lbl.classes(
                        remove="text-gray-400 text-green-400",
                        add="text-blue-400",
                    )
            else:
                lbl.set_text(f"○ {_STEP_NAMES[i]}")
                lbl.classes(
                    remove="text-green-400 text-blue-400",
                    add="text-gray-400",
                )

    def _clear_steps() -> None:
        for i, lbl in enumerate(step_labels):
            lbl.set_text(f"○ {_STEP_NAMES[i]}")
            lbl.classes(
                remove="text-green-400 text-blue-400",
                add="text-gray-400",
            )

    def _load_svgs(svg_paths: list[str]) -> None:
        combined_svg = ""
        for path in svg_paths:
            if os.path.exists(path):
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        combined_svg += f.read() + "\n"
                except Exception:
                    pass
        if combined_svg:
            svg_empty.visible = False
            svg_container.visible = True
            svg_html.set_content(combined_svg)
        else:
            svg_empty.visible = True
            svg_container.visible = False

    def _handle_engine_result(result: Any) -> None:
        nonlocal last_result
        last_result = result

        generate_btn.enable()
        demo_btn.enable()
        progress_bar.visible = False
        cancel_btn.visible = False

        if not result.success:
            progress_msg.set_text(f"失败 — 阶段: {result.stage}")
            quick_status.set_text(f"错误: {result.error}")
            quick_status.classes(
                remove="text-gray-400 text-green-400", add="text-red-400"
            )
            _append_log(f"❌ 失败: {result.error}")
            chat["add_message"]("system", f"生成失败: {result.error}")
            ui.notify(f"生成失败: {result.stage}\n{result.error}", type="negative")
            return

        progress_msg.set_text(f"完成 — {result.design_name}")
        quick_status.set_text(f"生成成功 | SVG: {len(result.svg_paths)} 个")
        quick_status.classes(remove="text-gray-400 text-red-400", add="text-green-400")
        _append_log(f"✅ 完成: {result.design_name}")
        chat["add_message"](
            "assistant",
            f"原理图已生成：{result.design_name}\n"
            f"模块: {len(result.circuits)} 个 | SVG: {len(result.svg_paths)} 个",
        )

        _load_svgs(result.svg_paths)
        bom_text.set_value(result.bom_text.strip())
        spice_text.set_value(result.spice_text.strip())

        if result.erc_errors:
            errors = [e for e in result.erc_errors if e.severity.value == "error"]
            warnings = [e for e in result.erc_errors if e.severity.value != "error"]
            erc_lines = []
            if errors:
                erc_lines.append(f"=== 错误 ({len(errors)}) ===\n")
                for e in errors:
                    erc_lines.append(f"  [X] [{e.rule}] {e.message}")
            if warnings:
                erc_lines.append(f"\n=== 警告 ({len(warnings)}) ===\n")
                for e in warnings:
                    erc_lines.append(f"  [!] [{e.rule}] {e.message}")
            erc_text.set_value("\n".join(erc_lines))
            quick_status.set_text(
                f"生成成功 | SVG: {len(result.svg_paths)} 个 | "
                f"ERC: {len(errors)} 错误, {len(warnings)} 警告"
            )
        else:
            erc_text.set_value("ERC 检查全部通过，无错误无警告。")

        summary_lines = [
            f"设计名称: {result.design_name}",
            f"描述: {result.description}",
            "",
            f"模块数量: {len(result.circuits)}",
            f"SVG文件: {len(result.svg_paths)} 个",
        ]
        if result.svg_paths:
            summary_lines += ["", "SVG文件路径:"] + [f"  {p}" for p in result.svg_paths]
        if result.notes:
            summary_lines += ["", f"设计备注:\n  {result.notes}"]
        summary_text.set_value("\n".join(summary_lines))

        result_tabs.set_value(tab_bom)

    def _handle_session_result(result: Any) -> None:
        nonlocal last_session_result
        last_session_result = result

        generate_btn.enable()
        demo_btn.enable()
        progress_bar.visible = False
        cancel_btn.visible = False

        if not result.success:
            progress_msg.set_text(f"失败 — 阶段: {result.stage}")
            quick_status.set_text(f"错误: {result.error}")
            quick_status.classes(
                remove="text-gray-400 text-green-400", add="text-red-400"
            )
            _append_log(f"❌ 失败: {result.error}")
            chat["add_message"]("system", f"生成失败: {result.error}")
            ui.notify(f"生成失败: {result.stage}\n{result.error}", type="negative")
            return

        design_name = result.plan.name if result.plan else "未命名"
        progress_msg.set_text(f"完成 — {design_name}")
        quick_status.set_text(f"生成成功 | SVG: {len(result.svg_paths)} 个")
        quick_status.classes(remove="text-gray-400 text-red-400", add="text-green-400")
        _append_log(f"✅ 完成: {design_name}")
        chat["add_message"](
            "assistant",
            f"原理图已生成：{design_name}\n"
            f"模块: {len(result.modules)} 个 | SVG: {len(result.svg_paths)} 个",
        )

        _load_svgs(result.svg_paths)
        bom_text.set_value(result.bom_text.strip())
        spice_text.set_value(result.spice_text.strip())

        if result.design_review is not None:
            review = result.design_review
            blocking = [i for i in review.issues if i.severity.value == "blocking"]
            review_warnings = [
                i for i in review.issues if i.severity.value != "blocking"
            ]
            erc_lines = ["=== 设计审查 (新主链) ===\n"]
            if blocking:
                erc_lines.append(f"阻断: {len(blocking)}")
                for b in blocking:
                    erc_lines.append(f"  [X] [{b.rule_id}] {b.message}")
            if review_warnings:
                erc_lines.append(f"\n警告: {len(review_warnings)}")
                for w in review_warnings:
                    erc_lines.append(f"  [!] [{w.rule_id}] {w.message}")
            erc_text.set_value("\n".join(erc_lines))
            quick_status.set_text(
                f"生成成功 | SVG: {len(result.svg_paths)} 个 | "
                f"审查: {len(blocking)} 阻断, {len(review_warnings)} 警告"
            )
        else:
            erc_text.set_value("设计审查通过，无问题。")

        summary_lines = [
            f"设计名称: {design_name}",
            "后端: 新主链（库驱动+IR+审查）",
            f"模块数量: {len(result.modules)}",
            f"SVG文件: {len(result.svg_paths)} 个",
        ]
        for mr in result.modules:
            d = mr.to_dict()
            status = "OK" if d.get("review_passed") else "待审"
            summary_lines.append(
                f"  {d['role']}: {d['device'] or '未匹配'} "
                f"({d.get('solver_candidates', 0)} 候选, {status})"
            )
        if result.reference_design is not None:
            summary_lines.append(f"\n参考设计: {result.reference_design.name}")
        if result.svg_paths:
            summary_lines += ["\nSVG文件路径:"] + [f"  {p}" for p in result.svg_paths]
        summary_text.set_value("\n".join(summary_lines))

        result_tabs.set_value(tab_bom)

    async def on_generate():
        nonlocal _cancelled
        user_input = input_area.value.strip()
        if not user_input:
            ui.notify("请先输入电路需求！", type="warning")
            return

        _cancelled = False
        generate_btn.disable()
        demo_btn.disable()
        progress_bar.visible = True
        progress_bar.set_value(0)
        cancel_btn.visible = True
        progress_msg.set_text(f"处理中... {user_input[:40]}")
        quick_status.set_text("正在调用引擎...")
        quick_status.classes(remove="text-red-400 text-green-400", add="text-gray-400")
        log_textarea.set_value("")
        _clear_steps()
        _append_log(f"开始处理: {user_input[:60]}")
        chat["add_message"]("user", user_input)

        def _on_progress(message: str, percentage: int) -> None:
            _append_log(message)
            if percentage >= 0:
                _update_steps(percentage)
                progress_bar.set_value(percentage / 100)
                progress_stage_label.set_text(message)

        try:
            if chain_select.value == "new":
                _append_log("后端: 新主链（库驱动+IR+审查）")
                session = DesignSession(
                    store_dir=Path("schemaforge/store"),
                    use_mock=(mode_select.value == "mock"),
                )
                result = await asyncio.to_thread(session.run, user_input)
                _handle_session_result(result)
            else:
                _append_log("后端: 经典链（模板驱动）")
                engine = SchemaForgeEngine(use_mock=(mode_select.value == "mock"))
                result = await asyncio.to_thread(
                    engine.process,
                    user_input,
                    progress_callback=_on_progress,
                )
                _handle_engine_result(result)

        except Exception as exc:
            generate_btn.enable()
            demo_btn.enable()
            progress_bar.visible = False
            cancel_btn.visible = False
            quick_status.set_text(f"异常: {exc}")
            quick_status.classes(
                remove="text-gray-400 text-green-400", add="text-red-400"
            )
            _append_log(f"❌ 异常: {exc}")
            chat["add_message"]("system", f"引擎异常: {exc}")
            ui.notify(f"引擎异常: {exc}", type="negative")

    async def on_demo():
        demo_text = "5V转3.3V稳压电路，带绿色LED电源指示灯"
        input_area.set_value(demo_text)
        await on_generate()

    generate_btn.on_click(lambda: asyncio.ensure_future(on_generate()))
    demo_btn.on_click(lambda: asyncio.ensure_future(on_demo()))

    chat["on_message_sent"](lambda text: asyncio.ensure_future(_chat_generate(text)))

    async def _chat_generate(text: str) -> None:
        input_area.set_value(text)
        await on_generate()
