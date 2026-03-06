"""设计会话工作流

串联 Phase 4 全部模块的端到端流程：
自然语言需求 → 规划 → 检索 → 适配 → 合理性检查 → 渲染 → 导出

用法::

    session = DesignSession(store_dir=Path("schemaforge/store"))
    result = session.run("5V转3.3V稳压电路，带LED指示灯")
    if result.success:
        print(result.svg_paths)
        print(result.bom_text)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from schemaforge.common.progress import ProgressTracker
from schemaforge.design.planner import DesignPlan, DesignPlanner, ModuleRequirement
from schemaforge.design.rationality import RationalityChecker, RationalityReport
from schemaforge.design.retrieval import DeviceRetriever, RetrievalResult
from schemaforge.design.topology_adapter import TopologyAdapter
from schemaforge.library.models import DeviceModel
from schemaforge.library.store import ComponentStore
from schemaforge.workflows.state_machine import (
    WorkflowStateMachine,
    create_design_session_sm,
)


# ============================================================
# 会话结果
# ============================================================

@dataclass
class ModuleResult:
    """单个模块的处理结果"""

    requirement: ModuleRequirement
    retrieval: RetrievalResult | None = None
    device: DeviceModel | None = None
    rationality: RationalityReport | None = None
    svg_path: str = ""
    bom_text: str = ""
    spice_text: str = ""
    error: str = ""

    @property
    def success(self) -> bool:
        return bool(self.device and not self.error and self.svg_path)

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.requirement.role,
            "category": self.requirement.category,
            "device": self.device.part_number if self.device else None,
            "score": self.retrieval.score if self.retrieval else 0,
            "svg_path": self.svg_path,
            "has_bom": bool(self.bom_text),
            "has_spice": bool(self.spice_text),
            "rationality_ok": (
                self.rationality.is_acceptable if self.rationality else None
            ),
            "error": self.error,
        }


@dataclass
class DesignSessionResult:
    """设计会话完整结果"""

    success: bool = False
    plan: DesignPlan | None = None
    modules: list[ModuleResult] = field(default_factory=list)
    svg_paths: list[str] = field(default_factory=list)
    bom_text: str = ""
    spice_text: str = ""
    design_spec: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    stage: str = ""             # 失败阶段

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "design_name": self.plan.name if self.plan else "",
            "module_count": len(self.modules),
            "svg_count": len(self.svg_paths),
            "modules": [m.to_dict() for m in self.modules],
            "error": self.error,
            "stage": self.stage,
        }


# ============================================================
# 设计会话
# ============================================================

class DesignSession:
    """设计会话

    端到端编排：需求 → SVG + BOM + SPICE

    流程:
    1. searching: 规划 (planner) + 检索 (retriever)
    2. planning: 将规划结果与库匹配
    3. validating: 合理性检查
    4. compiling: 拓扑适配
    5. rendering: SVG 渲染 + BOM/SPICE 导出
    """

    def __init__(
        self,
        store_dir: Path | str,
        use_mock: bool = True,
        progress_callback: Callable[[str, int], None] | None = None,
        tracker: ProgressTracker | None = None,
    ) -> None:
        self._store = ComponentStore(Path(store_dir))
        self._planner = DesignPlanner(use_mock=use_mock)
        self._retriever = DeviceRetriever(self._store)
        self._adapter = TopologyAdapter()
        self._checker = RationalityChecker()
        self._progress = progress_callback
        self._tracker = tracker
        self._sm: WorkflowStateMachine | None = None

    def _emit(self, message: str, percentage: int) -> None:
        if self._progress:
            self._progress(message, percentage)
        if self._tracker:
            self._tracker.log(message)

    def run(self, user_input: str) -> DesignSessionResult:
        """执行完整设计会话

        Args:
            user_input: 用户自然语言需求

        Returns:
            DesignSessionResult
        """
        result = DesignSessionResult()
        self._sm = create_design_session_sm()

        # === 阶段1: searching — 规划 ===
        self._emit("正在分析设计需求...", 5)
        self._sm.transition("searching", reason="开始规划")
        result.stage = "searching"

        try:
            plan = self._planner.plan(user_input)
            result.plan = plan
        except Exception as e:
            result.error = f"设计规划失败: {e}"
            self._sm.transition("error", reason=str(e))
            return result

        if not plan.modules:
            result.error = "规划器未识别出任何模块需求"
            self._sm.transition("error", reason="无模块")
            return result

        self._emit(f"识别出 {len(plan.modules)} 个模块需求", 15)

        # === 阶段2: planning — 检索匹配 ===
        self._emit("正在从器件库检索匹配器件...", 20)
        self._sm.transition("planning", reason="开始检索")
        result.stage = "planning"

        module_results: list[ModuleResult] = []
        for mod_req in plan.modules:
            mr = self._search_and_match(mod_req)
            module_results.append(mr)

        result.modules = module_results

        # 检查是否有任何模块匹配到器件
        matched = [m for m in module_results if m.device is not None]
        if not matched:
            result.error = "器件库中没有找到匹配的器件"
            self._sm.transition("error", reason="无匹配")
            return result

        self._emit(
            f"匹配到 {len(matched)}/{len(module_results)} 个器件", 35,
        )

        # === 阶段3: validating — 合理性检查 ===
        self._emit("正在执行合理性检查...", 40)
        self._sm.transition("validating", reason="开始检查")
        result.stage = "validating"

        has_blocking_error = False
        for mr in matched:
            assert mr.device is not None
            report = self._checker.check(mr.device, mr.requirement.parameters)
            mr.rationality = report
            if report.has_errors:
                has_blocking_error = True
                mr.error = report.summary()
                self._emit(f"⚠️ {mr.device.part_number}: {report.summary()}", 45)

        if has_blocking_error:
            # 有 error 级别问题，但不完全阻断（尝试渲染成功的模块）
            self._emit("部分模块存在合理性问题", 48)

        # === 阶段4: compiling — 拓扑适配 ===
        self._emit("正在适配拓扑...", 55)
        self._sm.transition("compiling", reason="开始适配")
        result.stage = "compiling"

        # 过滤：只处理通过合理性检查的模块
        renderable = [
            m for m in matched
            if m.rationality is None or m.rationality.is_acceptable
        ]

        if not renderable:
            result.error = "所有模块均未通过合理性检查"
            self._sm.transition("error", reason="全部不合理")
            return result

        # 构建 DesignSpec
        adapt_modules = []
        for mr in renderable:
            assert mr.device is not None
            adapt_modules.append((
                mr.device,
                mr.requirement.parameters,
                mr.requirement.role,
            ))

        adaptation = self._adapter.adapt_multi(
            modules=adapt_modules,
            design_name=plan.name,
            description=plan.description,
        )
        result.design_spec = adaptation.to_design_spec()

        self._emit("拓扑适配完成", 65)

        # === 阶段5: rendering — 渲染 + 导出 ===
        self._emit("正在渲染原理图 SVG...", 70)
        self._sm.transition("rendering", reason="开始渲染")
        result.stage = "rendering"

        svg_paths: list[str] = []
        bom_parts: list[str] = []
        spice_parts: list[str] = []

        for mr in renderable:
            assert mr.device is not None
            try:
                svg_path = self._adapter.render(
                    mr.device, mr.requirement.parameters,
                )
                mr.svg_path = svg_path
                svg_paths.append(svg_path)
            except Exception as e:
                mr.error = f"渲染失败: {e}"
                self._emit(f"渲染 {mr.device.part_number} 失败: {e}", 75)
                continue

            try:
                bom, spice = self._adapter.generate_exports(
                    mr.device, mr.requirement.parameters, mr.requirement.role,
                )
                mr.bom_text = bom
                mr.spice_text = spice
                bom_parts.append(bom)
                spice_parts.append(spice)
            except Exception as e:
                # BOM/SPICE 导出失败不阻断
                self._emit(f"导出 {mr.device.part_number} 失败: {e}", 78)

        result.svg_paths = svg_paths
        result.bom_text = "\n\n".join(bom_parts)
        result.spice_text = "\n\n".join(spice_parts)

        # === 完成 ===
        if svg_paths:
            self._sm.transition("done", reason="渲染完成")
            result.success = True
            result.stage = "done"
            self._emit(
                f"设计完成！生成 {len(svg_paths)} 个 SVG",
                100,
            )
        else:
            result.error = "没有成功渲染的模块"
            self._sm.transition("error", reason="渲染全失败")

        return result

    # ----------------------------------------------------------
    # 内部方法
    # ----------------------------------------------------------

    def _search_and_match(self, mod_req: ModuleRequirement) -> ModuleResult:
        """搜索并匹配单个模块"""
        mr = ModuleResult(requirement=mod_req)

        req = mod_req.to_device_requirement()
        best = self._retriever.get_best_match(req)

        if best is None:
            mr.error = f"器件库中没有找到匹配 {mod_req.category} 的器件"
            return mr

        mr.retrieval = best
        mr.device = best.device

        self._emit(
            f"匹配: {mod_req.role} → {best.device.part_number} "
            f"(得分: {best.score:.2f})",
            30,
        )
        return mr
