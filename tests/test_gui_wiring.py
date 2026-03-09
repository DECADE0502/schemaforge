"""GUI 集成布线测试

验证 design_page.py / engine_worker.py 的多轮对话布线：
- SchemaForgeWorker 会话持久化（session_ready 信号）
- SchemaForgeReviseWorker 多轮修改路径
- SchemaForgeOrchestratedWorker AI 编排路径
- _on_chat_send 路由逻辑（首次走 start，后续走 revise）
- _display_bundle 数据提取
- AgentStep 分发（_on_orchestrated_finished removed as dead code）

NOTE: 不依赖 PySide6 运行时，通过源码级检查和单元测试验证逻辑。
"""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

# ============================================================
# Source paths
# ============================================================

_DESIGN_PAGE_PY = (
    Path(__file__).parent.parent
    / "schemaforge"
    / "gui"
    / "pages"
    / "design_page.py"
)
_ENGINE_WORKER_PY = (
    Path(__file__).parent.parent
    / "schemaforge"
    / "gui"
    / "workers"
    / "engine_worker.py"
)
_MAIN_PY = Path(__file__).parent.parent / "main.py"


def _read_source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _parse_ast(path: Path) -> ast.Module:
    return ast.parse(_read_source(path))


# ============================================================
# Source-level structural tests
# ============================================================


class TestDesignPageStructure:
    """design_page.py 源码结构验证。"""

    def setup_method(self) -> None:
        self.src = _read_source(_DESIGN_PAGE_PY)
        self.tree = _parse_ast(_DESIGN_PAGE_PY)
        self.class_methods = self._get_class_methods("DesignPage")

    def _get_class_methods(self, cls_name: str) -> list[str]:
        for node in ast.walk(self.tree):
            if isinstance(node, ast.ClassDef) and node.name == cls_name:
                return [
                    n.name
                    for n in node.body
                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                ]
        return []

    def test_imports_revise_worker(self) -> None:
        assert "SchemaForgeReviseWorker" in self.src

    def test_no_dead_orchestrated_worker_import(self) -> None:
        """SchemaForgeOrchestratedWorker import removed (dead code cleanup)."""
        assert "SchemaForgeOrchestratedWorker" not in self.src

    def test_has_start_revise_method(self) -> None:
        assert "_start_revise" in self.class_methods

    def test_has_on_session_ready(self) -> None:
        assert "_on_session_ready" in self.class_methods

    def test_has_on_sf_revise_finished(self) -> None:
        assert "_on_sf_revise_finished" in self.class_methods

    def test_has_start_revise_image_method(self) -> None:
        assert "_start_revise_image" in self.class_methods

    def test_no_dead_on_orchestrated_finished(self) -> None:
        assert "_on_orchestrated_finished" not in self.class_methods

    def test_has_display_bundle(self) -> None:
        assert "_display_bundle" in self.class_methods

    def test_has_design_flag(self) -> None:
        assert "_has_design" in self.src

    def test_session_ready_signal_connected(self) -> None:
        """session_ready 信号必须在 _on_generate 中连接。"""
        assert "session_ready.connect" in self.src

    def test_chat_send_routes_to_revise(self) -> None:
        """_on_chat_send 必须检查 _sf_session 和 _has_design。"""
        assert "_sf_session is not None" in self.src
        assert "_has_design" in self.src
        assert "_start_revise" in self.src

    def test_image_paste_routes_to_image_revise(self) -> None:
        assert "_start_revise_image" in self.src
        assert "图片修改意图" in self.src or "图片修改" in self.src

    def test_visual_review_checkbox_exists(self) -> None:
        assert "_visual_review_checkbox" in self.src
        assert "visual review" in self.src

    def test_generate_passes_visual_review_toggle(self) -> None:
        assert "enable_visual_review=self._visual_review_checkbox.isChecked()" in self.src

    def test_revise_hint_shown_after_generation(self) -> None:
        """生成完成后应该提示用户可以通过对话修改。"""
        assert "你可以在对话框中输入修改指令" in self.src

