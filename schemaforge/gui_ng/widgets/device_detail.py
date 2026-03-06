from __future__ import annotations

import asyncio
import os
from typing import Callable

from nicegui import ui

from schemaforge.library.models import DeviceModel, SymbolDef
from schemaforge.gui_ng.widgets.symbol_editor import build_symbol_editor


def build_device_detail() -> dict:
    state: dict = {
        "device": None,
        "on_symbol_saved": None,
        "ai_running": False,
    }

    with ui.column().classes("w-full gap-3"):
        ui.label("📋 器件详情").classes("text-lg font-bold text-slate-100")

        empty_label = ui.label("← 请在左侧列表中选择一个器件").classes(
            "text-slate-500 text-sm text-center w-full py-8"
        )

        with ui.column().classes("w-full gap-3") as detail_container:
            with ui.card().classes("w-full"):
                ui.label("基本信息").classes(
                    "text-sm font-semibold text-slate-300 mb-2"
                )
                info_labels: dict[str, ui.label] = {}
                fields = [
                    ("part_number", "料号"),
                    ("manufacturer", "制造商"),
                    ("category", "类别"),
                    ("package", "封装"),
                    ("description", "描述"),
                    ("source", "来源"),
                    ("confidence", "置信度"),
                ]
                with ui.grid(columns=2).classes("w-full gap-x-4 gap-y-1"):
                    for key, label_text in fields:
                        ui.label(f"{label_text}:").classes(
                            "text-slate-400 text-sm text-right"
                        )
                        val_lbl = ui.label("").classes("text-slate-200 text-sm")
                        info_labels[key] = val_lbl

            editor = build_symbol_editor()

            with ui.row().classes("gap-2 w-full"):
                save_btn = ui.button("💾 保存 Symbol").props("color=positive")
                ai_btn = ui.button("🤖 AI 重新分析引脚").props("color=primary")

        detail_container.visible = False

    def load_device(device: DeviceModel) -> None:
        state["device"] = device
        empty_label.visible = False
        detail_container.visible = True

        info_labels["part_number"].text = device.part_number
        info_labels["manufacturer"].text = device.manufacturer or "—"
        info_labels["category"].text = device.category or "—"
        info_labels["package"].text = device.package or "—"
        info_labels["description"].text = device.description or "—"
        info_labels["source"].text = device.source or "—"
        info_labels["confidence"].text = f"{device.confidence:.0%}"

        symbol = (
            device.symbol
            if (device.symbol and device.symbol.pins)
            else SymbolDef(pins=[])
        )
        editor["load_symbol"](symbol, device.part_number)

    def clear() -> None:
        state["device"] = None
        detail_container.visible = False
        empty_label.visible = True

    def _on_save() -> None:
        if state["device"] is None:
            return
        symbol = editor["get_symbol"]()
        cb = state.get("on_symbol_saved")
        if cb:
            cb(state["device"].part_number, symbol)

    async def _run_ai() -> None:
        device: DeviceModel | None = state.get("device")
        if device is None:
            return

        state["ai_running"] = True
        ai_btn.props("loading color=primary")
        ai_btn.text = "🤖 AI 分析中..."

        try:
            from schemaforge.ingest.ai_analyzer import analyze_datasheet_text
            from schemaforge.core.models import PinType
            from schemaforge.library.models import PinSide, SymbolPin

            use_mock = os.environ.get("SCHEMAFORGE_MOCK", "0").lower() in (
                "1",
                "true",
                "yes",
            )

            text_parts = [
                f"器件型号: {device.part_number}",
                f"制造商: {device.manufacturer}",
                f"类别: {device.category}",
                f"封装: {device.package}",
                f"描述: {device.description}",
            ]
            if device.specs:
                text_parts.append("电气参数:")
                for k, v in device.specs.items():
                    text_parts.append(f"  {k}: {v}")
            if device.symbol and device.symbol.pins:
                text_parts.append("现有引脚:")
                for p in device.symbol.pins:
                    text_parts.append(
                        f"  {p.name} (#{p.pin_number}, {p.side.value}, {p.pin_type.value})"
                    )

            text = "\n".join(text_parts)
            hint = f"请重新分析并完善 {device.part_number} 的引脚定义"

            result = await asyncio.to_thread(
                analyze_datasheet_text, text, hint=hint, use_mock=use_mock
            )

            if not result.success or result.data is None:
                ui.notify("AI 分析失败", type="negative")
                return

            type_map = {
                "input": PinType.INPUT,
                "output": PinType.OUTPUT,
                "power": PinType.POWER_IN,
                "passive": PinType.PASSIVE,
                "nc": PinType.NO_CONNECT,
                "bidirectional": PinType.BIDIRECTIONAL,
            }
            side_map = {
                "left": PinSide.LEFT,
                "right": PinSide.RIGHT,
                "top": PinSide.TOP,
                "bottom": PinSide.BOTTOM,
            }

            analysis = result.data
            pins: list[SymbolPin] = []
            for p in analysis.pins:
                if isinstance(p, dict):
                    pins.append(
                        SymbolPin(
                            name=p.get("name", ""),
                            pin_number=p.get("number", ""),
                            side=side_map.get(p.get("side", "left"), PinSide.LEFT),
                            pin_type=type_map.get(
                                p.get("type", "passive"), PinType.PASSIVE
                            ),
                            description=p.get("description", ""),
                        )
                    )

            if pins:
                new_symbol = SymbolDef(pins=pins)
                editor["load_symbol"](new_symbol, device.part_number)
                ui.notify(
                    f"AI 返回了 {len(pins)} 个引脚定义，请检查后保存",
                    type="positive",
                )
            else:
                ui.notify("AI 未返回有效引脚定义", type="warning")

        except Exception as exc:
            ui.notify(f"AI 调用异常: {exc}", type="negative")
        finally:
            state["ai_running"] = False
            ai_btn.props(remove="loading")
            ai_btn.text = "🤖 AI 重新分析引脚"

    save_btn.on_click(_on_save)
    ai_btn.on_click(lambda: asyncio.ensure_future(_run_ai()))

    def set_on_symbol_saved(cb: Callable) -> None:
        state["on_symbol_saved"] = cb

    return {
        "load_device": load_device,
        "clear": clear,
        "on_symbol_saved": set_on_symbol_saved,
    }
