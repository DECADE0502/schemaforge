"""AI 原子工具集 v3 — 细粒度工具，AI 真正控制每一步。

v2 的 start_system_design 是黑盒（内含完整管线 + 冗余 AI 调用），
v3 把管线拆成原子工具，AI 自己编排调用顺序：

1. resolve_modules    — AI 提交模块意图列表，本地查器件库
2. resolve_connections — AI 提交连接意图列表，本地规则引擎解析
3. synthesize_parameters — 本地公式引擎计算所有外围元件参数
4. render_schematic   — 本地渲染引擎生成 SVG
5. export_outputs     — 本地导出 BOM + SPICE

辅助工具（保留）：
- search_device_library — 查器件库
- get_design_status    — 查看当前 IR 状态
- review_design        — 工程审查规则检查
- revise_module_param  — 修改单个模块参数后重新综合
"""

from __future__ import annotations

import logging
from typing import Any

from schemaforge.agent.tool_registry import ToolRegistry, ToolResult
from schemaforge.common.errors import ErrorCode, ToolError
from schemaforge.system.session import SystemDesignSession

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# AI Agent System Prompt — AI 真正控制每一步
# ------------------------------------------------------------------

AGENT_SYSTEM_PROMPT = """\
你是 SchemaForge 电路设计 AI 助手。你通过调用工具来完成电路设计。

## 你的工作流程

1. **理解需求**：从用户自然语言中提取出模块意图（器件型号、类别、电气参数、连接关系）。
2. **调用 resolve_modules**：提交结构化的模块意图列表，本地器件库会查找匹配的器件。
3. **调用 resolve_connections**：提交连接意图列表（电源链、GPIO、SPI等），本地规则引擎解析为引脚级连接。
4. **调用 synthesize_parameters**：本地公式引擎计算所有外围元件参数（电感、电容、分压电阻等）。
5. **调用 render_schematic**：渲染原理图 SVG。
6. **调用 export_outputs**：导出 BOM 清单和 SPICE 网表。
7. **调用 review_design**：运行工程审查规则，检查设计是否合理。
8. **总结结果**给用户。

## 关键规则

- 用户指定了器件型号时，`part_number_hint` 必须精确保留（如 "TPS54202"、"AMS1117-3.3"）。
- 器件库查不到时（status=needs_asset），告知用户缺失哪些器件，让用户上传 datasheet。
- intent_id 命名规则：类别+序号，如 buck1, ldo1, mcu1, led1, boost1。
- category_hint 可选值：buck, ldo, boost, mcu, led, resistor, capacitor, diode, connector。
- signal_type 可选值：power_supply, gpio, spi, i2c, uart, analog, enable, feedback, other。
- connection_semantics 可选值：supply_chain, gpio_drive, bus_connect, enable_control, ground_tie。
- 电源链上下游关系：buck/boost 的 VOUT 接下一级的 VIN，用 signal_type=power_supply + connection_semantics=supply_chain。
- MCU 控制 LED：用 signal_type=gpio + connection_semantics=gpio_drive。
- 不要输出电阻/电容/电感的具体数值（由本地公式引擎在 synthesize_parameters 中计算）。
- 不要输出原理图坐标或 SVG 结构（由本地渲染引擎在 render_schematic 中生成）。

## 辅助工具

- search_device_library：不确定器件库里有什么时可先搜索。
- get_design_status：查看当前设计状态。
- revise_module_param：修改某个模块的参数（如改输出电压），修改后需重新 render + export。

用中文回复用户。
"""


# ------------------------------------------------------------------
# 辅助：序列化
# ------------------------------------------------------------------


def _serialize_module_instance(mid: str, inst: object) -> dict[str, Any]:
    """ModuleInstance → 简单 dict 供 AI 阅读。"""
    device = getattr(inst, "device", None)
    part_number = getattr(device, "part_number", "") if device else ""
    status = getattr(inst, "status", "")
    status_val = status.value if hasattr(status, "value") else str(status)
    return {
        "module_id": mid,
        "role": getattr(inst, "role", ""),
        "category": getattr(inst, "resolved_category", ""),
        "part_number": part_number,
        "status": status_val,
        "parameters": dict(getattr(inst, "parameters", {})),
        "missing_part_number": getattr(inst, "missing_part_number", ""),
        "warnings": list(getattr(inst, "warnings", [])),
    }


def _serialize_connection(conn: object) -> dict[str, str]:
    """ResolvedConnection → 简单 dict。"""
    src = getattr(conn, "src_port", None)
    dst = getattr(conn, "dst_port", None)
    return {
        "id": getattr(conn, "resolved_connection_id", ""),
        "src": f"{getattr(src, 'module_id', '')}.{getattr(src, 'pin_name', '')}"
        if src else "",
        "dst": f"{getattr(dst, 'module_id', '')}.{getattr(dst, 'pin_name', '')}"
        if dst else "",
        "net": getattr(conn, "net_name", ""),
        "rule": getattr(conn, "rule_id", ""),
    }


# ------------------------------------------------------------------
# 主入口
# ------------------------------------------------------------------


