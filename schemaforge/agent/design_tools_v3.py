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

    return registry
