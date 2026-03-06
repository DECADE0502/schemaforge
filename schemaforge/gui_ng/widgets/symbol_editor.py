from __future__ import annotations

import asyncio
import base64

from nicegui import ui

from schemaforge.library.models import PinSide, SymbolDef, SymbolPin
from schemaforge.core.models import PinType
from schemaforge.schematic.renderer import TopologyRenderer

_SIDE_MAP: dict[str, PinSide] = {
    "左": PinSide.LEFT,
    "右": PinSide.RIGHT,
    "上": PinSide.TOP,
    "下": PinSide.BOTTOM,
}
_SIDE_DISPLAY: dict[PinSide, str] = {v: k for k, v in _SIDE_MAP.items()}

_TYPE_MAP: dict[str, PinType] = {
    "输入": PinType.INPUT,
    "输出": PinType.OUTPUT,
    "电源": PinType.POWER_IN,
    "无源": PinType.PASSIVE,
    "空脚": PinType.NO_CONNECT,
}
_TYPE_DISPLAY: dict[PinType, str] = {v: k for k, v in _TYPE_MAP.items()}


def build_symbol_editor() -> dict:
    state: dict = {"label": "", "debounce_task": None}

    with ui.column().classes("w-full gap-2") as container:
        ui.label("符号引脚编辑器").classes("text-base font-semibold text-slate-200")

        with ui.card().classes("w-full p-2"):
            with ui.row().classes("gap-2 mb-2"):
                add_btn = ui.button("＋ 添加引脚", icon="add").props(
                    "flat dense color=positive"
                )
                del_btn = ui.button("－ 删除选中", icon="delete").props(
                    "flat dense color=negative"
                )

            grid = (
                ui.aggrid(
                    {
                        "columnDefs": [
                            {
                                "headerName": "名称",
                                "field": "name",
                                "editable": True,
                                "flex": 2,
                            },
                            {
                                "headerName": "编号",
                                "field": "pin_number",
                                "editable": True,
                                "width": 80,
                            },
                            {
                                "headerName": "方位",
                                "field": "side",
                                "editable": True,
                                "width": 80,
                                "cellEditor": "agSelectCellEditor",
                                "cellEditorParams": {
                                    "values": ["左", "右", "上", "下"]
                                },
                            },
                            {
                                "headerName": "类型",
                                "field": "pin_type",
                                "editable": True,
                                "width": 80,
                                "cellEditor": "agSelectCellEditor",
                                "cellEditorParams": {
                                    "values": ["输入", "输出", "电源", "无源", "空脚"]
                                },
                            },
                            {
                                "headerName": "描述",
                                "field": "description",
                                "editable": True,
                                "flex": 3,
                            },
                        ],
                        "rowData": [],
                        "rowSelection": "multiple",
                    },
                    theme="quartz",
                )
                .classes("w-full")
                .style("height: 300px;")
            )

        with ui.card().classes("w-full p-2"):
            ui.label("符号预览").classes("text-sm text-slate-400 mb-1")
            preview_img = (
                ui.image("")
                .classes("w-full")
                .style(
                    "max-height: 400px; object-fit: contain;"
                    " background: #1a1a2e; border-radius: 6px;"
                )
            )
            preview_placeholder = ui.label("（无引脚，无法预览）").classes(
                "text-slate-500 text-sm text-center w-full py-4"
            )
            preview_img.visible = False

    def _get_symbol() -> SymbolDef:
        rows: list[dict] = grid.options.get("rowData", [])
        pins: list[SymbolPin] = []
        for row in rows:
            pins.append(
                SymbolPin(
                    name=str(row.get("name", "")).strip(),
                    pin_number=str(row.get("pin_number", "")).strip(),
                    side=_SIDE_MAP.get(str(row.get("side", "左")), PinSide.LEFT),
                    pin_type=_TYPE_MAP.get(
                        str(row.get("pin_type", "无源")), PinType.PASSIVE
                    ),
                    description=str(row.get("description", "")).strip(),
                )
            )
        return SymbolDef(pins=pins)

    async def _do_refresh_preview() -> None:
        symbol = _get_symbol()
        if not symbol.pins:
            preview_img.visible = False
            preview_placeholder.visible = True
            return
        try:
            png_bytes: bytes = await asyncio.to_thread(
                TopologyRenderer.render_symbol_preview,
                symbol,
                state["label"],
                120,
            )
            b64 = base64.b64encode(png_bytes).decode()
            preview_img.set_source(f"data:image/png;base64,{b64}")
            preview_img.visible = True
            preview_placeholder.visible = False
        except Exception:
            preview_img.visible = False
            preview_placeholder.text = "渲染失败"
            preview_placeholder.classes("text-red-400")
            preview_placeholder.visible = True

    def _schedule_preview() -> None:
        task = state.get("debounce_task")
        if task is not None and not task.done():
            task.cancel()

        async def _delayed():
            await asyncio.sleep(0.3)
            await _do_refresh_preview()

        state["debounce_task"] = asyncio.ensure_future(_delayed())

    grid.on("cellValueChanged", lambda _: _schedule_preview())

    def _add_pin() -> None:
        rows: list[dict] = list(grid.options.get("rowData", []))
        rows.append(
            {
                "name": "",
                "pin_number": str(len(rows) + 1),
                "side": "左",
                "pin_type": "无源",
                "description": "",
            }
        )
        grid.options["rowData"] = rows
        grid.update()
        _schedule_preview()

    def _delete_selected() -> None:
        async def _do() -> None:
            selected = await grid.get_selected_rows()
            if not selected:
                return
            selected_keys = {
                (r.get("name", ""), r.get("pin_number", "")) for r in selected
            }
            rows: list[dict] = list(grid.options.get("rowData", []))
            grid.options["rowData"] = [
                r
                for r in rows
                if (r.get("name", ""), r.get("pin_number", "")) not in selected_keys
            ]
            grid.update()
            _schedule_preview()

        asyncio.ensure_future(_do())

    add_btn.on_click(_add_pin)
    del_btn.on_click(_delete_selected)

    def load_symbol(symbol: SymbolDef, label: str) -> None:
        state["label"] = label
        grid.options["rowData"] = [
            {
                "name": pin.name,
                "pin_number": pin.pin_number,
                "side": _SIDE_DISPLAY.get(pin.side, "左"),
                "pin_type": _TYPE_DISPLAY.get(pin.pin_type, "无源"),
                "description": pin.description,
            }
            for pin in symbol.pins
        ]
        grid.update()
        _schedule_preview()

    return {
        "load_symbol": load_symbol,
        "get_symbol": _get_symbol,
        "container": container,
    }