def build_atomic_design_tools(session: SystemDesignSession) -> ToolRegistry:
    """为指定 SystemDesignSession 构建原子级设计工具集。"""
    registry = ToolRegistry()

    # ================================================================
    # 工具 1: resolve_modules — 器件查找
    # ================================================================
    def _handle_resolve_modules(modules: list[dict[str, Any]]) -> ToolResult:
        """接收 AI 解析的模块意图列表，逐个在器件库中查找。

        AI 负责从用户自然语言中提取出结构化的模块意图，
        本地负责在器件库中查找匹配的器件并实例化。
        """
        try:
            from schemaforge.system.models import (
                ModuleIntent,
                ModuleStatus,
                SystemDesignIR,
                SystemDesignRequest,
            )
            from schemaforge.system.resolver import (
                instantiate_module_from_device,
                resolve_part_candidates,
            )

            module_intents: list[ModuleIntent] = []
            for m in modules:
                intent = ModuleIntent(
                    intent_id=m.get("intent_id", ""),
                    role=m.get("role", ""),
                    part_number_hint=m.get("part_number_hint", ""),
                    category_hint=m.get("category_hint", ""),
                    electrical_targets=m.get("electrical_targets", {}),
                    control_targets=m.get("control_targets", {}),
                    placement_hint=m.get("placement_hint", ""),
                    priority=m.get("priority", 0),
                )
                module_intents.append(intent)

            # 逐个解析
            results: list[dict[str, Any]] = []
            module_instances: dict[str, Any] = {}
            missing: list[str] = []

            for intent in module_intents:
                candidates = resolve_part_candidates(session._store, intent)
                if candidates:
                    instance = instantiate_module_from_device(intent, candidates[0])
                else:
                    from schemaforge.system.models import ModuleInstance as MI
                    instance = MI(
                        module_id=intent.intent_id,
                        role=intent.role,
                        resolved_category=intent.category_hint,
                        parameters=dict(intent.electrical_targets),
                        status=ModuleStatus.NEEDS_ASSET,
                        missing_part_number=(
                            intent.part_number_hint or intent.category_hint
                        ),
                        warnings=[
                            f"器件未命中: part='{intent.part_number_hint}', "
                            f"category='{intent.category_hint}'"
                        ],
                    )

                module_instances[intent.intent_id] = instance
                results.append(
                    _serialize_module_instance(intent.intent_id, instance)
                )
                if instance.status == ModuleStatus.NEEDS_ASSET:
                    display = instance.missing_part_number or intent.intent_id
                    missing.append(display)

            # 构建初始 IR（无连接，无综合）并存入 session
            request = SystemDesignRequest(
                raw_text="",  # AI 已做解析，无需存原文
                modules=module_intents,
            )
            ir = SystemDesignIR(
                request=request,
                module_instances=module_instances,
            )
            session._ir = ir

            return ToolResult(
                success=True,
                data={
                    "modules": results,
                    "resolved_count": sum(
                        1 for r in results if r["status"] == "resolved"
                    ),
                    "missing_count": len(missing),
                    "missing_modules": missing,
                },
            )
        except Exception as exc:
            logger.exception("resolve_modules failed")
            return ToolResult(
                success=False,
                error=ToolError(
                    code=ErrorCode.ENGINE_FAILED,
                    message=f"模块解析失败: {exc}",
                    retriable=True,
                ),
            )

    registry.register_fn(
        name="resolve_modules",
        description=(
            "接收模块意图列表，在器件库中查找匹配器件并实例化。"
            "必须在 resolve_connections 之前调用。"
        ),
        handler=_handle_resolve_modules,
        parameters_schema={
            "modules": {
                "type": "array",
                "description": (
                    "模块意图列表，每个元素: "
                    '{"intent_id": "buck1", "role": "降压", '
                    '"part_number_hint": "TPS54202", "category_hint": "buck", '
                    '"electrical_targets": {"v_in": "20", "v_out": "5"}}'
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "intent_id": {
                            "type": "string",
                            "description": "模块唯一ID: buck1, ldo1, mcu1, led1",
                        },
                        "role": {
                            "type": "string",
                            "description": "模块功能描述: 第一级降压, 主控",
                        },
                        "part_number_hint": {
                            "type": "string",
                            "description": "用户指定的器件型号（精确保留）",
                        },
                        "category_hint": {
                            "type": "string",
                            "description": "器件类别: buck/ldo/mcu/led/boost",
                        },
                        "electrical_targets": {
                            "type": "object",
                            "description": "电气参数目标: {v_in, v_out, i_out}",
                        },
                        "control_targets": {
                            "type": "object",
                            "description": "控制参数: {gpio_pin, drive_mode}",
                        },
                        "placement_hint": {
                            "type": "string",
                            "description": "放置提示: power_chain / control_side",
                        },
                    },
                    "required": ["intent_id", "role", "category_hint"],
                },
            },
        },
        required_params=["modules"],
        category="design",
    )

    # ================================================================
    # 工具 2: resolve_connections — 连接解析
    # ================================================================
    def _handle_resolve_connections(
        connections: list[dict[str, Any]],
    ) -> ToolResult:
        """接收 AI 解析的连接意图列表，用规则引擎解析为引脚级连接。"""
        try:
            if session._ir is None:
                return ToolResult(
                    success=False,
                    error=ToolError(
                        code=ErrorCode.DESIGN_INVALID,
                        message="请先调用 resolve_modules 创建模块。",
                    ),
                )

            from schemaforge.system.connection_rules import resolve_all_connections
            from schemaforge.system.models import (
                ConnectionIntent,
                ConnectionSemantic,
                ModuleStatus,
                SignalType,
            )

            ir = session._ir

            # 构建 ConnectionIntent 列表
            conn_intents: list[ConnectionIntent] = []
            for c in connections:
                signal_type_str = c.get("signal_type", "other")
                semantics_str = c.get("connection_semantics", "unknown")
                try:
                    signal_type = SignalType(signal_type_str)
                except ValueError:
                    signal_type = SignalType.OTHER
                try:
                    semantics = ConnectionSemantic(semantics_str)
                except ValueError:
                    semantics = ConnectionSemantic.UNKNOWN

                intent = ConnectionIntent(
                    connection_id=c.get("connection_id", ""),
                    src_module_intent=c.get("src_module", ""),
                    src_port_hint=c.get("src_port_hint", ""),
                    dst_module_intent=c.get("dst_module", ""),
                    dst_port_hint=c.get("dst_port_hint", ""),
                    signal_type=signal_type,
                    connection_semantics=semantics,
                    confidence=c.get("confidence", 1.0),
                )
                conn_intents.append(intent)

            # 保存到 request 里供后续 revise 使用
            ir.request.connections = conn_intents

            # 过滤：仅保留两端都已解析的连接
            resolved_ids = {
                mid for mid, m in ir.module_instances.items()
                if m.status in (ModuleStatus.RESOLVED, ModuleStatus.SYNTHESIZED)
            }
            valid = [
                c for c in conn_intents
                if c.src_module_intent in resolved_ids
                and (c.dst_module_intent or "") in resolved_ids
            ]
            skipped = [c for c in conn_intents if c not in valid]

            resolved_conns, nets, unresolved = resolve_all_connections(
                ir.module_instances, valid,
            )
            ir.connections = resolved_conns
            ir.nets = nets
            ir.unresolved_items.extend(unresolved)

            # 序列化结果
            conn_results = [_serialize_connection(c) for c in resolved_conns]
            net_results = [
                {
                    "net_id": n.net_id,
                    "net_name": n.net_name,
                    "net_type": n.net_type.value
                    if hasattr(n.net_type, "value") else str(n.net_type),
                    "member_count": len(n.members),
                }
                for n in nets.values()
            ]

            return ToolResult(
                success=True,
                data={
                    "connections": conn_results,
                    "nets": net_results,
                    "resolved_count": len(resolved_conns),
                    "skipped_count": len(skipped),
                    "unresolved_count": len(unresolved),
                    "skipped_reasons": [
                        f"连接 '{c.connection_id}' 跳过: 端模块未解析"
                        for c in skipped
                    ],
                },
            )
        except Exception as exc:
            logger.exception("resolve_connections failed")
            return ToolResult(
                success=False,
                error=ToolError(
                    code=ErrorCode.ENGINE_FAILED,
                    message=f"连接解析失败: {exc}",
                    retriable=True,
                ),
            )

    registry.register_fn(
        name="resolve_connections",
        description=(
            "接收连接意图列表，用规则引擎解析为引脚级连接。"
            "必须在 resolve_modules 之后、synthesize_parameters 之前调用。"
        ),
        handler=_handle_resolve_connections,
        parameters_schema={
            "connections": {
                "type": "array",
                "description": (
                    "连接意图列表，每个元素: "
                    '{"connection_id": "conn1", "src_module": "buck1", '
                    '"dst_module": "ldo1", "signal_type": "power_supply", '
                    '"connection_semantics": "supply_chain"}'
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "connection_id": {
                            "type": "string",
                            "description": "连接唯一ID",
                        },
                        "src_module": {
                            "type": "string",
                            "description": "源模块 intent_id",
                        },
                        "dst_module": {
                            "type": "string",
                            "description": "目标模块 intent_id",
                        },
                        "signal_type": {
                            "type": "string",
                            "description": (
                                "信号类型: power_supply / gpio / spi / i2c / "
                                "uart / analog / enable / feedback / other"
                            ),
                        },
                        "connection_semantics": {
                            "type": "string",
                            "description": (
                                "连接语义: supply_chain / gpio_drive / "
                                "bus_connect / enable_control / ground_tie"
                            ),
                        },
                        "src_port_hint": {
                            "type": "string",
                            "description": "源端口提示: VOUT / PA1（可空）",
                        },
                        "dst_port_hint": {
                            "type": "string",
                            "description": "目标端口提示: VIN / ANODE（可空）",
                        },
                    },
                    "required": ["connection_id", "src_module", "dst_module",
                                 "signal_type", "connection_semantics"],
                },
            },
        },
        required_params=["connections"],
        category="design",
    )

    # ================================================================
    # 工具 3: synthesize_parameters — 参数计算
    # ================================================================
    def _handle_synthesize_parameters() -> ToolResult:
        """用本地公式引擎为所有已解析模块计算外围元件参数。"""
        try:
            if session._ir is None:
                return ToolResult(
                    success=False,
                    error=ToolError(
                        code=ErrorCode.DESIGN_INVALID,
                        message="请先调用 resolve_modules 和 resolve_connections。",
                    ),
                )

            from schemaforge.system.synthesis import synthesize_all_modules

            session._ir = synthesize_all_modules(session._ir)

            # 返回每个模块的综合后参数
            module_params: list[dict[str, Any]] = []
            for mid, inst in session._ir.module_instances.items():
                ext_comps = []
                for ec in getattr(inst, "external_components", []):
                    ext_comps.append({
                        "role": ec.get("role", ""),
                        "value": ec.get("value", ""),
                        "type": ec.get("type", ""),
                    })
                module_params.append({
                    "module_id": mid,
                    "status": inst.status.value
                    if hasattr(inst.status, "value") else str(inst.status),
                    "parameters": dict(inst.parameters),
                    "external_component_count": len(ext_comps),
                    "external_components": ext_comps,
                })

            return ToolResult(
                success=True,
                data={
                    "synthesized_count": sum(
                        1 for m in module_params
                        if m["status"] == "synthesized"
                    ),
                    "modules": module_params,
                },
            )
        except Exception as exc:
            logger.exception("synthesize_parameters failed")
            return ToolResult(
                success=False,
                error=ToolError(
                    code=ErrorCode.ENGINE_FAILED,
                    message=f"参数综合失败: {exc}",
                    retriable=True,
                ),
            )

    registry.register_fn(
        name="synthesize_parameters",
        description=(
            "用公式引擎为所有模块计算外围元件参数（电感、电容、电阻分压器等）。"
            "必须在 resolve_connections 之后调用。"
        ),
        handler=_handle_synthesize_parameters,
        parameters_schema={},
        category="design",
    )

    # ================================================================
    # 工具 4: render_schematic — 渲染 SVG
    # ================================================================
    def _handle_render_schematic() -> ToolResult:
        """渲染当前设计为 SVG 原理图。"""
        try:
            if session._ir is None:
                return ToolResult(
                    success=False,
                    error=ToolError(
                        code=ErrorCode.DESIGN_INVALID,
                        message="请先完成模块解析和参数综合。",
                    ),
                )

            from schemaforge.system.instances import (
                allocate_global_references,
                create_component_instances,
                stabilize_references_after_revision,
            )
            from schemaforge.system.layout import create_default_layout
            from schemaforge.system.rendering import render_system_svg_with_metadata

            ir = session._ir

            # 实例收集 + 编号分配
            comp_instances = create_component_instances(ir)
            if session._prev_component_instances:
                comp_instances = stabilize_references_after_revision(
                    session._prev_component_instances,
                    comp_instances,  # type: ignore[arg-type]
                )
            else:
                comp_instances = allocate_global_references(comp_instances)
            session._prev_component_instances = comp_instances  # type: ignore[assignment]

            # 布局 + 渲染
            session._layout_spec = create_default_layout(ir)
            svg_path, render_metadata = render_system_svg_with_metadata(
                ir, layout_spec=session._layout_spec,
            )

            # 暂存 comp_instances, render_metadata, svg_path 供后续 export 使用
            session._last_comp_instances = comp_instances  # type: ignore[attr-defined]
            session._last_render_metadata = render_metadata  # type: ignore[attr-defined]
            session._last_svg_path = svg_path  # type: ignore[attr-defined]

            meta_summary: dict[str, Any] = {
                "canvas_size": list(render_metadata.canvas_size),
                "module_count": len(render_metadata.module_bboxes),
                "wire_count": len(render_metadata.wire_paths),
            }

            return ToolResult(
                success=True,
                data={
                    "svg_path": svg_path,
                    "render_metadata": meta_summary,
                    "component_count": len(comp_instances),
                },
            )
        except Exception as exc:
            logger.exception("render_schematic failed")
            return ToolResult(
                success=False,
                error=ToolError(
                    code=ErrorCode.RENDER_FAILED,
                    message=f"原理图渲染失败: {exc}",
                    retriable=True,
                ),
            )

    registry.register_fn(
        name="render_schematic",
        description=(
            "渲染当前设计为 SVG 原理图，自动完成元件编号和布局。"
            "必须在 synthesize_parameters 之后调用。"
        ),
        handler=_handle_render_schematic,
        parameters_schema={},
        category="design",
    )

    # ================================================================
    # 工具 5: export_outputs — 导出 BOM + SPICE
    # ================================================================
    def _handle_export_outputs() -> ToolResult:
        """导出 BOM (Markdown + CSV) 和 SPICE 网表。"""
        try:
            if session._ir is None:
                return ToolResult(
                    success=False,
                    error=ToolError(
                        code=ErrorCode.DESIGN_INVALID,
                        message="请先完成设计并渲染原理图。",
                    ),
                )

            from schemaforge.system.export_bom import (
                export_system_bom_csv,
                export_system_bom_markdown,
            )
            from schemaforge.system.export_spice import export_system_spice

            ir = session._ir
            comp_instances = getattr(
                session, "_last_comp_instances", None
            )
            if comp_instances is None:
                # 如果没渲染过，先做实例收集
                from schemaforge.system.instances import (
                    allocate_global_references,
                    create_component_instances,
                )
                comp_instances = create_component_instances(ir)
                comp_instances = allocate_global_references(comp_instances)

            bom_text = export_system_bom_markdown(comp_instances, ir)
            bom_csv = export_system_bom_csv(comp_instances)
            spice_text = export_system_spice(ir, comp_instances)

            # 组装 bundle 并存入 session
            from schemaforge.system.models import RenderMetadata, SystemBundle

            svg_path = getattr(session, "_last_svg_path", "") or ""
            render_metadata = getattr(
                session, "_last_render_metadata", RenderMetadata(),
            ) or RenderMetadata()

            bundle = SystemBundle(
                design_ir=ir,
                svg_path=svg_path,
                bom_text=bom_text,
                bom_csv=bom_csv,
                spice_text=spice_text,
                render_metadata=render_metadata,
            )
            session._bundle = bundle

            return ToolResult(
                success=True,
                data={
                    "bom_text": bom_text,
                    "spice_text": spice_text,
                    "bom_csv_lines": len(bom_csv.splitlines()),
                    "bundle_assembled": True,
                },
            )
        except Exception as exc:
            logger.exception("export_outputs failed")
            return ToolResult(
                success=False,
                error=ToolError(
                    code=ErrorCode.ENGINE_FAILED,
                    message=f"导出失败: {exc}",
                    retriable=True,
                ),
            )

    registry.register_fn(
        name="export_outputs",
        description=(
            "导出 BOM 清单 (Markdown) 和 SPICE 网表，并组装最终 bundle。"
            "必须在 render_schematic 之后调用。"
        ),
        handler=_handle_export_outputs,
        parameters_schema={},
        category="design",
    )

    # ================================================================
    # 工具 6: search_device_library — 查器件库
    # ================================================================
    def _handle_search_device_library(query: str) -> ToolResult:
        """在器件库中搜索器件。"""
        try:
            devices = session._store.search_devices(query=query)
            results: list[dict[str, str]] = []
            for dev in devices:
                results.append({
                    "part_number": getattr(dev, "part_number", ""),
                    "category": getattr(dev, "category", ""),
                    "manufacturer": getattr(dev, "manufacturer", ""),
                    "description": getattr(dev, "description", ""),
                    "package": getattr(dev, "package", ""),
                })
            return ToolResult(
                success=True,
                data={
                    "query": query,
                    "count": len(results),
                    "devices": results,
                },
            )
        except Exception as exc:
            logger.exception("search_device_library failed")
            return ToolResult(
                success=False,
                error=ToolError(
                    code=ErrorCode.ENGINE_FAILED,
                    message=f"器件搜索失败: {exc}",
                    retriable=True,
                ),
            )

    registry.register_fn(
        name="search_device_library",
        description="在器件库中搜索器件（按型号、类别、厂商）",
        handler=_handle_search_device_library,
        parameters_schema={
            "query": {
                "type": "string",
                "description": "搜索关键词：型号、类别、厂商",
            },
        },
        required_params=["query"],
        category="library",
    )

    # ================================================================
    # 工具 7: get_design_status — 查设计状态
    # ================================================================
    def _handle_get_design_status() -> ToolResult:
        """获取当前设计状态。"""
        ir = session._ir
        if ir is None:
            return ToolResult(
                success=True,
                data={"has_design": False, "message": "当前没有活跃设计。"},
            )

        modules = []
        for mid, inst in ir.module_instances.items():
            modules.append(_serialize_module_instance(mid, inst))

        connections = [_serialize_connection(c) for c in ir.connections]

        bundle = session._bundle
        svg_path = getattr(bundle, "svg_path", "") if bundle else ""
        has_bom = bool(getattr(bundle, "bom_text", "")) if bundle else False
        has_spice = bool(getattr(bundle, "spice_text", "")) if bundle else False

        return ToolResult(
            success=True,
            data={
                "has_design": True,
                "modules": modules,
                "connections": connections,
                "svg_path": svg_path,
                "has_bom": has_bom,
                "has_spice": has_spice,
                "warnings": list(ir.warnings),
            },
        )

    registry.register_fn(
        name="get_design_status",
        description="获取当前设计状态：模块列表、连接关系、SVG路径、BOM",
        handler=_handle_get_design_status,
        parameters_schema={},
        category="design",
    )

    # ================================================================
    # 工具 8: review_design — 工程审查
    # ================================================================
    def _handle_review_design() -> ToolResult:
        """运行工程审查规则检查。"""
        ir = session._ir
        if ir is None:
            return ToolResult(
                success=False,
                error=ToolError(
                    code=ErrorCode.DESIGN_INVALID,
                    message="当前没有活跃的设计，请先完成设计流程。",
                ),
            )

        resolved = ir.get_resolved_modules()
        if not resolved:
            return ToolResult(
                success=True,
                data={
                    "passed": True,
                    "issue_count": 0,
                    "issues": [],
                    "message": "没有已解析的模块可审查。",
                },
            )

        try:
            from schemaforge.design.review import (
                DesignReviewEngine,
                ModuleReviewInput,
            )

            review_inputs: list[ModuleReviewInput] = []
            for inst in resolved:
                if inst.device is None:
                    continue
                category = (inst.resolved_category or "").lower()
                role = (
                    f"{inst.module_id}_{category}" if category else inst.module_id
                )
                review_inputs.append(
                    ModuleReviewInput(
                        role=role,
                        category=category,
                        device=inst.device,
                        parameters=inst.parameters,
                    )
                )

            engine = DesignReviewEngine()
            review = engine.review_design(review_inputs)

            issues_data: list[dict[str, str | None]] = []
            for issue in review.issues:
                issues_data.append({
                    "severity": issue.severity.value
                    if hasattr(issue.severity, "value")
                    else str(issue.severity),
                    "category": issue.category.value
                    if hasattr(issue.category, "value")
                    else str(issue.category),
                    "message": issue.message,
                    "suggestion": issue.suggestion,
                    "module_role": issue.module_role,
                })

            return ToolResult(
                success=True,
                data={
                    "overall_passed": review.overall_passed,
                    "issue_count": len(review.issues),
                    "blocking_count": sum(
                        1
                        for i in review.issues
                        if hasattr(i.severity, "value")
                        and i.severity.value == "blocking"
                    ),
                    "issues": issues_data,
                    "reviewed_modules": len(review_inputs),
                },
            )
        except Exception as exc:
            logger.exception("review_design failed")
            return ToolResult(
                success=False,
                error=ToolError(
                    code=ErrorCode.ENGINE_FAILED,
                    message=f"工程审查失败: {exc}",
                    retriable=True,
                ),
            )

    registry.register_fn(
        name="review_design",
        description="运行工程审查规则检查当前设计",
        handler=_handle_review_design,
        parameters_schema={},
        category="design",
    )

    # ================================================================
    # 工具 9: revise_module_param — 修改单个模块参数
    # ================================================================
    def _handle_revise_module_param(
        module_id: str, parameters: dict[str, str],
    ) -> ToolResult:
        """修改指定模块的参数，然后重新综合。"""
        try:
            if session._ir is None:
                return ToolResult(
                    success=False,
                    error=ToolError(
                        code=ErrorCode.DESIGN_INVALID,
                        message="当前没有活跃设计。",
                    ),
                )

            ir = session._ir
            inst = ir.module_instances.get(module_id)
            if inst is None:
                return ToolResult(
                    success=False,
                    error=ToolError(
                        code=ErrorCode.DESIGN_INVALID,
                        message=f"模块 '{module_id}' 不存在。",
                    ),
                )

            # 更新参数
            inst.parameters.update(parameters)
            # 也更新 request 中对应 intent 的 electrical_targets
            for m_intent in ir.request.modules:
                if m_intent.intent_id == module_id:
                    m_intent.electrical_targets.update(parameters)
                    break

            # 重新综合
            from schemaforge.system.synthesis import synthesize_all_modules
            session._ir = synthesize_all_modules(ir)

            updated = session._ir.module_instances.get(module_id)
            return ToolResult(
                success=True,
                data=_serialize_module_instance(
                    module_id, updated,
                ) if updated else {"message": "模块参数已更新"},
            )
        except Exception as exc:
            logger.exception("revise_module_param failed")
            return ToolResult(
                success=False,
                error=ToolError(
                    code=ErrorCode.ENGINE_FAILED,
                    message=f"参数修改失败: {exc}",
                    retriable=True,
                ),
            )

    registry.register_fn(
        name="revise_module_param",
        description=(
            "修改指定模块的参数（如修改输出电压），然后自动重新综合。"
            "修改后需重新调用 render_schematic 和 export_outputs 更新输出。"
        ),
        handler=_handle_revise_module_param,
        parameters_schema={
            "module_id": {
                "type": "string",
                "description": "要修改的模块 ID（如 buck1, ldo1）",
            },
            "parameters": {
                "type": "object",
                "description": '要修改的参数键值对: {"v_out": "3.3"}',
            },
        },
        required_params=["module_id", "parameters"],
        category="design",
    )

    # ================================================================
    # 工具 10: get_svg_template — 生成 SVG 坐标骨架
    # ================================================================
    def _handle_get_svg_template() -> ToolResult:
        """根据当前 IR 数据生成严格的 SVG 坐标模板。

        返回一个结构化 JSON，包含每个元件的精确坐标、
        每条连线的起止点，AI 据此绘制 SVG 即可保证不重叠不断开。
        """
        try:
            if session._ir is None:
                return ToolResult(
                    success=False,
                    error=ToolError(
                        code=ErrorCode.DESIGN_INVALID,
                        message="请先完成 resolve_modules + synthesize_parameters。",
                    ),
                )
            ir = session._ir
            template = _build_svg_template(ir)
            return ToolResult(success=True, data=template)
        except Exception as exc:
            logger.exception("get_svg_template failed")
            return ToolResult(
                success=False,
                error=ToolError(
                    code=ErrorCode.ENGINE_FAILED,
                    message=f"SVG 模板生成失败: {exc}",
                    retriable=True,
                ),
            )

    registry.register_fn(
        name="get_svg_template",
        description=(
            "根据当前设计数据生成 SVG 坐标模板。"
            "返回每个元件的精确位置、每条连线的起止点坐标。"
            "AI 必须在调用 render_schematic_ai 之前先调用此工具获取布局。"
        ),
        handler=_handle_get_svg_template,
        parameters_schema={},
        category="design",
    )

    # ================================================================
    # 工具 11: render_schematic_ai — AI 自己写 SVG
    # ================================================================
    def _handle_render_schematic_ai(svg_code: str) -> ToolResult:
        """AI 自己生成的 SVG 源码 → 保存文件 + 转 PNG 供审查。"""
        try:
            if session._ir is None:
                return ToolResult(
                    success=False,
                    error=ToolError(
                        code=ErrorCode.DESIGN_INVALID,
                        message="请先完成 resolve_modules / resolve_connections / synthesize_parameters。",
                    ),
                )

            import base64
            import os
            import time

            from schemaforge.render.base import output_path

            # 保存 SVG 文件
            ts = int(time.time() * 1000) % 100000
            svg_filename = f"ai_schematic_{ts}.svg"
            svg_path = output_path(svg_filename)
            with open(svg_path, "w", encoding="utf-8") as f:
                f.write(svg_code)

            # SVG → PNG 用于视觉审查
            png_path = svg_path.replace(".svg", ".png")
            _svg_to_png(svg_path, png_path)

            # 读取 PNG 的 base64
            png_b64 = ""
            if os.path.exists(png_path):
                with open(png_path, "rb") as f:
                    png_b64 = base64.b64encode(f.read()).decode("ascii")

            # 暂存到 session
            session._last_svg_path = svg_path  # type: ignore[attr-defined]
            session._last_svg_content = svg_code  # type: ignore[attr-defined]
            session._last_png_path = png_path  # type: ignore[attr-defined]
            session._last_png_b64 = png_b64  # type: ignore[attr-defined]

            return ToolResult(
                success=True,
                data={
                    "svg_path": svg_path,
                    "png_path": png_path,
                    "svg_length": len(svg_code),
                    "has_png": bool(png_b64),
                    "message": "SVG 已保存，PNG 已生成。可调用 review_schematic_visual 审查渲染效果。",
                },
            )
        except Exception as exc:
            logger.exception("render_schematic_ai failed")
            return ToolResult(
                success=False,
                error=ToolError(
                    code=ErrorCode.RENDER_FAILED,
                    message=f"AI SVG 保存/转换失败: {exc}",
                    retriable=True,
                ),
            )

    registry.register_fn(
        name="render_schematic_ai",
        description=(
            "保存 AI 生成的 SVG 原理图源码，并自动转换为 PNG 图片供审查。"
            "传入完整的 SVG 源码字符串。保存后可调用 review_schematic_visual 审查。"
        ),
        handler=_handle_render_schematic_ai,
        parameters_schema={
            "svg_code": {
                "type": "string",
                "description": "完整的 SVG 源码（包括 <svg> 根元素和所有内容）",
            },
        },
        required_params=["svg_code"],
        category="design",
    )

    # ================================================================
    # 工具 11: review_schematic_visual — 视觉审查（PNG + SVG 源码）
    # ================================================================
    def _handle_review_schematic_visual() -> ToolResult:
        """将当前 PNG 截图 + SVG 源码发给 vision API 做审查。

        返回 AI 的审查意见（JSON），AI 可据此决定是否调用
        revise_schematic_svg 修改。
        """
        try:
            png_b64 = getattr(session, "_last_png_b64", "") or ""
            svg_content = getattr(session, "_last_svg_content", "") or ""

            if not png_b64:
                return ToolResult(
                    success=False,
                    error=ToolError(
                        code=ErrorCode.DESIGN_INVALID,
                        message="没有可审查的 PNG，请先调用 render_schematic_ai。",
                    ),
                )

            from schemaforge.ai.client import call_llm_vision

            # 构建审查 prompt — 把 SVG 源码也传过去
            review_prompt = (
                "你是电路原理图视觉审查专家。审查这张原理图 PNG 截图，指出渲染问题。\n"
                "只关注视觉/布局问题（重叠、遮挡、对齐、标签可读性、连线清晰度），"
                "不要修改电路设计本身。\n\n"
                "用 JSON 回复：\n"
                '{"score": 1-10, "issues": [{"type": "...", "description": "...", "fix": "..."}], "summary": "..."}'
            )
            user_text = (
                "请审查这张原理图截图。以下是对应的 SVG 源码（你稍后可能需要修改它）：\n\n"
                f"```svg\n{svg_content}\n```"
            )

            review_text = call_llm_vision(
                system_prompt=review_prompt,
                user_text=user_text,
                image_base64=png_b64,
                max_tokens=4096,
            )

            # 尝试解析 JSON
            from schemaforge.ai.client import _extract_json
            review_json = _extract_json(review_text)

            return ToolResult(
                success=True,
                data={
                    "review": review_json or {"raw_text": review_text},
                    "svg_length": len(svg_content),
                    "message": (
                        "审查完成。如果需要修改，调用 revise_schematic_svg 提交修改后的完整 SVG。"
                        "你已经看到了 SVG 源码和渲染截图，请基于审查意见直接修改 SVG。"
                    ),
                },
            )
        except Exception as exc:
            logger.exception("review_schematic_visual failed")
            return ToolResult(
                success=False,
                error=ToolError(
                    code=ErrorCode.ENGINE_FAILED,
                    message=f"视觉审查失败: {exc}",
                    retriable=True,
                ),
            )

    registry.register_fn(
        name="review_schematic_visual",
        description=(
            "将当前渲染的 PNG 截图和 SVG 源码发给 vision AI 做视觉审查。"
            "返回审查评分和问题列表。审查后可调用 revise_schematic_svg 修改。"
        ),
        handler=_handle_review_schematic_visual,
        parameters_schema={},
        category="design",
    )

    # ================================================================
    # 工具 12: revise_schematic_svg — 提交修改后的 SVG
    # ================================================================
    def _handle_revise_schematic_svg(svg_code: str) -> ToolResult:
        """接收修改后的完整 SVG，覆盖保存 + 重新生成 PNG。"""
        # 复用 render_schematic_ai 的逻辑
        return _handle_render_schematic_ai(svg_code)

    registry.register_fn(
        name="revise_schematic_svg",
        description=(
            "提交修改后的完整 SVG 源码，覆盖保存并重新生成 PNG。"
            "用于在 review_schematic_visual 审查后修改原理图。"
            "传入完整的修改后 SVG（不是 diff，是整个文件）。"
        ),
        handler=_handle_revise_schematic_svg,
        parameters_schema={
            "svg_code": {
                "type": "string",
                "description": "修改后的完整 SVG 源码",
            },
        },
        required_params=["svg_code"],
        category="design",
    )

    return registry


