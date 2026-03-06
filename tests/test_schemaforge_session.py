"""SchemaForge 新工作台会话测试。"""

from __future__ import annotations

from pathlib import Path

from schemaforge.design.synthesis import ExactPartResolver, parse_design_request
from schemaforge.library.models import DeviceModel
from schemaforge.library.store import ComponentStore
from schemaforge.workflows.schemaforge_session import SchemaForgeSession


def test_parse_design_request_extracts_exact_part_and_voltages() -> None:
    req = parse_design_request("帮我用 TPS54202 搭一个 20V 转 5V 的 DCDC 电路")
    assert req.part_number == "TPS54202"
    assert req.category == "buck"
    assert req.v_in == "20"
    assert req.v_out == "5"


def test_exact_part_resolver_supports_alias(tmp_path: Path) -> None:
    store = ComponentStore(tmp_path / "store")
    store.save_device(
        DeviceModel(
            part_number="TPS54202RGTR",
            aliases=["TPS54202"],
            category="buck",
        )
    )
    resolver = ExactPartResolver(store)
    hit = resolver.resolve("TPS54202")
    assert hit is not None
    assert hit.part_number == "TPS54202RGTR"


def test_session_returns_needs_asset_for_missing_exact_part(tmp_path: Path) -> None:
    session = SchemaForgeSession(tmp_path / "store", use_mock=True)
    result = session.start("帮我用 TPS54202 搭一个 20V 转 5V 的 DCDC 电路")
    assert result.status == "needs_asset"
    assert result.missing_part_number == "TPS54202"


def test_session_import_and_generate_buck_design(tmp_path: Path) -> None:
    session = SchemaForgeSession(tmp_path / "store", use_mock=True)
    start = session.start("帮我用 TPS54202 搭一个 20V 转 5V 的 DCDC 电路")
    assert start.status == "needs_asset"

    image = tmp_path / "tps54202.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 128)
    preview = session.ingest_asset(str(image))
    assert preview.status == "needs_confirmation"

    generated = session.confirm_import(
        {
            "part_number": "TPS54202",
            "manufacturer": "TI",
            "description": "2A Buck Converter",
            "category": "buck",
            "package": "SOT-23-6",
            "datasheet_url": "https://example.com/tps54202.pdf",
            "pins": [
                {"name": "BOOT", "number": "1", "type": "passive"},
                {"name": "VIN", "number": "2", "type": "power"},
                {"name": "EN", "number": "3", "type": "input"},
                {"name": "GND", "number": "4", "type": "power"},
                {"name": "FB", "number": "5", "type": "input"},
                {"name": "SW", "number": "6", "type": "output"},
            ],
            "specs": {
                "v_in_max": "28V",
                "i_out_max": "2A",
                "fsw": "500kHz",
                "v_ref": "0.8V",
            },
        }
    )
    assert generated.status == "generated"
    assert generated.bundle is not None
    assert generated.bundle.device.part_number == "TPS54202"
    assert generated.bundle.parameters["v_in"] == "20"
    assert generated.bundle.parameters["v_out"] == "5"
    assert generated.bundle.parameters["c_out"] == "22uF"
    assert generated.bundle.parameters["l_value"].endswith("uH")
    assert Path(generated.bundle.svg_path).exists()
    assert "TPS54202" in generated.bundle.bom_text
    assert "RFB1" in generated.bundle.spice_text


def test_session_revision_updates_output_cap_and_led(tmp_path: Path) -> None:
    session = SchemaForgeSession(tmp_path / "store", use_mock=True)
    store = ComponentStore(tmp_path / "store")
    store.save_device(
        DeviceModel(
            part_number="TPS5430",
            category="buck",
            specs={"v_in_max": "36V", "i_out_max": "3A", "fsw": "500kHz"},
        )
    )

    created = session.start("用 TPS5430 搭一个 12V 转 5V 的降压电路")
    assert created.status == "generated"

    revised = session.revise("换个 22μF 的输出电容，并加个绿色 LED")
    assert revised.status == "generated"
    assert revised.bundle is not None
    assert revised.bundle.parameters["c_out"] == "22uF"
    assert revised.bundle.parameters["power_led"] == "true"
    assert "DLED1" in revised.bundle.bom_text
