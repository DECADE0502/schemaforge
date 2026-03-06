"""SchemaForge 设计工具测试。"""

from __future__ import annotations

from pathlib import Path

from schemaforge.agent.design_tools import build_design_tool_registry
from schemaforge.library.models import DeviceModel
from schemaforge.library.store import ComponentStore
from schemaforge.workflows.schemaforge_session import SchemaForgeSession


def test_design_tool_registry_starts_design(tmp_path: Path) -> None:
    store = ComponentStore(tmp_path / "store")
    store.save_device(
        DeviceModel(
            part_number="AMS1117-3.3",
            category="ldo",
            specs={"v_out": "3.3V"},
        )
    )
    session = SchemaForgeSession(tmp_path / "store", use_mock=True)
    registry = build_design_tool_registry(session)

    result = registry.execute(
        "start_design_request",
        {"user_input": "用 AMS1117-3.3 搭一个 5V 转 3.3V 稳压电路"},
    )
    assert result.success
    assert result.data["status"] == "generated"
