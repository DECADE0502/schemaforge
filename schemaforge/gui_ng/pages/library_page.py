from __future__ import annotations

from pathlib import Path
from typing import Optional

from nicegui import ui

from schemaforge.library.service import LibraryService

_service = LibraryService(Path(__file__).resolve().parents[3] / "schemaforge" / "store")

_selected_pn: Optional[str] = None
_grid: Optional[object] = None
_stats_label: Optional[object] = None
_detail_tab: Optional[object] = None
_detail_panel_tab: Optional[object] = None


async def _refresh_device_list(search_query: str = "") -> None:
    global _grid, _stats_label

    if search_query:
        devices = _service.search(query=search_query)
    else:
        part_numbers = _service.list_all()
        devices = []
        for pn in part_numbers:
            dev = _service.get(pn)
            if dev:
                devices.append(dev)

    row_data = [
        {
            "part_number": d.part_number,
            "category": d.category,
            "manufacturer": d.manufacturer,
            "package": d.package,
            "source": d.source,
        }
        for d in devices
    ]

    if _grid is not None:
        _grid.options["rowData"] = row_data
        _grid.update()

    stats = _service.get_stats()
    total = stats["total_devices"]
    if _stats_label is not None:
        _stats_label.set_text(f"共 {total} 个器件")


async def _on_device_selected(part_number: str) -> None:
    global _selected_pn, _detail_tab, _detail_panel_tab

    _selected_pn = part_number

    if _detail_tab is not None and _detail_panel_tab is not None:
        try:
            from schemaforge.gui_ng.widgets.device_detail import load_device_detail

            device = _service.get(part_number)
            if device:
                load_device_detail(device)
        except ImportError:
            pass
        _detail_tab.set_value(_detail_panel_tab)


async def _on_delete_selected() -> None:
    global _selected_pn

    if not _selected_pn:
        ui.notify("请先选中要删除的器件", type="warning")
        return

    pn = _selected_pn

    with ui.dialog() as dialog, ui.card():
        ui.label(f"确定要删除器件 {pn} 吗？").classes("text-base")
        with ui.row().classes("gap-2 justify-end mt-4"):
            ui.button("取消", on_click=dialog.close).props("flat")

            async def confirm_delete():
                _service.delete(pn)
                _selected_pn = None
                dialog.close()
                await _refresh_device_list()
                ui.notify(f"已删除器件 {pn}", type="positive")

            ui.button("确认删除", on_click=confirm_delete).classes("sf-danger-btn")

    dialog.open()


def build_library_page() -> None:
    global _grid, _stats_label, _selected_pn, _detail_tab, _detail_panel_tab

    with ui.column().classes("w-full h-full p-4 gap-3"):
        with ui.row().classes("w-full items-center gap-3"):
            ui.label("📦 器件库管理").classes("sf-title")
            ui.space()
            _stats_label = ui.label("").classes("sf-muted")
            ui.button("🔄 刷新", on_click=lambda: _refresh_device_list()).props(
                "flat dense"
            )

        with ui.splitter(value=35).classes("w-full flex-grow") as splitter:
            with splitter.before:
                with ui.column().classes("w-full h-full gap-2"):
                    search_input = (
                        ui.input(placeholder="搜索器件库...")
                        .classes("w-full")
                        .props("dense outlined")
                    )
                    search_input.on(
                        "update:model-value",
                        lambda e: _refresh_device_list(
                            e.args if isinstance(e.args, str) else ""
                        ),
                    )

                    _grid = (
                        ui.aggrid(
                            {
                                "columnDefs": [
                                    {
                                        "headerName": "料号",
                                        "field": "part_number",
                                        "flex": 2,
                                        "sortable": True,
                                        "filter": True,
                                    },
                                    {
                                        "headerName": "类别",
                                        "field": "category",
                                        "width": 100,
                                        "sortable": True,
                                        "filter": True,
                                    },
                                    {
                                        "headerName": "制造商",
                                        "field": "manufacturer",
                                        "flex": 2,
                                        "sortable": True,
                                    },
                                    {
                                        "headerName": "封装",
                                        "field": "package",
                                        "width": 100,
                                    },
                                    {
                                        "headerName": "来源",
                                        "field": "source",
                                        "width": 80,
                                    },
                                ],
                                "rowData": [],
                                "rowSelection": "single",
                                "animateRows": True,
                            },
                            theme="quartz",
                        )
                        .classes("w-full")
                        .style("height: calc(100vh - 200px);")
                    )

                    _grid.on(
                        "cellClicked",
                        lambda e: _on_device_selected(
                            e.args.get("data", {}).get("part_number", "")
                        )
                        if e.args.get("data")
                        else None,
                    )

                    ui.button("删除选中", on_click=_on_delete_selected).classes(
                        "sf-danger-btn"
                    ).props("flat dense")

            with splitter.after:
                with ui.tabs().classes("w-full") as right_tabs:
                    form_tab = ui.tab("✏️ 手动录入")
                    search_tab = ui.tab("🔍 EasyEDA搜索")
                    import_tab = ui.tab("📄 PDF/图片导入")
                    _detail_panel_tab = ui.tab("📋 器件详情")

                _detail_tab = right_tabs

                with ui.tab_panels(right_tabs, value=form_tab).classes(
                    "w-full flex-grow"
                ):
                    with ui.tab_panel(form_tab):
                        try:
                            from schemaforge.gui_ng.widgets.device_form import (
                                build_device_form,
                            )

                            build_device_form()
                        except ImportError:
                            ui.label("手动录入模块加载中...").classes("sf-muted")

                    with ui.tab_panel(search_tab):
                        try:
                            from schemaforge.gui_ng.widgets.device_search import (
                                build_device_search,
                            )

                            build_device_search()
                        except ImportError:
                            ui.label("EasyEDA搜索模块加载中...").classes("sf-muted")

                    with ui.tab_panel(import_tab):
                        try:
                            from schemaforge.gui_ng.widgets.import_wizard import (
                                build_import_wizard,
                            )

                            build_import_wizard()
                        except ImportError:
                            ui.label("PDF/图片导入模块加载中...").classes("sf-muted")

                    with ui.tab_panel(_detail_panel_tab):
                        try:
                            from schemaforge.gui_ng.widgets.device_detail import (
                                build_device_detail,
                            )

                            build_device_detail()
                        except ImportError:
                            ui.label("器件详情模块加载中...").classes("sf-muted")

    ui.timer(0.1, lambda: _refresh_device_list(), once=True)
