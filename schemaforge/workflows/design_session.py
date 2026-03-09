"""[LEGACY] 旧版设计会话，仅用于测试兼容。生产代码使用 SystemDesignSession。

串联 Phase 4 全部模块的端到端流程：
自然语言需求 → 规划 → 检索 → 适配 → 合理性检查 → 渲染 → 导出

同时构建 Design IR（中间真值），支持后续多轮修改、快照回滚、审查等能力。

用法::

    session = DesignSession(store_dir=Path("schemaforge/store"))
    result = session.run("5V转3.3V稳压电路，带LED指示灯")
    if result.success:
        print(result.svg_paths)
        print(result.bom_text)

    # 获取 Design IR
    ir = session.ir
    print(ir.to_summary())
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, ClassVar

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
    solver_result: Any | None = None  # SolverResult — 多候选方案求解结果
    review_result: Any | None = None  # ModuleReview (from DesignReviewEngine)
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
            "solver_candidates": (
                len(self.solver_result.candidates)
                if self.solver_result is not None
                else 0
            ),
            "review_passed": (
                self.review_result.passed if self.review_result is not None else None
            ),
            "error": self.error,
        }


# MissingModule 已迁移至 schemaforge.common.models，此处保留向后兼容重导出
from schemaforge.common.models import MissingModule as MissingModule  # noqa: E402, F401


@dataclass
class DesignSessionResult:
    """设计会话完整结果"""

    success: bool = False
    plan: DesignPlan | None = None
    modules: list[ModuleResult] = field(default_factory=list)
    missing_modules: list[MissingModule] = field(default_factory=list)
    svg_paths: list[str] = field(default_factory=list)
    bom_text: str = ""
    spice_text: str = ""
    design_spec: dict[str, Any] = field(default_factory=dict)
    reference_design: Any | None = None  # ReferenceDesign — 匹配的参考设计
    design_review: Any | None = None  # DesignReview (from DesignReviewEngine)
    error: str = ""
    stage: str = ""

    @property
    def has_missing(self) -> bool:
        """是否存在缺失器件需要用户补录"""
        return len(self.missing_modules) > 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "design_name": self.plan.name if self.plan else "",
            "module_count": len(self.modules),
            "svg_count": len(self.svg_paths),
            "modules": [m.to_dict() for m in self.modules],
            "missing_modules": [m.to_dict() for m in self.missing_modules],
            "has_reference_design": self.reference_design is not None,
            "design_review_passed": (
                self.design_review.overall_passed
                if self.design_review is not None
                else None
            ),
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

    LEGACY_COMPAT_ONLY: ClassVar[bool] = True

    def __init__(
        self,
        store_dir: Path | str,
        progress_callback: Callable[[str, int], None] | None = None,
        tracker: ProgressTracker | None = None,
    ) -> None:
        self._store = ComponentStore(Path(store_dir))
        self._planner = DesignPlanner()
        self._retriever = DeviceRetriever(self._store)
        self._adapter = TopologyAdapter()
        self._checker = RationalityChecker()
        self._progress = progress_callback
        self._tracker = tracker
        self._sm: WorkflowStateMachine | None = None
        self._ir: Any = None
        self._ir_history: Any = None
        self._clarification: Any = None

    @property
    def ir(self) -> Any:
        """当前设计的 IR（Design IR 中间真值）"""
        return self._ir

    @property
    def ir_history(self) -> Any:
        """IR 快照历史管理器"""
        if self._ir_history is None:
            from schemaforge.design.ir import IRHistory

            self._ir_history = IRHistory()
        return self._ir_history

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

        from schemaforge.design.clarifier import RequirementClarifier

        clarifier = RequirementClarifier()
        self._clarification = clarifier.clarify(user_input, plan)

        # === 阶段2: planning — 检索匹配 + 候选方案求解 ===
        self._emit("正在从器件库检索匹配器件...", 20)
        self._sm.transition("planning", reason="开始检索")
        result.stage = "planning"

        from schemaforge.design.candidate_solver import CandidateSolver

        solver = CandidateSolver(self._store)

        module_results: list[ModuleResult] = []
        missing: list[MissingModule] = []
        for mod_req in plan.modules:
            mr = self._search_and_match(mod_req)
            if mr.device is not None:
                mr.solver_result = solver.solve(mod_req, max_candidates=3)
            else:
                # 记录缺失器件，供 GUI 展示待办卡片
                missing.append(MissingModule(
                    role=mod_req.role,
                    category=mod_req.category,
                    description=mod_req.description,
                    part_number=mod_req.part_number,
                    parameters=dict(mod_req.parameters),
                    search_error=mr.error,
                ))
            module_results.append(mr)

        result.modules = module_results
        result.missing_modules = missing

        matched = [m for m in module_results if m.device is not None]

        # 存在缺失器件时：返回部分结果 + 缺失列表，不视为全局失败
        if missing:
            self._emit(
                f"缺失 {len(missing)} 个器件，需要用户补录后继续",
                30,
            )

        if not matched:
            if missing:
                # 全部缺失 — 仍然返回结果（带 missing_modules），不设 error
                result.stage = "waiting_devices"
                self._sm.transition("error", reason="全部缺失，等待用户补录")
                return result
            result.error = "器件库中没有找到匹配的器件"
            self._sm.transition("error", reason="无匹配")
            return result

        self._emit(
            f"匹配到 {len(matched)}/{len(module_results)} 个器件",
            35,
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
            self._emit("部分模块存在合理性问题", 48)

        # --- 设计审查引擎（深度工程审查） ---
        from schemaforge.design.review import DesignReviewEngine, ModuleReviewInput

        review_engine = DesignReviewEngine()
        review_inputs: list[ModuleReviewInput] = []
        for mr in matched:
            assert mr.device is not None
            review_input = ModuleReviewInput(
                role=mr.requirement.role,
                category=mr.requirement.category,
                device=mr.device,
                parameters=mr.requirement.parameters,
            )
            review_inputs.append(review_input)
            mr.review_result = review_engine.review_module(review_input)

        if review_inputs:
            result.design_review = review_engine.review_design(review_inputs)
            self._emit("设计审查完成", 52)

        # === 阶段4: compiling — 拓扑适配 ===
        self._emit("正在适配拓扑...", 55)
        self._sm.transition("compiling", reason="开始适配")
        result.stage = "compiling"

        # 过滤：只处理通过合理性检查的模块
        renderable = [
            m for m in matched if m.rationality is None or m.rationality.is_acceptable
        ]

        if not renderable:
            result.error = "所有模块均未通过合理性检查"
            self._sm.transition("error", reason="全部不合理")
            return result

        # 构建 DesignSpec
        adapt_modules = []
        for mr in renderable:
            assert mr.device is not None
            adapt_modules.append(
                (
                    mr.device,
                    mr.requirement.parameters,
                    mr.requirement.role,
                )
            )

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
                    mr.device,
                    mr.requirement.parameters,
                )
                mr.svg_path = svg_path
                svg_paths.append(svg_path)
            except Exception as e:
                mr.error = f"渲染失败: {e}"
                self._emit(f"渲染 {mr.device.part_number} 失败: {e}", 75)
                continue

            try:
                bom, spice = self._adapter.generate_exports(
                    mr.device,
                    mr.requirement.parameters,
                    mr.requirement.role,
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

        # --- 参考设计匹配 ---
        from schemaforge.library.reference_models import ReferenceDesignStore

        ref_store_dir = Path(self._store.store_dir) / "reference_designs"
        if ref_store_dir.exists():
            ref_store = ReferenceDesignStore(ref_store_dir)
            categories = [m.requirement.category for m in module_results if m.device]
            roles = [m.requirement.role for m in module_results if m.device]
            result.reference_design = ref_store.find_best_match(categories, roles)

        # === 构建 Design IR ===
        self._ir = self._build_ir(user_input, result, plan)
        self.ir_history.save(self._ir, f"v{self._ir.version}")

        return result

    # ----------------------------------------------------------
    # 内部方法
    # ----------------------------------------------------------

    def _build_ir(
        self,
        user_input: str,
        result: DesignSessionResult,
        plan: DesignPlan,
    ) -> Any:
        """从设计结果构建 Design IR"""
        from schemaforge.design.ir import (
            Assumption,
            CandidateDevice,
            Constraint,
            ConstraintPriority,
            DerivedParameters,
            DesignIR,
            DesignIntent,
            DesignOutputs,
            DesignReview,
            DeviceSelection,
            ModuleIR,
            ModuleIntent,
            ModuleReview,
            ReviewIssue,
            ReviewSeverity,
            TopologyIR,
        )

        clarification = self._clarification
        design_mode_assumption = Assumption(
            field="design_mode",
            assumed_value="ai",
            reason="规划器模式",
            risk="",
        )
        if clarification is not None:
            known_constraints = clarification.known_constraints
            assumptions = [design_mode_assumption] + clarification.assumptions
            unresolved_questions = (
                clarification.missing_required + clarification.optional_preferences
            )
            confidence = clarification.confidence
        else:
            known_constraints = [
                Constraint(
                    name=k,
                    value=v,
                    priority=ConstraintPriority.REQUIRED,
                    source="user",
                )
                for mod in plan.modules
                for k, v in mod.parameters.items()
            ]
            assumptions = [design_mode_assumption]
            unresolved_questions = []
            confidence = 0.8 if result.success else 0.3

        ir = DesignIR(
            intent=DesignIntent(
                raw_input=user_input,
                summary=plan.description or plan.name,
                known_constraints=known_constraints,
                assumptions=assumptions,
                unresolved_questions=unresolved_questions,
                confidence=confidence,
            ),
            stage=result.stage,
            success=result.success,
            error=result.error,
        )

        for mr in result.modules:
            module_ir = ModuleIR(
                intent=ModuleIntent(
                    role=mr.requirement.role,
                    category=mr.requirement.category,
                    description=mr.requirement.description,
                    target_specs=mr.requirement.parameters,
                    depends_on=mr.requirement.connections_to,
                ),
                svg_path=mr.svg_path,
                bom_text=mr.bom_text,
                spice_text=mr.spice_text,
                error=mr.error,
            )

            if mr.device is not None:
                if mr.solver_result is not None and mr.solver_result.candidates:
                    candidates = [
                        CandidateDevice(
                            part_number=c.device.part_number,
                            manufacturer=c.device.manufacturer,
                            score=c.total_score,
                            match_reasons=[
                                f"{s.name}: {s.score:.2f}" for s in c.scores
                            ],
                            tradeoff_notes=c.tradeoff_notes,
                        )
                        for c in mr.solver_result.candidates
                    ]
                    selection_reason = (
                        mr.solver_result.recommendation_reason or "多候选方案评分推荐"
                    )
                else:
                    candidates = [
                        CandidateDevice(
                            part_number=mr.device.part_number,
                            manufacturer=mr.device.manufacturer,
                            score=mr.retrieval.score if mr.retrieval else 0.0,
                            match_reasons=(
                                mr.retrieval.match_reasons if mr.retrieval else []
                            ),
                        ),
                    ]
                    selection_reason = "最佳匹配"
                module_ir.selection = DeviceSelection(
                    selected=candidates[0],
                    candidates=candidates,
                    selection_reason=selection_reason,
                )

            if mr.device is not None:
                module_ir.parameters = DerivedParameters(
                    input_params=mr.requirement.parameters,
                    render_params=mr.requirement.parameters,
                )

            if mr.review_result is not None:
                module_ir.review = mr.review_result
            elif mr.rationality is not None:
                review_issues = [
                    ReviewIssue(
                        severity=(
                            ReviewSeverity.BLOCKING
                            if issue.severity == "error"
                            else ReviewSeverity.WARNING
                        ),
                        rule_id=issue.rule_id,
                        message=issue.message,
                        suggestion=issue.suggestion,
                        evidence=issue.evidence,
                        module_role=mr.requirement.role,
                    )
                    for issue in mr.rationality.issues
                ]
                module_ir.review = ModuleReview(
                    issues=review_issues,
                    passed=mr.rationality.is_acceptable,
                )

            ir.modules.append(module_ir)

        ir.topology = TopologyIR(
            design_spec=result.design_spec,
        )

        ir.outputs = DesignOutputs(
            svg_paths=result.svg_paths,
            bom_text=result.bom_text,
            spice_text=result.spice_text,
        )

        if result.design_review is not None:
            ir.review = result.design_review
        else:
            all_review_issues: list[ReviewIssue] = []
            for m in ir.modules:
                all_review_issues.extend(m.review.issues)
            ir.review = DesignReview(
                issues=all_review_issues,
                overall_passed=result.success,
            )

        return ir

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
