"""SchemaForge AI输出验证器

验证LLM返回的JSON是否符合设计规格要求：
- 模板名存在
- 参数完整且类型正确
- 连接的模块和网络存在
"""

from __future__ import annotations

from typing import Any

from schemaforge.core.models import DesignSpec
from schemaforge.core.templates import TEMPLATE_REGISTRY, get_template


class ValidationError:
    """验证错误"""

    def __init__(self, field: str, message: str, severity: str = "error") -> None:
        self.field = field
        self.message = message
        self.severity = severity  # "error" | "warning"

    def __repr__(self) -> str:
        return f"[{self.severity.upper()}] {self.field}: {self.message}"


class ValidationResult:
    """验证结果"""

    def __init__(self) -> None:
        self.errors: list[ValidationError] = []
        self.warnings: list[ValidationError] = []
        self.design: DesignSpec | None = None

    @property
    def is_valid(self) -> bool:
        return len(self.errors) == 0

    def add_error(self, field: str, message: str) -> None:
        self.errors.append(ValidationError(field, message, "error"))

    def add_warning(self, field: str, message: str) -> None:
        self.warnings.append(ValidationError(field, message, "warning"))

    def summary(self) -> str:
        lines: list[str] = []
        if self.is_valid:
            lines.append("✅ 验证通过")
        else:
            lines.append(f"❌ 验证失败（{len(self.errors)}个错误）")
        for e in self.errors:
            lines.append(f"  错误: {e.field} — {e.message}")
        for w in self.warnings:
            lines.append(f"  警告: {w.field} — {w.message}")
        return "\n".join(lines)


def validate_design_spec(data: dict[str, Any]) -> ValidationResult:
    """验证AI输出的设计规格

    Args:
        data: AI输出的JSON字典

    Returns:
        ValidationResult，包含错误列表和解析后的DesignSpec
    """
    result = ValidationResult()

    # 1. 基本结构检查
    if not isinstance(data, dict):
        result.add_error("root", "输出必须是JSON对象")
        return result

    if "design_name" not in data:
        result.add_error("design_name", "缺少design_name字段")

    if "modules" not in data or not isinstance(data.get("modules"), list):
        result.add_error("modules", "缺少modules字段或不是列表")
        return result

    if len(data["modules"]) == 0:
        result.add_error("modules", "modules列表不能为空")
        return result

    # 2. 验证每个模块
    instance_names: set[str] = set()

    for i, mod_data in enumerate(data["modules"]):
        prefix = f"modules[{i}]"

        if not isinstance(mod_data, dict):
            result.add_error(prefix, "模块必须是JSON对象")
            continue

        # 模板名
        template_name = mod_data.get("template", "")
        if not template_name:
            result.add_error(f"{prefix}.template", "缺少template字段")
            continue

        template = get_template(template_name)
        if template is None:
            available = ", ".join(TEMPLATE_REGISTRY.keys())
            result.add_error(
                f"{prefix}.template",
                f"未知模板'{template_name}'，可用模板: {available}",
            )
            continue

        # 实例名
        inst_name = mod_data.get("instance_name", "")
        if not inst_name:
            result.add_error(f"{prefix}.instance_name", "缺少instance_name")
        elif inst_name in instance_names:
            result.add_error(f"{prefix}.instance_name", f"实例名'{inst_name}'重复")
        else:
            instance_names.add(inst_name)

        # 参数验证（先将数值类型强制转为字符串，LLM可能返回数字而非字符串）
        params = mod_data.get("parameters", {})
        if not isinstance(params, dict):
            result.add_error(f"{prefix}.parameters", "parameters必须是对象")
            continue

        # 自动将数值参数转为字符串（LLM兼容性）
        for k, v in list(params.items()):
            if isinstance(v, (int, float)):
                params[k] = str(v)
        mod_data["parameters"] = params

        for param_name, param_def in template.parameters.items():
            if param_name not in params:
                if param_def.default:
                    # 使用默认值
                    params[param_name] = param_def.default
                    result.add_warning(
                        f"{prefix}.parameters.{param_name}",
                        f"使用默认值: {param_def.default}",
                    )
                else:
                    result.add_error(
                        f"{prefix}.parameters.{param_name}",
                        "缺少必填参数",
                    )
                    continue

            # 类型和范围检查
            value = params[param_name]
            if param_def.type == "float":
                try:
                    fval = float(value)
                    if param_def.min_val is not None and fval < param_def.min_val:
                        result.add_error(
                            f"{prefix}.parameters.{param_name}",
                            f"值{fval}小于最小值{param_def.min_val}",
                        )
                    if param_def.max_val is not None and fval > param_def.max_val:
                        result.add_error(
                            f"{prefix}.parameters.{param_name}",
                            f"值{fval}大于最大值{param_def.max_val}",
                        )
                except (ValueError, TypeError):
                    result.add_error(
                        f"{prefix}.parameters.{param_name}",
                        f"期望float类型，得到'{value}'",
                    )

            elif param_def.type == "choice" and param_def.choices:
                if str(value) not in param_def.choices:
                    result.add_error(
                        f"{prefix}.parameters.{param_name}",
                        f"值'{value}'不在可选范围{param_def.choices}中",
                    )

    # 3. 验证连接
    connections = data.get("connections", [])
    if not isinstance(connections, list):
        result.add_error("connections", "connections必须是列表")
    else:
        for j, conn in enumerate(connections):
            prefix = f"connections[{j}]"
            if not isinstance(conn, dict):
                result.add_error(prefix, "连接必须是JSON对象")
                continue

            from_mod = conn.get("from_module", "")
            to_mod = conn.get("to_module", "")

            if from_mod and from_mod not in instance_names:
                result.add_error(f"{prefix}.from_module", f"未知模块'{from_mod}'")
            if to_mod and to_mod not in instance_names:
                result.add_error(f"{prefix}.to_module", f"未知模块'{to_mod}'")

    # 4. 尝试构建DesignSpec
    if result.is_valid:
        try:
            result.design = DesignSpec(**data)
        except Exception as e:
            result.add_error("parse", f"DesignSpec解析失败: {e}")

    return result
