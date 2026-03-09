"""Regression guards for the frozen legacy SchemaForgeSession path."""

from __future__ import annotations

import inspect
from pathlib import Path

from schemaforge.workflows.schemaforge_session import SchemaForgeSession


_MAIN_PY = Path(__file__).parent.parent / "main.py"


def _main_source() -> str:
    return _MAIN_PY.read_text(encoding="utf-8")


def _public_session_api(cls: type[SchemaForgeSession]) -> set[str]:
    names: set[str] = set()
    for name, value in inspect.getmembers(cls):
        if name.startswith("_"):
            continue
        if inspect.isfunction(value) or isinstance(value, property):
            names.add(name)
    return names


def test_legacy_schemaforge_session_marked_compat_only() -> None:
    assert SchemaForgeSession.LEGACY_COMPAT_ONLY is True


def test_legacy_schemaforge_session_public_api_is_frozen() -> None:
    assert _public_session_api(SchemaForgeSession) == set(
        SchemaForgeSession.FROZEN_PUBLIC_API
    )


def test_cli_default_path_is_system_session_in_source() -> None:
    source = " ".join(_main_source().split())

    # main.py uses SystemDesignSession + AI Orchestrator
    assert "session = SystemDesignSession(" in source
    assert "orch = _build_orchestrator(session)" in source
    assert "process_via_agent(" in source
    assert "run_interactive(" in source


def test_cli_uses_ai_agent_architecture_in_source() -> None:
    source = " ".join(_main_source().split())

    # SchemaForgeSession 不在 main.py 中
    assert "SchemaForgeSession" not in source
    # AI agent 架构：Orchestrator + function calling
    assert "Orchestrator" in source
    assert "_build_orchestrator" in source


def test_cli_exposes_visual_review_flag_in_source() -> None:
    source = _main_source()

    assert '"--visual-review"' in source
    assert "enable_visual_review=args.visual_review" in source