# ------------------------------------------------------------------
# SVG 坐标模板生成器
# ------------------------------------------------------------------


def _build_svg_template(ir: Any) -> dict[str, Any]:
    """根据 IR 生成严格的 SVG 坐标模板。

    核心思想：把画布分成网格区域，每个元件有固定的坐标和朝向，
    AI 只需要按坐标画标准符号 + 走直线连接即可。
    """
    canvas_w, canvas_h = 1200, 800
    # 主电源轨道 Y 坐标
    rail_y = 200
    # GND 轨道 Y 坐标
    gnd_y = 550
    # 元件区域
    components: list[dict[str, Any]] = []
    wires: list[dict[str, Any]] = []
    pins: list[dict[str, Any]] = []

    # 收集所有模块
    modules = list(ir.module_instances.items())
    power_modules = [
        (mid, m) for mid, m in modules
        if getattr(m, "resolved_category", "") in ("buck", "ldo", "boost")
    ]
    # ---- 布局电源模块 ----
    for idx, (mid, inst) in enumerate(power_modules):
        device = getattr(inst, "device", None)
        pn = getattr(device, "part_number", mid) if device else mid
        ext_comps = getattr(inst, "external_components", [])

        # IC 中心 X 坐标：每个电源模块占 400px 宽度
        ic_cx = 400 + idx * 400
        ic_cy = rail_y + 100  # IC 中心
        ic_w, ic_h = 160, 200

        # IC 矩形
        ic_left = ic_cx - ic_w // 2
        ic_top = ic_cy - ic_h // 2
        ic_right = ic_cx + ic_w // 2

        # 定义引脚位置（左侧引脚从上到下，右侧引脚从上到下）
        left_pins = ["VIN", "SW", "GND"]
        right_pins = ["BOOT", "EN", "FB"]
        pin_positions: dict[str, dict[str, int]] = {}
        for i, p in enumerate(left_pins):
            py = ic_top + 40 + i * 60
            pin_positions[p] = {"x": ic_left, "y": py, "side": "left"}
        for i, p in enumerate(right_pins):
            py = ic_top + 40 + i * 60
            pin_positions[p] = {"x": ic_right, "y": py, "side": "right"}

        pins.append({
            "module_id": mid,
            "ic_rect": {"x": ic_left, "y": ic_top, "w": ic_w, "h": ic_h},
            "label": pn,
            "ref": f"U{idx + 1}",
            "pins": pin_positions,
        })

        # ---- 外围元件坐标 ----
        vin_pin = pin_positions["VIN"]
        sw_pin = pin_positions["SW"]
        gnd_pin = pin_positions["GND"]
        boot_pin = pin_positions["BOOT"]
        fb_pin = pin_positions["FB"]

        # 输入电容 C_in: VIN 引脚左边，竖直放置
        cin_x = vin_pin["x"] - 80
        components.append({
            "role": "input_cap",
            "ref": "C1",
            "type": "capacitor",
            "orientation": "vertical",
            "x": cin_x, "y": vin_pin["y"],
            "value": _find_ext_value(ext_comps, "input_cap", "10μF"),
            "connect_top": {"x": cin_x, "y": vin_pin["y"]},
            "connect_bottom": {"x": cin_x, "y": gnd_y},
        })
        # 水平线: C_in top → VIN 引脚
        wires.append({
            "from": {"x": cin_x, "y": vin_pin["y"]},
            "to": {"x": vin_pin["x"], "y": vin_pin["y"]},
            "label": "VIN rail",
            "weight": 3,
        })

        # 电感 L: SW 引脚左边 → 输出节点
        l_x1 = sw_pin["x"] - 120
        l_x2 = sw_pin["x"] - 30
        out_x = ic_right + 120  # 输出节点 X
        components.append({
            "role": "inductor",
            "ref": "L1",
            "type": "inductor",
            "orientation": "horizontal",
            "x1": l_x1, "y": sw_pin["y"],
            "x2": l_x2,
            "value": _find_ext_value(ext_comps, "inductor", "10μH"),
        })
        # SW 引脚 → 电感左端
        wires.append({
            "from": {"x": sw_pin["x"], "y": sw_pin["y"]},
            "to": {"x": l_x1, "y": sw_pin["y"]},
            "label": "SW→L",
        })
        # 电感右端 → 输出节点
        wires.append({
            "from": {"x": l_x2, "y": sw_pin["y"]},
            "to": {"x": out_x, "y": sw_pin["y"]},
            "label": "L→VOUT",
            "weight": 3,
        })

        # 续流二极管 D: SW 节点 → GND，竖直方向
        d_x = l_x1
        d_top = sw_pin["y"] + 20
        d_bottom = d_top + 60
        components.append({
            "role": "diode",
            "ref": "D1",
            "type": "diode",
            "orientation": "vertical",
            "x": d_x, "y_top": d_top, "y_bottom": d_bottom,
            "value": _find_ext_value(ext_comps, "diode", "SS34"),
            "connect_top": {"x": d_x, "y": sw_pin["y"]},
            "connect_bottom": {"x": d_x, "y": gnd_y},
        })

        # 输出电容 C_out: 输出节点到 GND
        cout_x = out_x
        components.append({
            "role": "output_cap",
            "ref": "C2",
            "type": "capacitor",
            "orientation": "vertical",
            "x": cout_x, "y": sw_pin["y"] + 40,
            "value": _find_ext_value(ext_comps, "output_cap", "22μF"),
            "connect_top": {"x": cout_x, "y": sw_pin["y"]},
            "connect_bottom": {"x": cout_x, "y": gnd_y},
        })

        # 自举电容 C_bst: BOOT 引脚 → SW 引脚（竖直）
        bst_x = boot_pin["x"] + 60
        components.append({
            "role": "boot_cap",
            "ref": "C3",
            "type": "capacitor",
            "orientation": "vertical",
            "x": bst_x, "y": boot_pin["y"],
            "value": _find_ext_value(ext_comps, "boot_cap", "100nF"),
            "connect_top": {"x": bst_x, "y": boot_pin["y"]},
            "connect_bottom": {"x": bst_x, "y": sw_pin["y"]},
        })
        wires.append({
            "from": {"x": boot_pin["x"], "y": boot_pin["y"]},
            "to": {"x": bst_x, "y": boot_pin["y"]},
            "label": "BOOT→C3",
        })
        # C3 下端 → SW 节点（水平走线回到 SW）
        wires.append({
            "from": {"x": bst_x, "y": sw_pin["y"]},
            "to": {"x": sw_pin["x"], "y": sw_pin["y"]},
            "label": "C3→SW",
        })

        # FB 分压电阻: VOUT → R1 → FB 节点 → R2 → GND
        fb_x = fb_pin["x"] + 60
        r1_top = fb_pin["y"] - 60
        r1_bottom = fb_pin["y"]
        r2_top = fb_pin["y"]
        r2_bottom = fb_pin["y"] + 60
        components.append({
            "role": "fb_upper",
            "ref": "R1",
            "type": "resistor",
            "orientation": "vertical",
            "x": fb_x, "y_top": r1_top, "y_bottom": r1_bottom,
            "value": _find_ext_value(ext_comps, "fb_upper", "30kΩ"),
            "connect_top": {"x": fb_x, "y": r1_top},
            "connect_bottom": {"x": fb_x, "y": fb_pin["y"]},
        })
        components.append({
            "role": "fb_lower",
            "ref": "R2",
            "type": "resistor",
            "orientation": "vertical",
            "x": fb_x, "y_top": r2_top, "y_bottom": r2_bottom,
            "value": _find_ext_value(ext_comps, "fb_lower", "10kΩ"),
            "connect_top": {"x": fb_x, "y": fb_pin["y"]},
            "connect_bottom": {"x": fb_x, "y": gnd_y},
        })
        # VOUT rail → R1 上端
        wires.append({
            "from": {"x": out_x, "y": sw_pin["y"]},
            "to": {"x": fb_x, "y": r1_top},
            "label": "VOUT→R1",
            "path": [
                {"x": out_x, "y": sw_pin["y"]},
                {"x": out_x, "y": r1_top},
                {"x": fb_x, "y": r1_top},
            ],
        })
        # R1-R2 中点 → FB 引脚
        wires.append({
            "from": {"x": fb_x, "y": fb_pin["y"]},
            "to": {"x": fb_pin["x"], "y": fb_pin["y"]},
            "label": "FB node",
        })
        # GND 引脚 → GND 轨
        wires.append({
            "from": {"x": gnd_pin["x"], "y": gnd_pin["y"]},
            "to": {"x": gnd_pin["x"], "y": gnd_y},
            "label": "IC GND",
        })

        # EN 上拉到 VIN（简化：直接连线）
        wires.append({
            "from": {"x": pin_positions['EN']['x'], "y": pin_positions['EN']['y']},
            "to": {"x": pin_positions['EN']['x'] + 40, "y": pin_positions['EN']['y']},
            "label": "EN",
        })
        wires.append({
            "from": {"x": pin_positions['EN']['x'] + 40, "y": pin_positions['EN']['y']},
            "to": {"x": pin_positions['EN']['x'] + 40, "y": vin_pin["y"]},
            "label": "EN pullup to VIN",
        })

    # ---- VIN/VOUT 标签 ----
    labels = [
        {"text": "VIN", "x": 60, "y": rail_y, "color": "red", "font_size": 16},
    ]
    if power_modules:
        _, first_inst = power_modules[0]
        v_in = dict(getattr(first_inst, "parameters", {})).get("v_in", "")
        v_out = dict(getattr(first_inst, "parameters", {})).get("v_out", "")
        if v_in:
            labels[0]["text"] = f"VIN {v_in}V"
        last_out_x = 400 + (len(power_modules) - 1) * 400 + 200
        labels.append({
            "text": f"VOUT {v_out}V" if v_out else "VOUT",
            "x": last_out_x, "y": rail_y, "color": "blue", "font_size": 16,
        })

    # ---- GND 轨道 ----
    gnd_rail = {"y": gnd_y, "x_start": 100, "x_end": canvas_w - 100}

    return {
        "canvas": {"width": canvas_w, "height": canvas_h},
        "rail_y": rail_y,
        "gnd_y": gnd_y,
        "gnd_rail": gnd_rail,
        "ic_modules": pins,
        "components": components,
        "wires": wires,
        "labels": labels,
        "instructions": (
            "严格按照上面的坐标画 SVG。"
            "每个元件用标准符号画在指定位置，连线走指定路径（横平竖直）。"
            "所有竖直元件（电容/电阻/二极管）的上端 connect_top、下端 connect_bottom 必须对齐。"
            "GND 轨道是一条水平线 y={gnd_y}，所有接地元件底端连到这条线。"
            "IC 用矩形框+引脚短线表示，引脚名标在短线末端。"
        ),
    }


