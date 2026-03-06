from __future__ import annotations

import asyncio
from typing import Callable

from nicegui import ui

from schemaforge.ingest.easyeda_provider import EasyEDAHit, search_easyeda


def build_device_search() -> dict:
    state: dict = {"on_hit_selected": None}

    with ui.column().classes("w-full gap-3"):
        ui.label("EasyEDA 在线搜索").classes("text-lg font-bold text-slate-100")

        with ui.row().classes("w-full gap-2"):
            search_input = ui.input(
                placeholder="输入器件型号搜索，如: TPS54202"
            ).classes("flex-1")
            search_btn = ui.button("搜索", icon="search").props("color=primary")

        spinner = ui.spinner(size="md").classes("self-center")
        spinner.visible = False

        status_label = ui.label("").classes("text-slate-500 text-sm")

        results_col = ui.column().classes("w-full gap-2")

    async def _do_search(keyword: str) -> None:
        search_btn.props("loading")
        search_btn.props("disable")
        spinner.visible = True
        status_label.text = f"正在搜索: {keyword}..."
        results_col.clear()

        try:
            result = await asyncio.to_thread(search_easyeda, keyword, 20)
            if result.success:
                hits: list[EasyEDAHit] = result.data or []
                if not hits:
                    status_label.text = "未找到匹配器件"
                else:
                    status_label.text = f"找到 {len(hits)} 个结果"
                    with results_col:
                        for hit in hits:
                            _build_hit_card(hit)
            else:
                err_msg = result.error.message if result.error else "搜索失败"
                status_label.text = f"搜索失败: {err_msg}"
        except Exception as exc:
            status_label.text = f"搜索异常: {exc}"
        finally:
            search_btn.props(remove="loading disable")
            spinner.visible = False

    def _on_search() -> None:
        keyword = search_input.value.strip()
        if not keyword:
            ui.notify("请输入搜索关键词！", type="warning")
            return
        asyncio.ensure_future(_do_search(keyword))

    def _build_hit_card(hit: EasyEDAHit) -> None:
        with ui.card().classes(
            "w-full p-3 hover:bg-slate-700 transition-colors cursor-default"
        ):
            with ui.row().classes("w-full items-start justify-between gap-2"):
                with ui.column().classes("flex-1 gap-1"):
                    ui.label(hit.title or "(无标题)").classes(
                        "text-slate-100 font-medium text-sm"
                    )
                    if hit.description:
                        ui.label(hit.description[:120]).classes(
                            "text-slate-400 text-xs"
                        )

                    meta_parts: list[str] = []
                    if hit.manufacturer:
                        meta_parts.append(f"厂商: {hit.manufacturer}")
                    if hit.package:
                        meta_parts.append(f"封装: {hit.package}")
                    if hit.lcsc_part:
                        meta_parts.append(f"LCSC: {hit.lcsc_part}")
                    if hit.pin_count:
                        meta_parts.append(f"引脚: {hit.pin_count}")
                    if meta_parts:
                        ui.label(" | ".join(meta_parts)).classes(
                            "text-slate-500 text-xs"
                        )

                ui.button("导入", icon="download").props(
                    "flat dense color=positive"
                ).on_click(lambda h=hit: _on_import(h))

    def _on_import(hit: EasyEDAHit) -> None:
        cb = state.get("on_hit_selected")
        if cb:
            cb(hit)

    search_btn.on_click(_on_search)
    search_input.on("keydown.enter", _on_search)

    def set_on_hit_selected(cb: Callable) -> None:
        state["on_hit_selected"] = cb

    return {
        "on_hit_selected": set_on_hit_selected,
    }
