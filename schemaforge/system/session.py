"""系统级设计会话 (T091-T096)。

编排多器件设计的完整生命周期：
  意图解析 → 器件解析 → 连接规则 → 参数综合 → 实例收集 → 渲染 → 导出

支持：
- 全新设计（start）
- 修订（revise）
- 替换模块（replace_module）
- 增删模块（add_module / remove_module）
- AI / 非 AI 两种模式（skip_ai_parse 控制）
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from schemaforge.library.store import ComponentStore
from schemaforge.system.ai_protocol import parse_system_intent, regex_fallback_parse
from schemaforge.system.connection_rules import resolve_all_connections
from schemaforge.system.export_bom import (
    export_system_bom_csv,
    export_system_bom_markdown,
)
from schemaforge.system.export_spice import export_system_spice
from schemaforge.system.instances import (
    allocate_global_references,
    create_component_instances,
    stabilize_references_after_revision,
)
from schemaforge.system.models import (
    ModuleInstance,
    ModuleIntent,
    ModuleStatus,
    SystemBundle,
    SystemDesignIR,
    SystemDesignRequest,
)
from schemaforge.system.rendering import render_system_svg
from schemaforge.system.resolver import (
    instantiate_module_from_device,
    resolve_part_candidates,
)
from schemaforge.system.synthesis import (
    recompute_dependent_modules,
    synthesize_all_modules,
)

logger = logging.getLogger(__name__)


# ============================================================
# T091: SystemDesignResult
# ============================================================


@dataclass
class SystemDesignResult:
    """系统级设计结果。"""

    status: str  # "generated" / "needs_asset" / "partial" / "error"
    message: str
    bundle: SystemBundle | None = None
    missing_modules: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ============================================================
# T092-T096: SystemDesignSession
# ============================================================


class SystemDesignSession:
    """系统级设计会话。

    编排多器件设计从意图到 SVG 的完整管线。
    """

    def __init__(
        self,
        store_dir: Path | str,
        skip_ai_parse: bool | None = None,
    ) -> None:
        self._store = ComponentStore(Path(store_dir))
        self._skip_ai_parse = skip_ai_parse
        self._ir: SystemDesignIR | None = None
        self._bundle: SystemBundle | None = None
        self._prev_component_instances: list[object] = []

    @property
    def ir(self) -> SystemDesignIR | None:
        return self._ir

    @property
    def bundle(self) -> SystemBundle | None:
        return self._bundle

    # ----------------------------------------------------------
    # T093: start — 全新设计
    # ----------------------------------------------------------

    def start(self, user_input: str) -> SystemDesignResult:
        """完整管线：解析 → 解析器件 → 连接 → 综合 → 渲染 → 导出。"""
        if self._skip_ai_parse:
            request = regex_fallback_parse(user_input)
        else:
            request = parse_system_intent(user_input)
        return self._run_pipeline(request)

    def start_from_request(
        self, request: SystemDesignRequest,
    ) -> SystemDesignResult:
        """跳过 AI 解析，直接从 SystemDesignRequest 运行管线。

        用于测试和程序化调用。

        Args:
            request: 已构建的系统设计请求

        Returns:
            SystemDesignResult
        """
        return self._run_pipeline(request)

    # ----------------------------------------------------------
    # T093 核心: _run_pipeline
    # ----------------------------------------------------------

    def _run_pipeline(self, request: SystemDesignRequest) -> SystemDesignResult:
        """执行完整设计管线。

        Steps:
        1. 为每个 ModuleIntent 解析器件 → 实例化模块
        2. resolve_all_connections → 连接 + 网络
        3. synthesize_all_modules → 参数 + 外围元件
        4. create_component_instances + allocate_global_references
        5. render_system_svg → SVG
        6. export_system_bom_markdown → BOM
        7. export_system_spice → SPICE
        8. 组装 SystemBundle
        """
        warnings: list[str] = []
        missing_modules: list[str] = []

        # --- Step 1: 模块解析 ---
        module_instances: dict[str, ModuleInstance] = {}
        for intent in request.modules:
            instance = self._resolve_module(intent)
            module_instances[intent.intent_id] = instance
            if instance.status == ModuleStatus.NEEDS_ASSET:
                missing_modules.append(intent.intent_id)
                warnings.append(
                    f"模块 '{intent.intent_id}' 缺少器件 "
                    f"'{instance.missing_part_number}', 标记为 NEEDS_ASSET"
                )

        # 构建 IR
        ir = SystemDesignIR(
            request=request,
            module_instances=module_instances,
        )

        # --- Step 2: 连接解析 ---
        resolved_ids = {
            mid for mid, m in module_instances.items()
            if m.status in (ModuleStatus.RESOLVED, ModuleStatus.SYNTHESIZED)
        }
        # 过滤连接意图：仅保留两端模块都已解析的
        valid_intents = [
            c for c in request.connections
            if c.src_module_intent in resolved_ids
            and (c.dst_module_intent or "") in resolved_ids
        ]
        skipped_intents = [
            c for c in request.connections if c not in valid_intents
        ]
        for c in skipped_intents:
            warnings.append(
                f"连接 '{c.connection_id}' 跳过: 端模块未解析"
            )

        connections, nets, unresolved = resolve_all_connections(
            module_instances, valid_intents,
        )
        ir.connections = connections
        ir.nets = nets
        ir.unresolved_items.extend(unresolved)

        # --- Step 3: 综合 ---
        ir = synthesize_all_modules(ir)

        # --- Step 4: 实例收集 + 编号分配 ---
        comp_instances = create_component_instances(ir)
        if self._prev_component_instances:
            comp_instances = stabilize_references_after_revision(
                self._prev_component_instances, comp_instances,  # type: ignore[arg-type]
            )
        else:
            comp_instances = allocate_global_references(comp_instances)
        self._prev_component_instances = comp_instances  # type: ignore[assignment]

        # --- Step 5: 渲染 ---
        svg_path = ""
        try:
            svg_path = render_system_svg(ir)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"SVG 渲染失败: {exc}")
            logger.warning("SVG 渲染异常: %s", exc)

        # --- Step 6: BOM ---
        bom_text = ""
        bom_csv = ""
        try:
            bom_text = export_system_bom_markdown(comp_instances, ir)
            bom_csv = export_system_bom_csv(comp_instances)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"BOM 导出失败: {exc}")

        # --- Step 7: SPICE ---
        spice_text = ""
        try:
            spice_text = export_system_spice(ir, comp_instances)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"SPICE 导出失败: {exc}")

        # --- Step 8: 组装 Bundle ---
        bundle = SystemBundle(
            design_ir=ir,
            svg_path=svg_path,
            bom_text=bom_text,
            bom_csv=bom_csv,
            spice_text=spice_text,
        )

        # --- Step 8.5: 视觉审稿闭环 ---
        # 暂时禁用：patch 执行器尚未与 schemdraw 渲染参数联通，
        # 启用后只会增加延迟而不真正改善 SVG 布局。
        # 待 LayoutState → schemdraw 参数映射完成后重新启用。
        # if svg_path and ir.get_resolved_modules():
        #     try:
        #         from schemaforge.visual_review.loop import run_visual_review_loop
        #         bundle, trace = run_visual_review_loop(ir, bundle)
        #     except Exception:
        #         pass

        self._ir = ir
        self._bundle = bundle

        # 判断结果状态
        if missing_modules and not ir.get_resolved_modules():
            status = "error"
            message = "所有模块均缺少器件，无法生成设计"
        elif missing_modules:
            status = "partial"
            message = (
                f"部分设计已完成，{len(missing_modules)} 个模块缺少器件: "
                + ", ".join(missing_modules)
            )
        else:
            status = "generated"
            message = (
                f"设计已生成: {len(ir.get_resolved_modules())} 个模块, "
                f"{len(ir.connections)} 条连接"
            )

        ir.warnings.extend(warnings)

        return SystemDesignResult(
            status=status,
            message=message,
            bundle=bundle,
            missing_modules=missing_modules,
            warnings=warnings,
        )

    # ----------------------------------------------------------
    # T094: revise
    # ----------------------------------------------------------

    def revise(self, user_input: str) -> SystemDesignResult:
        """修订已有设计。

        解析修订意图，识别目标模块，应用变更，重新综合。
        """
        if self._ir is None:
            return SystemDesignResult(
                status="error",
                message="当前没有可修改的设计，请先调用 start()。",
            )

        # 解析修订文本
        if self._skip_ai_parse:
            revision_request = regex_fallback_parse(user_input)
        else:
            revision_request = parse_system_intent(user_input)

        # 将修订意图中的模块参数合并到现有 IR
        changed_ids: set[str] = set()

        for intent in revision_request.modules:
            existing = self._ir.get_module(intent.intent_id)
            if existing is not None:
                # 更新参数
                if intent.electrical_targets:
                    existing.parameters.update(intent.electrical_targets)
                    changed_ids.add(intent.intent_id)
                    existing.status = ModuleStatus.RESOLVED
            else:
                # 新模块 → 走 add 路径
                instance = self._resolve_module(intent)
                self._ir.module_instances[intent.intent_id] = instance
                changed_ids.add(intent.intent_id)

        # 合并新连接
        if revision_request.connections:
            new_conns, new_nets, new_unresolved = resolve_all_connections(
                self._ir.module_instances, revision_request.connections,
            )
            self._ir.connections.extend(new_conns)
            self._ir.nets.update(new_nets)
            self._ir.unresolved_items.extend(new_unresolved)

        # 重新综合受影响子图
        if changed_ids:
            self._ir = recompute_dependent_modules(self._ir, changed_ids)
            # 也重新综合直接变更的模块
            self._ir = synthesize_all_modules(self._ir)

        # 重新生成输出
        return self._regenerate_outputs("修订完成")

    # ----------------------------------------------------------
    # T095: replace_module
    # ----------------------------------------------------------

    def replace_module(
        self, module_id: str, new_part_number: str,
    ) -> SystemDesignResult:
        """替换特定模块的器件。"""
        if self._ir is None:
            return SystemDesignResult(
                status="error",
                message="当前没有可修改的设计，请先调用 start()。",
            )

        existing = self._ir.get_module(module_id)
        if existing is None:
            return SystemDesignResult(
                status="error",
                message=f"模块 '{module_id}' 不存在。",
            )

        # 解析新器件
        device = self._store.get_device(new_part_number)
        if device is None:
            return SystemDesignResult(
                status="needs_asset",
                message=f"器件 '{new_part_number}' 不在库中。",
                missing_modules=[module_id],
            )

        # 用原有意图参数构建新的 intent
        intent = ModuleIntent(
            intent_id=module_id,
            role=existing.role,
            part_number_hint=new_part_number,
            category_hint=existing.resolved_category,
            electrical_targets=dict(existing.parameters),
        )

        # 重新实例化
        new_instance = instantiate_module_from_device(intent, device)
        new_instance.parameters.update(existing.parameters)
        self._ir.module_instances[module_id] = new_instance

        # 重新综合
        self._ir = recompute_dependent_modules(self._ir, {module_id})
        self._ir = synthesize_all_modules(self._ir)

        return self._regenerate_outputs(
            f"模块 '{module_id}' 已替换为 {new_part_number}"
        )

    # ----------------------------------------------------------
    # T096: add_module / remove_module
    # ----------------------------------------------------------

    def add_module(self, intent: ModuleIntent) -> SystemDesignResult:
        """添加新模块到系统。"""
        if self._ir is None:
            return SystemDesignResult(
                status="error",
                message="当前没有可修改的设计，请先调用 start()。",
            )

        if intent.intent_id in self._ir.module_instances:
            return SystemDesignResult(
                status="error",
                message=f"模块 '{intent.intent_id}' 已存在。",
            )

        instance = self._resolve_module(intent)
        self._ir.module_instances[intent.intent_id] = instance

        # 将意图追加到 request
        self._ir.request.modules.append(intent)

        # 重新综合
        self._ir = synthesize_all_modules(self._ir)

        status = "generated"
        missing: list[str] = []
        if instance.status == ModuleStatus.NEEDS_ASSET:
            status = "partial"
            missing = [intent.intent_id]

        result = self._regenerate_outputs(
            f"已添加模块 '{intent.intent_id}'"
        )
        result.status = status
        result.missing_modules = missing
        return result

    def remove_module(self, module_id: str) -> SystemDesignResult:
        """移除模块。"""
        if self._ir is None:
            return SystemDesignResult(
                status="error",
                message="当前没有可修改的设计，请先调用 start()。",
            )

        if module_id not in self._ir.module_instances:
            return SystemDesignResult(
                status="error",
                message=f"模块 '{module_id}' 不存在。",
            )

        # 删除模块
        del self._ir.module_instances[module_id]

        # 删除涉及该模块的连接
        self._ir.connections = [
            c for c in self._ir.connections
            if c.src_port.module_id != module_id
            and c.dst_port.module_id != module_id
        ]

        # 从网络中移除该模块的端口
        for net in self._ir.nets.values():
            net.members = [
                m for m in net.members if m.module_id != module_id
            ]

        # 从 request 中移除
        self._ir.request.modules = [
            m for m in self._ir.request.modules
            if m.intent_id != module_id
        ]
        self._ir.request.connections = [
            c for c in self._ir.request.connections
            if c.src_module_intent != module_id
            and (c.dst_module_intent or "") != module_id
        ]

        return self._regenerate_outputs(f"已移除模块 '{module_id}'")

    # ----------------------------------------------------------
    # 内部辅助
    # ----------------------------------------------------------

    def _resolve_module(self, intent: ModuleIntent) -> ModuleInstance:
        """解析单个模块意图 → ModuleInstance。

        尝试按 part_number_hint / category_hint 在库中查找器件。
        找不到则标记为 NEEDS_ASSET。
        """
        candidates = resolve_part_candidates(self._store, intent)

        if candidates:
            device = candidates[0]
            instance = instantiate_module_from_device(intent, device)
            return instance

        # 未命中：创建 NEEDS_ASSET 占位实例
        return ModuleInstance(
            module_id=intent.intent_id,
            role=intent.role,
            resolved_category=intent.category_hint,
            parameters=dict(intent.electrical_targets),
            status=ModuleStatus.NEEDS_ASSET,
            missing_part_number=intent.part_number_hint or intent.category_hint,
            warnings=[
                f"器件未命中: part='{intent.part_number_hint}', "
                f"category='{intent.category_hint}'"
            ],
        )

    def _regenerate_outputs(self, message: str) -> SystemDesignResult:
        """从现有 IR 重新生成渲染/导出。"""
        assert self._ir is not None
        warnings: list[str] = []

        # 实例收集
        comp_instances = create_component_instances(self._ir)
        if self._prev_component_instances:
            comp_instances = stabilize_references_after_revision(
                self._prev_component_instances, comp_instances,  # type: ignore[arg-type]
            )
        else:
            comp_instances = allocate_global_references(comp_instances)
        self._prev_component_instances = comp_instances  # type: ignore[assignment]

        # 渲染
        svg_path = ""
        try:
            svg_path = render_system_svg(self._ir)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"SVG 渲染失败: {exc}")

        # BOM
        bom_text = ""
        bom_csv = ""
        try:
            bom_text = export_system_bom_markdown(comp_instances, self._ir)
            bom_csv = export_system_bom_csv(comp_instances)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"BOM 导出失败: {exc}")

        # SPICE
        spice_text = ""
        try:
            spice_text = export_system_spice(self._ir, comp_instances)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"SPICE 导出失败: {exc}")

        bundle = SystemBundle(
            design_ir=self._ir,
            svg_path=svg_path,
            bom_text=bom_text,
            bom_csv=bom_csv,
            spice_text=spice_text,
        )
        self._bundle = bundle

        missing = [
            m.module_id for m in self._ir.get_unresolved_modules()
        ]
        status = "partial" if missing else "generated"

        return SystemDesignResult(
            status=status,
            message=message,
            bundle=bundle,
            missing_modules=missing,
            warnings=warnings,
        )