def _find_ext_value(
    ext_comps: list[dict[str, str]], role: str, default: str,
) -> str:
    """从外围元件列表中查找指定角色的值。"""
    for ec in ext_comps:
        if ec.get("role", "") == role:
            return ec.get("value", default)
    return default


# ------------------------------------------------------------------
# SVG → PNG 转换辅助
# ------------------------------------------------------------------


def _svg_to_png(svg_path: str, png_path: str, width: int = 1200) -> None:
    """SVG 文件转 PNG 文件。

    优先使用 cairosvg（高保真），fallback 到 Pillow + svglib，
    最后 fallback 到空白占位 PNG。
    """
    try:
        import cairosvg
        cairosvg.svg2png(url=svg_path, write_to=png_path, output_width=width)
        return
    except ImportError:
        pass
    except Exception:
        pass

    try:
        from io import BytesIO

        from PIL import Image
        from svglib.svglib import svg2rlg
        from reportlab.graphics import renderPM

        drawing = svg2rlg(svg_path)
        if drawing:
            png_data = BytesIO()
            renderPM.drawToFile(drawing, png_data, fmt="PNG")
            png_data.seek(0)
            img = Image.open(png_data)
            img.save(png_path)
            return
    except ImportError:
        pass
    except Exception:
        pass

    # 最终 fallback：用 schemdraw 生成空白提示图
    try:
        from PIL import Image, ImageDraw

        img = Image.new("RGB", (width, 400), "white")
        draw = ImageDraw.Draw(img)
        draw.text(
            (20, 180),
            "SVG→PNG 转换需要 cairosvg 或 svglib 库",
            fill="red",
        )
        img.save(png_path)
    except Exception:
        # 写一个最小的 1x1 PNG 保证文件存在
        import struct
        import zlib

        def _minimal_png() -> bytes:
            sig = b"\x89PNG\r\n\x1a\n"
            ihdr_data = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
            ihdr_crc = zlib.crc32(b"IHDR" + ihdr_data) & 0xFFFFFFFF
            ihdr = struct.pack(">I", 13) + b"IHDR" + ihdr_data + struct.pack(">I", ihdr_crc)
            raw = zlib.compress(b"\x00\xff\xff\xff")
            idat_crc = zlib.crc32(b"IDAT" + raw) & 0xFFFFFFFF
            idat = struct.pack(">I", len(raw)) + b"IDAT" + raw + struct.pack(">I", idat_crc)
            iend_crc = zlib.crc32(b"IEND") & 0xFFFFFFFF
            iend = struct.pack(">I", 0) + b"IEND" + struct.pack(">I", iend_crc)
            return sig + ihdr + idat + iend

        with open(png_path, "wb") as f:
            f.write(_minimal_png())


