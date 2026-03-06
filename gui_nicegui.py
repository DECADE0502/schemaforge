#!/usr/bin/env python3
from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from nicegui import ui

from schemaforge.gui_ng.theme import apply_theme


@ui.page("/")
def main_page():
    apply_theme()

    with (
        ui.header()
        .classes("items-center px-4 py-2")
        .style("background-color: #252526; border-bottom: 1px solid #3e3e42;")
    ):
        ui.label("SchemaForge").classes("text-lg font-bold")
        ui.space()
        ui.button("导出SVG", on_click=lambda: ui.notify("TODO")).props("flat dense")
        ui.button("导出BOM", on_click=lambda: ui.notify("TODO")).props("flat dense")
        ui.button("导出SPICE", on_click=lambda: ui.notify("TODO")).props("flat dense")

    with ui.tabs().classes("w-full").style("background-color: #252526;") as tabs:
        design_tab = ui.tab("⚡ 原理图设计")
        library_tab = ui.tab("📦 器件库管理")

    with ui.tab_panels(tabs, value=library_tab).classes("w-full flex-grow"):
        with ui.tab_panel(design_tab).classes("p-0"):
            from schemaforge.gui_ng.pages.design_page import build_design_page

            build_design_page()
        with ui.tab_panel(library_tab).classes("p-0"):
            from schemaforge.gui_ng.pages.library_page import build_library_page

            build_library_page()

    with ui.footer().style(
        "background-color: #0e7fd4; color: white; padding: 4px 16px; font-size: 12px;"
    ):
        ui.label("就绪 — 输入电路需求后点击「生成原理图」")


def main():
    ui.run(
        title="SchemaForge — 约束驱动的AI原理图生成器",
        dark=True,
        native=True,
        window_size=(1400, 900),
        language="zh-CN",
        reload=False,
    )


if __name__ == "__main__":
    main()
