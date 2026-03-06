from __future__ import annotations

from typing import Callable

from nicegui import ui

from schemaforge.library.validator import DeviceDraft, PinDraft, VALID_CATEGORIES


def build_device_form() -> dict:
    state: dict = {"on_draft_ready": None}

    with ui.scroll_area().classes("w-full h-full") as _scroll:
        with ui.column().classes("w-full gap-4 p-4"):
            ui.label("手动录入器件").classes("text-lg font-bold text-slate-100")

            with ui.card().classes("w-full"):
                ui.label("基础信息").classes(
                    "text-sm font-semibold text-slate-300 mb-2"
                )
                with ui.grid(columns=2).classes("w-full gap-x-4 gap-y-2"):
                    ui.label("料号 *").classes(
                        "text-slate-400 text-sm self-center text-right"
                    )
                    part_number_input = ui.input(
                        placeholder="如: TPS54202、AMS1117-3.3"
                    ).classes("w-full")

                    ui.label("制造商").classes(
                        "text-slate-400 text-sm self-center text-right"
                    )
                    manufacturer_input = ui.input(
                        placeholder="如: Texas Instruments"
                    ).classes("w-full")

                    ui.label("类别").classes(
                        "text-slate-400 text-sm self-center text-right"
                    )
                    category_select = ui.select(
                        options=[""] + sorted(VALID_CATEGORIES),
                        value="",
                        with_input=True,
                    ).classes("w-full")

                    ui.label("描述").classes(
                        "text-slate-400 text-sm self-center text-right"
                    )
                    description_input = ui.input(
                        placeholder="如: 3.3V 1A 低压差线性稳压器"
                    ).classes("w-full")

                    ui.label("封装").classes(
                        "text-slate-400 text-sm self-center text-right"
                    )
                    package_input = ui.input(
                        placeholder="如: SOT-223、SOT-23-6"
                    ).classes("w-full")

                    ui.label("LCSC 编号").classes(
                        "text-slate-400 text-sm self-center text-right"
                    )
                    lcsc_input = ui.input(placeholder="如: C87774").classes("w-full")

                    ui.label("Datasheet").classes(
                        "text-slate-400 text-sm self-center text-right"
                    )
                    datasheet_input = ui.input(placeholder="https://...").classes(
                        "w-full"
                    )

            with ui.card().classes("w-full"):
                ui.label("引脚定义").classes(
                    "text-sm font-semibold text-slate-300 mb-2"
                )
                with ui.row().classes("gap-2 mb-2"):
                    ui.button("＋ 添加引脚", icon="add").props(
                        "flat dense color=positive"
                    ).on_click(lambda: _add_pin_row())
                    ui.button("－ 删除末行", icon="remove").props(
                        "flat dense color=negative"
                    ).on_click(lambda: _remove_last_pin())

                pin_grid = (
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
                                    "field": "number",
                                    "editable": True,
                                    "width": 80,
                                },
                                {
                                    "headerName": "类型",
                                    "field": "pin_type",
                                    "editable": True,
                                    "width": 120,
                                    "cellEditor": "agSelectCellEditor",
                                    "cellEditorParams": {
                                        "values": [
                                            "",
                                            "input",
                                            "output",
                                            "power",
                                            "passive",
                                            "nc",
                                            "bidirectional",
                                        ]
                                    },
                                },
                                {
                                    "headerName": "方位",
                                    "field": "side",
                                    "editable": True,
                                    "width": 100,
                                    "cellEditor": "agSelectCellEditor",
                                    "cellEditorParams": {
                                        "values": ["left", "right", "top", "bottom"]
                                    },
                                },
                                {
                                    "headerName": "说明",
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
                    .style("height: 280px;")
                )

            with ui.card().classes("w-full"):
                ui.label("电气参数（可选）").classes(
                    "text-sm font-semibold text-slate-300 mb-2"
                )
                specs_textarea = (
                    ui.textarea(
                        placeholder="每行一个参数，格式: key=value\n示例:\nv_in_max=28V\nv_out_typ=3.3V\ni_out_max=2A",
                    )
                    .classes("w-full")
                    .style("min-height: 120px;")
                )

            with ui.card().classes("w-full"):
                ui.label("备注").classes("text-sm font-semibold text-slate-300 mb-2")
                notes_textarea = (
                    ui.textarea().classes("w-full").style("min-height: 80px;")
                )

            with ui.row().classes("gap-2"):
                ui.button("提交入库").props("color=primary").on_click(
                    lambda: _on_submit()
                )
                ui.button("清空").props("flat").on_click(lambda: _clear())

    def _add_pin_row() -> None:
        rows: list[dict] = list(pin_grid.options.get("rowData", []))
        rows.append(
            {
                "name": "",
                "number": str(len(rows) + 1),
                "pin_type": "passive",
                "side": "left",
                "description": "",
            }
        )
        pin_grid.options["rowData"] = rows
        pin_grid.update()

    def _remove_last_pin() -> None:
        rows: list[dict] = list(pin_grid.options.get("rowData", []))
        if rows:
            pin_grid.options["rowData"] = rows[:-1]
            pin_grid.update()

    def _collect_draft() -> DeviceDraft:
        rows: list[dict] = pin_grid.options.get("rowData", [])
        pins: list[PinDraft] = [
            PinDraft(
                name=str(r.get("name", "")).strip(),
                number=str(r.get("number", str(i + 1))).strip(),
                pin_type=str(r.get("pin_type", "")).strip(),
                side=str(r.get("side", "left")).strip(),
                description=str(r.get("description", "")).strip(),
            )
            for i, r in enumerate(rows)
        ]

        specs: dict[str, str] = {}
        for line in specs_textarea.value.strip().splitlines():
            line = line.strip()
            if "=" in line:
                key, _, val = line.partition("=")
                key = key.strip()
                if key:
                    specs[key] = val.strip()

        return DeviceDraft(
            part_number=part_number_input.value.strip(),
            manufacturer=manufacturer_input.value.strip(),
            category=category_select.value.strip() if category_select.value else "",
            description=description_input.value.strip(),
            package=package_input.value.strip(),
            lcsc_part=lcsc_input.value.strip(),
            datasheet_url=datasheet_input.value.strip(),
            pins=pins,
            pin_count=len(pins),
            specs=specs,
            source="manual",
            notes=notes_textarea.value.strip(),
        )

    def _on_submit() -> None:
        draft = _collect_draft()
        if not draft.part_number:
            ui.notify("料号不能为空！", type="warning")
            return
        cb = state.get("on_draft_ready")
        if cb:
            cb(draft)

    def _clear() -> None:
        part_number_input.set_value("")
        manufacturer_input.set_value("")
        category_select.set_value("")
        description_input.set_value("")
        package_input.set_value("")
        lcsc_input.set_value("")
        datasheet_input.set_value("")
        pin_grid.options["rowData"] = []
        pin_grid.update()
        specs_textarea.set_value("")
        notes_textarea.set_value("")

    def load_draft(draft: DeviceDraft) -> None:
        part_number_input.set_value(draft.part_number)
        manufacturer_input.set_value(draft.manufacturer)
        category_select.set_value(
            draft.category if draft.category in VALID_CATEGORIES else ""
        )
        description_input.set_value(draft.description)
        package_input.set_value(draft.package)
        lcsc_input.set_value(draft.lcsc_part)
        datasheet_input.set_value(draft.datasheet_url)
        notes_textarea.set_value(draft.notes)

        pin_grid.options["rowData"] = [
            {
                "name": p.name,
                "number": p.number,
                "pin_type": p.pin_type,
                "side": p.side,
                "description": p.description,
            }
            for p in draft.pins
        ]
        pin_grid.update()

        spec_lines = [f"{k}={v}" for k, v in draft.specs.items()]
        specs_textarea.set_value("\n".join(spec_lines))

    def set_on_draft_ready(cb: Callable) -> None:
        state["on_draft_ready"] = cb

    return {
        "clear": _clear,
        "load_draft": load_draft,
        "on_draft_ready": set_on_draft_ready,
    }