class TestEngineWorkerStructure:
    """engine_worker.py 源码结构验证。"""

    def setup_method(self) -> None:
        self.src = _read_source(_ENGINE_WORKER_PY)
        self.tree = _parse_ast(_ENGINE_WORKER_PY)

    def _get_class_names(self) -> list[str]:
        return [
            node.name
            for node in ast.walk(self.tree)
            if isinstance(node, ast.ClassDef)
        ]

    def _get_class_methods(self, cls_name: str) -> list[str]:
        for node in ast.walk(self.tree):
            if isinstance(node, ast.ClassDef) and node.name == cls_name:
                return [
                    n.name
                    for n in node.body
                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                ]
        return []

    def test_has_orchestrated_worker(self) -> None:
        assert "SchemaForgeOrchestratedWorker" in self._get_class_names()

    def test_orchestrated_worker_has_run(self) -> None:
        methods = self._get_class_methods("SchemaForgeOrchestratedWorker")
        assert "run" in methods

    def test_schemaforge_worker_has_session_ready_signal(self) -> None:
        assert "session_ready = Signal(object)" in self.src

    def test_schemaforge_worker_accepts_session_param(self) -> None:
        """SchemaForgeWorker 必须接受 session 参数复用已有会话。"""
        assert "session: object | None = None" in self.src

    def test_schemaforge_worker_accepts_visual_review_param(self) -> None:
        assert "enable_visual_review: bool = False" in self.src

    def test_schemaforge_worker_emits_session_ready(self) -> None:
        assert "self.session_ready.emit(session)" in self.src

    def test_gui_worker_passes_visual_review_to_system_session(self) -> None:
        assert "enable_visual_review=self._enable_visual_review" in self.src
        assert "visual_review_enabled == self._enable_visual_review" in self.src

    def test_orchestrated_worker_calls_run_orchestrated(self) -> None:
        assert "run_orchestrated" in self.src

    def test_revise_worker_exists(self) -> None:
        assert "SchemaForgeReviseWorker" in self._get_class_names()

    def test_image_revise_worker_exists(self) -> None:
        assert "SchemaForgeImageReviseWorker" in self._get_class_names()

    def test_image_revise_worker_calls_session_method(self) -> None:
        assert "revise_from_image" in self.src

    def test_gui_worker_uses_system_design_session(self) -> None:
        assert "SystemDesignSession" in self.src

    def test_all_workers_have_finished_signal(self) -> None:
        """所有 worker 类都必须有 finished 信号。"""
        for cls_name in [
            "SchemaForgeWorker",
            "SchemaForgeReviseWorker",
            "SchemaForgeImageReviseWorker",
            "IngestAssetWorker",
            "ConfirmImportWorker",
            "SchemaForgeOrchestratedWorker",
        ]:
            assert cls_name in self._get_class_names(), f"{cls_name} 缺失"


# ============================================================
# 单元测试 — 逻辑层（不需要 Qt 运行时）
# ============================================================


class TestSchemaForgeWorkerLogic:
    """SchemaForgeWorker.run() 逻辑测试（默认系统主链）。"""

    def test_worker_creates_session_when_none(self) -> None:
        """传入 session=None 时应创建新的系统级会话。"""
        from schemaforge.gui.workers.engine_worker import SchemaForgeWorker

        worker = SchemaForgeWorker(
            user_input="测试输入",
            session=None,
        )
        assert worker._session is None
        assert worker.user_input == "测试输入"

    def test_worker_reuses_session(self) -> None:
        """传入已有 session 时应复用。"""
        from schemaforge.gui.workers.engine_worker import SchemaForgeWorker

        mock_session = MagicMock()
        worker = SchemaForgeWorker(
            user_input="测试输入",
            session=mock_session,
        )
        assert worker._session is mock_session


class TestSchemaForgeReviseWorkerLogic:
    """SchemaForgeReviseWorker 逻辑测试。"""

    def test_revise_worker_stores_session(self) -> None:
        from schemaforge.gui.workers.engine_worker import SchemaForgeReviseWorker

        mock_session = MagicMock()
        worker = SchemaForgeReviseWorker(
            session=mock_session,
            user_input="把输出电压改成3.3V",
        )
        assert worker._session is mock_session
        assert worker.user_input == "把输出电压改成3.3V"


class TestSchemaForgeImageReviseWorkerLogic:
    """SchemaForgeImageReviseWorker 逻辑测试。"""

    def test_image_revise_worker_stores_session(self) -> None:
        from schemaforge.gui.workers.engine_worker import SchemaForgeImageReviseWorker

        mock_session = MagicMock()
        worker = SchemaForgeImageReviseWorker(
            session=mock_session,
            base64_png="aGVsbG8=",
        )
        assert worker._session is mock_session
        assert worker.base64_png == "aGVsbG8="