# ------------------------------------------------------------------
# AI SVG 模式 System Prompt
# ------------------------------------------------------------------

AGENT_SYSTEM_PROMPT_AI_SVG = """\
你是 SchemaForge 电路设计 AI 助手（AI SVG 绘图模式）。

在此模式下，你负责电路设计编排，并**亲自编写 SVG 源码**绘制原理图。

## 工作流程（严格按顺序）

1. **调用 resolve_modules** — 提交模块意图，查器件库
2. **调用 resolve_connections** — 提交连接意图
3. **调用 synthesize_parameters** — 计算外围元件参数
4. **调用 get_svg_template** — 获取 SVG 坐标模板（关键！）
5. **根据模板编写 SVG** → 调用 render_schematic_ai 保存
6. **调用 review_schematic_visual** — 审查（可选）
7. **根据审查修改** → 调用 revise_schematic_svg（可选）
8. **调用 export_outputs** — 导出 BOM + SPICE
9. **总结结果**

## 核心规则：使用 get_svg_template 的坐标

**你必须先调用 get_svg_template 获取布局模板，然后严格按照模板中的坐标画 SVG。**

模板返回的数据结构：
- `ic_modules`: IC 矩形位置、引脚精确坐标
- `components`: 每个外围元件的坐标、朝向、connect_top/connect_bottom 端点
- `wires`: 每条连线的起止坐标（有些还有 path 中间点）
- `gnd_rail`: GND 总线的 Y 坐标和起止 X
- `labels`: 文字标签位置

## SVG 绘图规范

### 画布
- `viewBox="0 0 1200 800"`, 白色背景

### 元件符号（必须画在模板指定的坐标上）

**IC 芯片**：
```svg
<rect x="{ic_rect.x}" y="{ic_rect.y}" width="{ic_rect.w}" height="{ic_rect.h}"
      fill="#f5f5f5" stroke="black" stroke-width="2"/>
<!-- 每个引脚: 从 IC 边缘伸出 20px 短线 -->
<line x1="{pin.x}" y1="{pin.y}" x2="{pin.x - 20}" y2="{pin.y}" stroke="black" stroke-width="2"/>
<text x="{pin.x - 25}" y="{pin.y + 4}" text-anchor="end" font-size="12">VIN</text>
```

**电容（竖直）**：两条平行水平短线，间距 5px
```svg
<line x1="{x-8}" y1="{y}" x2="{x+8}" y2="{y}" stroke="black" stroke-width="2"/>
<line x1="{x-8}" y1="{y+5}" x2="{x+8}" y2="{y+5}" stroke="black" stroke-width="2"/>
<!-- 上方引线 -->
<line x1="{x}" y1="{connect_top.y}" x2="{x}" y2="{y}" stroke="black" stroke-width="2"/>
<!-- 下方引线 -->
<line x1="{x}" y1="{y+5}" x2="{x}" y2="{connect_bottom.y}" stroke="black" stroke-width="2"/>
<!-- 标签在右侧 -->
<text x="{x+12}" y="{y}" font-size="11">{ref}</text>
<text x="{x+12}" y="{y+12}" font-size="10">{value}</text>
```

**电阻（竖直）**：小矩形 12×35
```svg
<rect x="{x-6}" y="{y_top}" width="12" height="35" fill="none" stroke="black" stroke-width="2"/>
<!-- 上方引线 -->
<line x1="{x}" y1="{connect_top.y}" x2="{x}" y2="{y_top}" stroke="black" stroke-width="2"/>
<!-- 下方引线 -->
<line x1="{x}" y1="{y_top+35}" x2="{x}" y2="{connect_bottom.y}" stroke="black" stroke-width="2"/>
<!-- 标签在右侧 -->
<text x="{x+10}" y="{y_top+15}" font-size="11">{ref}</text>
<text x="{x+10}" y="{y_top+27}" font-size="10">{value}</text>
```

**电感（水平）**：4 个半圆弧
```svg
<path d="M {x1} {y} Q {x1+10} {y-15} {x1+20} {y}
         Q {x1+30} {y-15} {x1+40} {y}
         Q {x1+50} {y-15} {x1+60} {y}
         Q {x1+70} {y-15} {x1+80} {y}" fill="none" stroke="black" stroke-width="2"/>
```

**二极管（竖直，阳极朝上阴极朝下）**：
```svg
<polygon points="{x-10},{y_top} {x+10},{y_top} {x},{y_top+20}" fill="none" stroke="black" stroke-width="2"/>
<line x1="{x-10}" y1="{y_top+20}" x2="{x+10}" y2="{y_top+20}" stroke="black" stroke-width="2"/>
```

**GND 符号**：
```svg
<line x1="{x-12}" y1="{gnd_y}" x2="{x+12}" y2="{gnd_y}" stroke="black" stroke-width="2"/>
<line x1="{x-8}" y1="{gnd_y+4}" x2="{x+8}" y2="{gnd_y+4}" stroke="black" stroke-width="2"/>
<line x1="{x-4}" y1="{gnd_y+8}" x2="{x+4}" y2="{gnd_y+8}" stroke="black" stroke-width="2"/>
```

### 连线
- 严格使用 wires 中的 from/to 坐标
- 用 `<line>` 画直线段（横平竖直）
- 如果 wire 有 path 字段，按 path 中的点画折线（每两点之间一条 line）
- 电源线 weight=3 时用 stroke-width="3"
- T 形节点处画实心小圆点: `<circle cx="{x}" cy="{y}" r="3" fill="black"/>`

### 标签
- 按 labels 中的坐标和颜色放置文字

## 设计规则

- intent_id 命名：buck1, ldo1, mcu1, led1
- category_hint：buck, ldo, boost, mcu, led
- signal_type：power_supply, gpio, spi, i2c, uart, analog, enable, feedback, other
- connection_semantics：supply_chain, gpio_drive, bus_connect, enable_control, ground_tie
- 不要在 resolve_modules 时指定电阻电容电感数值
- SVG 必须完整可渲染（含 `<?xml?>` 和 `<svg>` 根元素）

用中文回复用户。
"""
