"""PDF/图片导入向导 (NiceGUI版)

分步导入流程:
1. 文件选择 (PDF 或图片)
2. 解析进度展示
3. AI 分析结果预览
4. 追问卡片 (用户补全缺失信息)
5. 最终 DeviceDraft 预览
6. 确认入库

所有 UI 文案为中文。
"""

from __future__ import annotations

import asyncio
import base64
import tempfile
from pathlib import Path
from typing import Callable

from nicegui import ui


def build_import_wizard(use_mock: bool = True) -> dict:
    """构建 PDF/图片导入向导

    Returns:
        dict with keys:
            - reset: callable to reset the wizard
            - on_draft_ready: callable to set draft-ready callback
    """

    # ── 状态 ──────────────────────────────────────────────────
    uploaded_file_path: str | None = None
    extra_images: list[str] = []
    current_draft = None
    current_result = None
    answer_inputs: dict[str, object] = {}
    _on_draft_ready_cb: Callable | None = None

    # ── 外层容器 ──────────────────────────────────────────────
    with ui.column().classes("w-full gap-3 p-4"):
        # 标题
        ui.label("📄 PDF / 图片导入").classes("text-xl font-bold text-blue-300")
        ui.label(
            "上传 PDF datasheet 或引脚图截图，AI 自动提取器件信息。\n"
            "不确定的信息会提示你手动补全。"
        ).classes("text-sm text-gray-400 whitespace-pre-line")

        # ── 文件上传区 ──
        with ui.card().classes("w-full p-3"):
            ui.label("文件选择").classes("font-semibold text-gray-300 mb-2")
            file_label = ui.label("未选择文件").classes("text-sm text-gray-500")

            def handle_upload(e):
                nonlocal uploaded_file_path
                content = e.content.read()
                suffix = Path(e.name).suffix
                tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
                tmp.write(content)
                tmp.close()
                uploaded_file_path = tmp.name
                file_label.set_text(f"{e.name} ({len(content) / 1024:.0f}KB)")
                file_label.classes(remove="text-gray-500", add="text-green-400")
                start_btn.enable()

            ui.upload(on_upload=handle_upload, auto_upload=True).props(
                'accept=".pdf,.png,.jpg,.jpeg,.webp,.gif,.bmp"'
            ).classes("max-w-full")

        # ── 引脚图 / 封装图（可选多张） ──
        with ui.card().classes("w-full p-3"):
            ui.label("引脚图 / 封装图（可选）").classes(
                "font-semibold text-gray-300 mb-1"
            )
            ui.label(
                "上传引脚图或封装图截图，AI 会结合 PDF 文本与图片一起分析。"
            ).classes("text-xs text-gray-500 mb-2")

            extra_file_list = ui.column().classes("gap-1 w-full")

            def handle_extra_upload(e):
                content = e.content.read()
                suffix = Path(e.name).suffix
                tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
                tmp.write(content)
                tmp.close()
                extra_images.append(tmp.name)
                with extra_file_list:
                    with ui.row().classes("items-center gap-2 text-xs text-gray-400"):
                        ui.icon("image").classes("text-blue-400")
                        ui.label(e.name)

            ui.upload(
                on_upload=handle_extra_upload,
                auto_upload=True,
                multiple=True,
            ).props('accept=".png,.jpg,.jpeg,.webp,.gif,.bmp"').classes("max-w-full")

        # ── 器件型号提示 ──
        with ui.row().classes("w-full items-center gap-3"):
            ui.label("器件型号提示 (可选):").classes(
                "text-sm text-gray-400 whitespace-nowrap"
            )
            hint_input = ui.input(
                placeholder="如果你知道器件型号，输入可提高识别准确率"
            ).classes("flex-1")

        # ── 开始按钮 ──
        start_btn = (
            ui.button("开始提取", icon="play_arrow")
            .classes("w-full bg-blue-700 text-white font-bold")
            .props("size=lg")
        )
        start_btn.disable()

        # ── 进度区 ──
        progress_bar = ui.linear_progress(value=0).classes("w-full").props("color=blue")
        progress_bar.visible = False
        progress_label = ui.label("").classes("text-xs text-gray-400")
        progress_label.visible = False

        # ── 结果区（动态内容） ──
        result_area = ui.column().classes("w-full gap-3")

        # ── 底部按钮 ──
        with ui.row().classes("w-full gap-3 mt-2"):
            confirm_btn = (
                ui.button("✅ 确认入库", icon="check")
                .classes("flex-1 bg-green-700 text-white font-bold")
                .props("size=md")
            )
            confirm_btn.disable()

            reset_btn = (
                ui.button("重置", icon="refresh")
                .classes("bg-gray-600 text-white")
                .props("size=md")
            )

    # ── 内部函数 ────────────────────────────────────────────

    def _clear_results():
        nonlocal answer_inputs
        result_area.clear()
        answer_inputs = {}
        confirm_btn.disable()

    def _show_results(result) -> None:
        """展示提取结果与追问卡片"""
        nonlocal current_draft, answer_inputs
        _clear_results()

        if result.draft is None:
            return

        draft = result.draft
        current_draft = draft

        with result_area:
            # 已提取信息概览
            with ui.card().classes("w-full p-3"):
                ui.label("已提取信息").classes("font-semibold text-gray-200 mb-2")
                fields = [
                    ("料号", draft.part_number),
                    ("制造商", draft.manufacturer),
                    ("类别", draft.category),
                    ("描述", draft.description),
                    ("封装", draft.package),
                    ("引脚数", str(draft.pin_count) if draft.pin_count else ""),
                    ("来源", draft.source),
                    (
                        "置信度",
                        f"{draft.confidence:.0%}"
                        if hasattr(draft, "confidence")
                        else "",
                    ),
                ]
                for lbl, val in fields:
                    with ui.row().classes("w-full items-start gap-2 py-0.5"):
                        ui.label(f"{lbl}:").classes(
                            "text-xs text-gray-400 w-16 shrink-0 text-right"
                        )
                        disp = val or "(未识别)"
                        color = "text-white" if val else "text-red-400"
                        ui.label(disp).classes(f"text-xs {color} break-all")

            # Symbol 预览
            if draft.pins:
                _render_symbol_preview(draft)

            # 追问卡片
            if result.needs_user_input and result.questions:
                with ui.card().classes("w-full p-3 border border-yellow-600"):
                    ui.label(f"❓ 需要补全 ({len(result.questions)} 项)").classes(
                        "font-semibold text-yellow-300 mb-2"
                    )

                    answer_inputs.clear()
                    for q in result.questions:
                        with ui.column().classes("w-full gap-1 mb-2"):
                            ui.label(q.get("text", "")).classes(
                                "text-sm text-gray-300 break-words"
                            )
                            field_path = q.get("field_path", q.get("question_id", ""))
                            inp = ui.input(
                                placeholder="请输入...",
                                value=str(q.get("default", "")),
                            ).classes("w-full")
                            answer_inputs[field_path] = inp

        confirm_btn.enable()

    def _render_symbol_preview(draft) -> None:
        """将 draft.pins 渲染为 base64 PNG 并显示"""
        try:
            from schemaforge.library.models import PinSide, SymbolDef, SymbolPin
            from schemaforge.core.models import PinType

            type_map = {
                "input": PinType.INPUT,
                "output": PinType.OUTPUT,
                "power": PinType.POWER,
                "passive": PinType.PASSIVE,
                "nc": PinType.NC,
                "bidirectional": PinType.BIDIRECTIONAL,
            }
            side_map = {
                "left": PinSide.LEFT,
                "right": PinSide.RIGHT,
                "top": PinSide.TOP,
                "bottom": PinSide.BOTTOM,
            }

            symbol_pins = []
            for pin in draft.pins:
                symbol_pins.append(
                    SymbolPin(
                        name=pin.name or f"PIN{pin.number}",
                        pin_number=pin.number,
                        side=side_map.get(pin.side, PinSide.LEFT),
                        pin_type=type_map.get(pin.pin_type, PinType.PASSIVE),
                        description=pin.description,
                    )
                )

            if not symbol_pins:
                return

            symbol = SymbolDef(pins=symbol_pins)
            label_text = draft.part_number or "IC"

            from schemaforge.schematic.renderer import TopologyRenderer

            png_bytes = TopologyRenderer.render_symbol_preview(symbol, label_text)
            b64 = base64.b64encode(png_bytes).decode()

            with result_area:
                with ui.card().classes("w-full p-3"):
                    ui.label("原理图 Symbol 预览").classes(
                        "font-semibold text-gray-200 mb-2"
                    )
                    ui.image(f"data:image/png;base64,{b64}").classes(
                        "max-w-full max-h-64 object-contain"
                    )
        except Exception:
            pass  # 渲染失败时静默跳过

    async def on_start():
        nonlocal uploaded_file_path, current_result

        if not uploaded_file_path:
            ui.notify("请先上传文件！", type="warning")
            return

        start_btn.disable()
        confirm_btn.disable()
        _clear_results()

        progress_bar.visible = True
        progress_bar.set_value(0)
        progress_label.visible = True
        progress_label.set_text("正在启动提取...")

        filepath = uploaded_file_path
        hint = hint_input.value.strip()

        def _progress_cb(message: str, percentage: int):
            progress_label.set_text(message)
            if percentage >= 0:
                progress_bar.set_value(percentage / 100)

        try:
            from schemaforge.ingest.datasheet_extractor import (
                ExtractionResult,
                extract_from_pdf,
                extract_from_image,
            )

            ext = Path(filepath).suffix.lower()

            if ext == ".pdf":
                result = await asyncio.to_thread(
                    extract_from_pdf,
                    filepath,
                    hint=hint,
                    use_mock=use_mock,
                    extra_images=extra_images,
                )
            elif ext in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"):
                result = await asyncio.to_thread(
                    extract_from_image,
                    filepath,
                    hint=hint,
                    use_mock=use_mock,
                )
            else:
                from schemaforge.ingest.datasheet_extractor import ExtractionResult

                result = ExtractionResult(error_message=f"不支持的文件格式: {ext}")

            current_result = result
            progress_bar.visible = False

            if not result.success:
                progress_label.set_text(f"❌ 提取失败: {result.error_message}")
                progress_label.classes(remove="text-gray-400", add="text-red-400")
                ui.notify(f"提取失败: {result.error_message}", type="negative")
            else:
                progress_label.set_text("✅ 提取完成")
                progress_label.classes(remove="text-gray-400", add="text-green-400")
                _show_results(result)

        except Exception as exc:
            progress_bar.visible = False
            progress_label.set_text(f"❌ 提取异常: {exc}")
            progress_label.classes(remove="text-gray-400", add="text-red-400")
            ui.notify(f"提取异常: {exc}", type="negative")
        finally:
            start_btn.enable()

    async def on_confirm():
        nonlocal current_draft

        if current_draft is None:
            ui.notify("没有可确认的草稿", type="warning")
            return

        # 收集用户回答
        answers: dict[str, str] = {}
        for field_path, widget in answer_inputs.items():
            val = widget.value.strip() if hasattr(widget, "value") else ""
            if val:
                answers[field_path] = val

        if answers:
            try:
                from schemaforge.ingest.datasheet_extractor import apply_user_answers

                current_draft = apply_user_answers(current_draft, answers)
            except Exception:
                pass

        if not current_draft.part_number:
            ui.notify("料号不能为空！请先填写器件型号。", type="warning")
            return

        if _on_draft_ready_cb is not None:
            _on_draft_ready_cb(current_draft)
        ui.notify("草稿已确认入库", type="positive")

    def _reset():
        nonlocal uploaded_file_path, extra_images, current_draft, current_result
        uploaded_file_path = None
        extra_images = []
        current_draft = None
        current_result = None
        file_label.set_text("未选择文件")
        file_label.classes(remove="text-green-400", add="text-gray-500")
        hint_input.set_value("")
        extra_file_list.clear()
        _clear_results()
        start_btn.disable()
        progress_bar.visible = False
        progress_label.visible = False
        progress_label.set_text("")
        progress_label.classes(
            remove="text-red-400 text-green-400", add="text-gray-400"
        )

    # 绑定按钮事件
    start_btn.on_click(lambda: asyncio.ensure_future(on_start()))
    confirm_btn.on_click(lambda: asyncio.ensure_future(on_confirm()))
    reset_btn.on_click(_reset)

    # ── 返回控制字典 ──────────────────────────────────────────

    def set_on_draft_ready(cb: Callable) -> None:
        nonlocal _on_draft_ready_cb
        _on_draft_ready_cb = cb

    return {
        "reset": _reset,
        "on_draft_ready": set_on_draft_ready,
    }