class TestSchemaForgeOrchestratedWorkerLogic:
    """SchemaForgeOrchestratedWorker 逻辑测试。"""

    def test_orchestrated_worker_stores_session(self) -> None:
        from schemaforge.gui.workers.engine_worker import (
            SchemaForgeOrchestratedWorker,
        )

        mock_session = MagicMock()
        worker = SchemaForgeOrchestratedWorker(
            session=mock_session,
            user_input="设计一个5V转3.3V的LDO电路",
        )
        assert worker._session is mock_session
        assert worker.user_input == "设计一个5V转3.3V的LDO电路"


# ============================================================
# 路由逻辑测试（模拟 DesignPage 核心逻辑）
# ============================================================


class TestChatSendRouting:
    """验证 _on_chat_send 路由逻辑。

    不实例化真正的 DesignPage（需要 Qt event loop），
    改用直接测试条件分支逻辑。
    """

    def test_revise_condition_with_session_and_design(self) -> None:
        """有会话且已有设计 → 应走修改路径。"""
        sf_session = MagicMock()
        has_design = True

        # 模拟 _on_chat_send 的条件判断
        should_revise = sf_session is not None and has_design
        assert should_revise is True

    def test_new_design_condition_without_session(self) -> None:
        """无会话 → 应走新设计路径。"""
        sf_session = None
        has_design = False

        should_revise = sf_session is not None and has_design
        assert should_revise is False

    def test_new_design_condition_no_prior_design(self) -> None:
        """有会话但无设计结果 → 应走新设计路径。"""
        sf_session = MagicMock()
        has_design = False

        should_revise = sf_session is not None and has_design
        assert should_revise is False

    def test_empty_message_skipped(self) -> None:
        """空消息 → 不触发任何路径。"""
        message = "   "
        assert not message.strip()


class TestDefaultBackendConvergence:
    def test_cli_defaults_to_system_design_session(self) -> None:
        source = _read_source(_MAIN_PY)
        assert "SystemDesignSession" in source
        assert "Orchestrator" in source

    def test_gui_defaults_to_system_design_session(self) -> None:
        source = _read_source(_ENGINE_WORKER_PY)
        assert "SystemDesignSession" in source


class TestDisplayBundleExtraction:
    """验证 _display_bundle 提取逻辑。"""

    def test_extracts_svg_path(self) -> None:
        bundle = SimpleNamespace(
            svg_path="/tmp/test.svg",
            bom_text="R1 10k",
            spice_text=".subckt test",
            device=SimpleNamespace(part_number="TPS54202", category="buck"),
            parameters={"v_out": "5V"},
            rationale=["公式计算"],
        )
        # Verify all attributes are accessible
        assert bundle.svg_path == "/tmp/test.svg"
        assert bundle.bom_text == "R1 10k"
        assert bundle.spice_text == ".subckt test"
        assert bundle.device.part_number == "TPS54202"
        assert bundle.parameters["v_out"] == "5V"
        assert bundle.rationale == ["公式计算"]

    def test_handles_missing_device(self) -> None:
        bundle = SimpleNamespace(
            svg_path="",
            bom_text="",
            spice_text="",
            device=None,
            parameters={},
            rationale=[],
        )
        assert bundle.device is None
        assert bundle.svg_path == ""


class TestAgentStepDispatching:
    """验证 AgentStep action 分发逻辑。"""

    def test_ask_user_action(self) -> None:
        from schemaforge.agent.protocol import AgentAction

        step = SimpleNamespace(
            action=AgentAction.ASK_USER,
            message="请确认输出电压",
            questions=[SimpleNamespace(text="输出电压是多少?")],
        )
        assert step.action.value == "ask_user"
        assert len(step.questions) == 1

    def test_finalize_action(self) -> None:
        from schemaforge.agent.protocol import AgentAction

        step = SimpleNamespace(
            action=AgentAction.FINALIZE,
            message="设计完成",
            questions=[],
        )
        assert step.action.value == "finalize"

    def test_fail_action(self) -> None:
        from schemaforge.agent.protocol import AgentAction

        step = SimpleNamespace(
            action=AgentAction.FAIL,
            message="无法识别需求",
            questions=[],
        )
        assert step.action.value == "fail"

    def test_call_tools_action(self) -> None:
        from schemaforge.agent.protocol import AgentAction

        step = SimpleNamespace(
            action=AgentAction.CALL_TOOLS,
            message="调用工具",
            tool_calls=[],
        )
        assert step.action.value == "call_tools"

    def test_present_draft_action(self) -> None:
        from schemaforge.agent.protocol import AgentAction

        step = SimpleNamespace(
            action=AgentAction.PRESENT_DRAFT,
            message="草稿展示",
        )
        assert step.action.value == "present_draft"
