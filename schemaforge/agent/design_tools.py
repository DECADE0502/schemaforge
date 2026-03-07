"""SchemaForge 设计工作台工具集。

已注册工具（会话绑定）:
  - start_design_request: 从自然语言启动设计
  - ingest_datasheet_asset: 上传 PDF/图片解析器件
  - confirm_import_device: 确认导入器件
  - apply_design_revision: 自然语言修改设计
  - calculate_parameters: 基于 datasheet 公式计算外围元件参数
  - evaluate_formula: 单个公式表达式求值
  - generate_netlist: 生成 SPICE 网表
  - render_schematic: 重新渲染当前设计原理图 SVG
  - validate_design: 对当前设计执行工程审查
"""

from __future__ import annotations

from schemaforge.agent.tool_registry import ToolRegistry, ToolResult
from schemaforge.common.errors import ErrorCode, ToolError
from schemaforge.workflows.schemaforge_session import SchemaForgeSession


def _no_bundle_error() -> ToolResult:
    """当会话中没有活跃设计时返回的统一错误。"""
    return ToolResult(
        success=False,
        error=ToolError(
            code=ErrorCode.DESIGN_INVALID,
            message="当前没有活跃的设计，请先通过 start_design_request 创建设计。",
        ),
    )


def build_design_tool_registry(session: SchemaForgeSession) -> ToolRegistry:
    """为指定会话构建可调用工具集。"""
    registry = ToolRegistry()

    # ----------------------------------------------------------------
    # 工具 1: start_design_request — 启动设计
    # ----------------------------------------------------------------
    registry.register_fn(
        name="start_design_request",
        description="从用户自然语言启动设计，会精确匹配显式型号。",
        handler=lambda user_input: ToolResult(
            success=True,
            data=session.start(user_input).to_dict(),
        ),
        parameters_schema={
            "user_input": {"type": "string", "description": "用户中文设计请求"}
        },
        required_params=["user_input"],
        category="design",
    )

    # ----------------------------------------------------------------
    # 工具 2: ingest_datasheet_asset — 上传资料
    # ----------------------------------------------------------------
    registry.register_fn(
        name="ingest_datasheet_asset",
        description="上传 PDF 或图片后，解析器件信息并返回导入预览。",
        handler=lambda filepath: ToolResult(
            success=True,
            data=session.ingest_asset(filepath).to_dict(),
        ),
        parameters_schema={
            "filepath": {"type": "string", "description": "本地 PDF 或图片路径"}
        },
        required_params=["filepath"],
        category="design",
    )

    # ----------------------------------------------------------------
    # 工具 3: confirm_import_device — 确认导入
    # ----------------------------------------------------------------
    registry.register_fn(
        name="confirm_import_device",
        description="确认导入器件并继续完成设计。",
        handler=lambda answers=None: ToolResult(
            success=True,
            data=session.confirm_import(answers).to_dict(),
        ),
        parameters_schema={
            "answers": {"type": "object", "description": "用户确认/补充信息"}
        },
        category="design",
    )

    # ----------------------------------------------------------------
    # 工具 4: apply_design_revision — 自然语言修改
    # ----------------------------------------------------------------
    registry.register_fn(
        name="apply_design_revision",
        description="在当前设计上应用自然语言修改。",
        handler=lambda user_input: ToolResult(
            success=True,
            data=session.revise(user_input).to_dict(),
        ),
        parameters_schema={
            "user_input": {"type": "string", "description": "用户中文修改请求"}
        },
        required_params=["user_input"],
        category="design",
    )

    # ----------------------------------------------------------------
    # 工具 5: calculate_parameters — 公式驱动参数计算
    # ----------------------------------------------------------------
    def _handle_calculate_parameters(
        v_in: float = 0.0,
        v_out: float = 0.0,
        i_out: float = 0.0,
        fsw: float = 0.0,
    ) -> ToolResult:
        """基于当前器件的 datasheet 公式，计算外围元件参数值。"""
        bundle = session.bundle
        if bundle is None:
            return _no_bundle_error()

        recipe = bundle.recipe
        if not recipe.formulas and not recipe.sizing_components:
            return ToolResult(
                success=False,
                error=ToolError(
                    code=ErrorCode.DESIGN_INVALID,
                    message="当前器件的 recipe 中没有可计算的公式。",
                ),
            )

        from schemaforge.design.formula_eval import FormulaEvaluator

        # 构建上下文: 合并 bundle 已有参数 + 用户显式传入的覆盖值
        context: dict[str, float] = {}
        for key, val in bundle.parameters.items():
            try:
                context[key] = float(val)
            except (ValueError, TypeError):
                pass

        if v_in > 0:
            context["v_in"] = v_in
        if v_out > 0:
            context["v_out"] = v_out
        if i_out > 0:
            context["i_out"] = i_out
        if fsw > 0:
            context["fsw"] = fsw

        evaluator = FormulaEvaluator()
        eval_result = evaluator.evaluate_recipe(recipe, context)
        return ToolResult(
            success=eval_result.success,
            data={
                "computed_params": eval_result.computed_params,
                "raw_values": {
                    k: round(v, 9) for k, v in eval_result.raw_values.items()
                },
                "rationale": eval_result.rationale,
                "errors": eval_result.errors,
            },
            error=(
                ToolError(
                    code=ErrorCode.DESIGN_INVALID,
                    message=f"部分公式求解失败: {'; '.join(eval_result.errors)}",
                )
                if eval_result.errors
                else None
            ),
        )

    registry.register_fn(
        name="calculate_parameters",
        description=(
            "基于当前器件 datasheet 提取的公式，动态计算外围元件参数。"
            "需要先有活跃设计。可覆盖 v_in/v_out/i_out/fsw 重新计算。"
        ),
        handler=_handle_calculate_parameters,
        parameters_schema={
            "v_in": {"type": "number", "description": "输入电压 (V)，0 表示沿用当前值"},
            "v_out": {"type": "number", "description": "输出电压 (V)，0 表示沿用当前值"},
            "i_out": {"type": "number", "description": "输出电流 (A)，0 表示沿用当前值"},
            "fsw": {"type": "number", "description": "开关频率 (Hz)，0 表示沿用当前值"},
        },
        category="design",
    )

    # ----------------------------------------------------------------
    # 工具 6: evaluate_formula — 单表达式求值
    # ----------------------------------------------------------------
    def _handle_evaluate_formula(
        expression: str,
        component_type: str = "unknown",
        **extra_vars: object,
    ) -> ToolResult:
        """对单个数学表达式进行安全求值。"""
        from schemaforge.design.formula_eval import FormulaEvaluator

        # 构建变量上下文: 从当前 bundle + 额外变量
        context: dict[str, float] = {}
        bundle = session.bundle
        if bundle is not None:
            for key, val in bundle.parameters.items():
                try:
                    context[key] = float(val)
                except (ValueError, TypeError):
                    pass

        for key, val in extra_vars.items():
            try:
                context[key] = float(val)  # type: ignore[arg-type]
            except (ValueError, TypeError):
                pass

        evaluator = FormulaEvaluator()
        raw, formatted = evaluator.evaluate_single(expression, context, component_type)

        if raw is None:
            return ToolResult(
                success=False,
                error=ToolError(
                    code=ErrorCode.DESIGN_INVALID,
                    message=f"表达式无法求解: {formatted}",
                ),
            )
        return ToolResult(
            success=True,
            data={
                "raw_value": round(raw, 9),
                "formatted": formatted,
                "expression": expression,
                "component_type": component_type,
            },
        )

    registry.register_fn(
        name="evaluate_formula",
        description=(
            "安全求值单个数学/工程表达式。"
            "可传入额外变量覆盖。结果自动圆整到标准系列。"
        ),
        handler=_handle_evaluate_formula,
        parameters_schema={
            "expression": {
                "type": "string",
                "description": "数学表达式，如 'v_out * (1 - duty) / (fsw * delta_il)'",
            },
            "component_type": {
                "type": "string",
                "description": "元件类型 (capacitor/inductor/resistor/unknown)，影响圆整策略",
            },
        },
        required_params=["expression"],
        category="design",
    )

    # ----------------------------------------------------------------
    # 工具 7: generate_netlist — 生成 SPICE 网表
    # ----------------------------------------------------------------
    def _handle_generate_netlist(format: str = "spice") -> ToolResult:
        """获取当前设计的 SPICE 网表文本。"""
        bundle = session.bundle
        if bundle is None:
            return _no_bundle_error()

        if format == "spice":
            text = bundle.spice_text
            if not text:
                return ToolResult(
                    success=False,
                    error=ToolError(
                        code=ErrorCode.DESIGN_INVALID,
                        message="当前设计没有生成 SPICE 网表。",
                    ),
                )
            return ToolResult(
                success=True,
                data={
                    "format": "spice",
                    "netlist": text,
                    "device": bundle.device.part_number,
                },
            )

        return ToolResult(
            success=False,
            error=ToolError(
                code=ErrorCode.INVALID_FORMAT,
                message=f"不支持的网表格式: {format}。目前仅支持 'spice'。",
            ),
        )

    registry.register_fn(
        name="generate_netlist",
        description="获取当前设计的 SPICE 网表。需要先有活跃设计。",
        handler=_handle_generate_netlist,
        parameters_schema={
            "format": {
                "type": "string",
                "description": "网表格式，目前仅支持 'spice'",
            },
        },
        category="design",
    )

    # ----------------------------------------------------------------
    # 工具 8: render_schematic — 重新渲染原理图
    # ----------------------------------------------------------------
    def _handle_render_schematic(
        filename: str = "",
    ) -> ToolResult:
        """重新渲染当前设计的原理图 SVG。"""
        bundle = session.bundle
        if bundle is None:
            return _no_bundle_error()

        from schemaforge.schematic.renderer import TopologyRenderer

        device = bundle.device
        params = bundle.parameters

        try:
            svg_path = TopologyRenderer().render(
                device,
                params,
                filename=filename or None,
            )
        except ValueError as exc:
            return ToolResult(
                success=False,
                error=ToolError(
                    code=ErrorCode.RENDER_FAILED,
                    message=f"渲染失败: {exc}",
                ),
            )

        return ToolResult(
            success=True,
            data={
                "svg_path": svg_path,
                "device": device.part_number,
                "topology": (
                    device.topology.circuit_type if device.topology else "unknown"
                ),
            },
        )

    registry.register_fn(
        name="render_schematic",
        description="重新渲染当前设计的原理图 SVG 文件。需要先有活跃设计。",
        handler=_handle_render_schematic,
        parameters_schema={
            "filename": {
                "type": "string",
                "description": "输出文件名（可选，留空自动生成）",
            },
        },
        category="design",
    )

    # ----------------------------------------------------------------
    # 工具 9: validate_design — 工程审查
    # ----------------------------------------------------------------
    def _handle_validate_design() -> ToolResult:
        """对当前设计执行工程审查规则检查。"""
        bundle = session.bundle
        if bundle is None:
            return _no_bundle_error()

        from schemaforge.design.review import DesignReviewEngine, ModuleReviewInput

        device = bundle.device
        category = (device.category or "").lower()
        role = f"main_{category}" if category else "main"

        module_input = ModuleReviewInput(
            role=role,
            category=category,
            device=device,
            parameters=bundle.parameters,
        )

        engine = DesignReviewEngine()
        review = engine.review_module(module_input)

        issues_data = []
        for issue in review.issues:
            issues_data.append({
                "severity": issue.severity.value if hasattr(issue.severity, "value") else str(issue.severity),
                "category": issue.category.value if hasattr(issue.category, "value") else str(issue.category),
                "message": issue.message,
                "suggestion": issue.suggestion,
                "module_role": issue.module_role,
            })

        return ToolResult(
            success=True,
            data={
                "passed": review.passed,
                "issue_count": len(review.issues),
                "blocking_count": sum(
                    1 for i in review.issues
                    if hasattr(i.severity, "value") and i.severity.value == "blocking"
                ),
                "issues": issues_data,
                "device": device.part_number,
                "category": category,
            },
        )

    registry.register_fn(
        name="validate_design",
        description=(
            "对当前设计执行工程审查（电压余量、热耗散、电容配置等）。"
            "返回审查结果，包括阻断问题、警告、建议。需要先有活跃设计。"
        ),
        handler=_handle_validate_design,
        parameters_schema={},
        category="design",
    )

    return registry


def validate_design_tool_result(result: ToolResult) -> ToolResult:
    """对工具结果做最小一致性检查。"""
    if result.success or result.error is not None:
        return result
    return ToolResult(
        success=False,
        error=ToolError(
            code=ErrorCode.UNKNOWN,
            message="设计工具返回了无错误对象的失败结果。",
        ),
    )
