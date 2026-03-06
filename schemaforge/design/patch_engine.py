"""PatchEngine — PatchOp 执行器

将 AI 生成的 PatchOp 操作序列应用到 DesignSessionResult 对象上，
支持 set / add / remove / replace 四种操作，并提供验证与预览功能。

用法::

    engine = PatchEngine()
    ops = [
        PatchOp(op="set", path="modules[0].parameters.c_out", value="100uF"),
        PatchOp(op="add", path="modules", value={"template": "led_indicator",
                                                   "instance_name": "led1",
                                                   "parameters": {}}),
    ]
    result = engine.apply(session_result, ops)
    if result.success:
        updated = result.modified_result
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from typing import Any

from schemaforge.agent.protocol import PatchOp
from schemaforge.workflows.design_session import DesignSessionResult


# ============================================================
# 数据模型
# ============================================================


@dataclass
class PatchResult:
    """PatchEngine 执行结果"""

    success: bool = False
    """是否有至少一个操作成功执行"""

    modified_result: DesignSessionResult | None = None
    """应用操作后的 DesignSessionResult（深拷贝）"""

    applied_ops: list[PatchOp] = field(default_factory=list)
    """成功执行的操作列表"""

    rejected_ops: list[tuple[PatchOp, str]] = field(default_factory=list)
    """被拒绝的操作列表，每项为 (PatchOp, 拒绝原因)"""

    warnings: list[str] = field(default_factory=list)
    """执行过程中的警告信息"""


# ============================================================
# 路径解析工具
# ============================================================

_INDEX_RE = re.compile(r"^(.+)\[(\d+)\]$")


def _parse_segment(segment: str) -> str | int:
    """解析路径段，返回字符串键或整数索引"""
    m = _INDEX_RE.match(segment)
    if m:
        # 如 "modules[0]" → ("modules", 0)，但这里只返回 0，上层处理 "modules"
        # 实际上 "modules[0]" 整体不会出现在 split('.') 的单个段中，
        # 除非路径如 "modules[0]" 本身就是整段
        raise ValueError(f"路径段 '{segment}' 含索引，应由 _resolve_path 处理")
    return segment


def _split_path(path: str) -> list[str | int]:
    """将 JSON 路径字符串拆分为键/索引序列

    例::
        "modules[0].parameters.c_out" → ["modules", 0, "parameters", "c_out"]
        "modules[1]"                  → ["modules", 1]
        "design_name"                 → ["design_name"]
        "modules"                     → ["modules"]
    """
    if not path:
        raise ValueError("路径不能为空")

    parts: list[str | int] = []
    for seg in path.split("."):
        if not seg:
            raise ValueError(f"路径 '{path}' 包含空段")
        # 处理含数组索引的段，如 "modules[0]" 或 "modules[0][1]"
        # 先分离基础名称和所有索引
        idx_matches = list(re.finditer(r"\[(\d+)\]", seg))
        if idx_matches:
            base = seg[: idx_matches[0].start()]
            if base:
                parts.append(base)
            elif not parts:
                raise ValueError(f"路径 '{path}' 中 '[N]' 前缺少键名")
            for m in idx_matches:
                parts.append(int(m.group(1)))
        else:
            parts.append(seg)
    return parts


# ============================================================
# PatchEngine
# ============================================================


class PatchEngine:
    """PatchOp 执行器

    对 DesignSessionResult.design_spec 执行 AI 生成的修改操作序列。
    每个操作独立执行：某操作失败不影响其他操作。
    """

    # ----------------------------------------------------------
    # 公开 API
    # ----------------------------------------------------------

    def apply(
        self,
        result: DesignSessionResult,
        ops: list[PatchOp],
    ) -> PatchResult:
        """执行操作序列，返回 PatchResult

        Args:
            result: 原始设计会话结果（不会被修改）
            ops:    要执行的 PatchOp 列表

        Returns:
            PatchResult，含修改后的 result 深拷贝
        """
        modified = copy.deepcopy(result)
        patch_result = PatchResult(modified_result=modified)

        for op in ops:
            ok, reason = self._execute_op(modified.design_spec, op)
            if ok:
                patch_result.applied_ops.append(op)
            else:
                patch_result.rejected_ops.append((op, reason))

        patch_result.success = len(patch_result.applied_ops) > 0
        return patch_result

    def validate(self, result: DesignSessionResult, ops: list[PatchOp]) -> list[str]:
        """验证操作序列，返回错误信息列表（不修改 result）

        Args:
            result: 设计会话结果
            ops:    要验证的操作列表

        Returns:
            错误信息列表，空列表表示全部合法
        """
        errors: list[str] = []
        spec = copy.deepcopy(result.design_spec)

        for i, op in enumerate(ops):
            # 校验操作类型
            if op.op not in ("set", "add", "remove", "replace"):
                errors.append(f"op[{i}]: 不支持的操作类型 '{op.op}'")
                continue

            # 校验路径可解析
            try:
                keys = _split_path(op.path)
            except ValueError as exc:
                errors.append(f"op[{i}]: 路径解析失败 — {exc}")
                continue

            # 校验路径可访问（对 set/remove/replace 有意义）
            if op.op in ("set", "remove", "replace"):
                try:
                    self._resolve_path(spec, keys)
                except (KeyError, IndexError, TypeError) as exc:
                    errors.append(f"op[{i}]: 路径 '{op.path}' 不可访问 — {exc}")

            # add 操作：路径目标必须是列表
            if op.op == "add":
                try:
                    target = self._resolve_path(spec, keys)
                    if not isinstance(target, list):
                        errors.append(f"op[{i}]: add 操作目标 '{op.path}' 不是列表")
                except (KeyError, IndexError, TypeError) as exc:
                    errors.append(f"op[{i}]: 路径 '{op.path}' 不可访问 — {exc}")

        return errors

    def preview(
        self,
        result: DesignSessionResult,
        ops: list[PatchOp],
    ) -> PatchResult:
        """预览操作结果（不修改原始 result）

        与 apply() 完全相同，但语义上表明调用方只想查看效果。
        原始 result 保证不被修改。
        """
        return self.apply(result, ops)

    # ----------------------------------------------------------
    # 内部方法
    # ----------------------------------------------------------

    def _resolve_path(
        self,
        spec: dict[str, Any],
        keys: list[str | int],
    ) -> Any:
        """沿路径键序列访问 spec，返回目标节点

        Args:
            spec: design_spec 字典（或其子节点）
            keys: _split_path() 返回的键/索引序列

        Returns:
            目标节点的值

        Raises:
            KeyError:   字典中不存在的键
            IndexError: 列表索引越界
            TypeError:  节点类型不匹配（如对非字典取键）
        """
        node: Any = spec
        for key in keys:
            if isinstance(key, int):
                if not isinstance(node, list):
                    raise TypeError(f"期望列表，实际为 {type(node).__name__}")
                if key < 0 or key >= len(node):
                    raise IndexError(f"索引 {key} 越界（列表长度 {len(node)}）")
                node = node[key]
            else:
                if not isinstance(node, dict):
                    raise TypeError(f"期望字典，实际为 {type(node).__name__}")
                if key not in node:
                    raise KeyError(f"键 '{key}' 不存在")
                node = node[key]
        return node

    def _execute_op(
        self,
        spec: dict[str, Any],
        op: PatchOp,
    ) -> tuple[bool, str]:
        """执行单个操作，返回 (成功, 失败原因)"""
        if op.op not in ("set", "add", "remove", "replace"):
            return False, f"不支持的操作类型 '{op.op}'"

        try:
            keys = _split_path(op.path)
        except ValueError as exc:
            return False, f"路径解析失败：{exc}"

        if op.op == "set":
            return self._op_set(spec, keys, op.value)
        if op.op == "add":
            return self._op_add(spec, keys, op.value)
        if op.op == "remove":
            return self._op_remove(spec, keys)
        if op.op == "replace":
            return self._op_replace(spec, keys, op.value)

        return False, "未知操作"  # pragma: no cover

    def _op_set(
        self,
        spec: dict[str, Any],
        keys: list[str | int],
        value: Any,
    ) -> tuple[bool, str]:
        """set：修改目标路径的值"""
        if not keys:
            return False, "路径为空，无法 set"

        parent_keys = keys[:-1]
        last_key = keys[-1]

        try:
            parent = self._resolve_path(spec, parent_keys) if parent_keys else spec
        except (KeyError, IndexError, TypeError) as exc:
            return False, f"父路径不可访问：{exc}"

        if isinstance(last_key, int):
            if not isinstance(parent, list):
                return False, f"父节点不是列表，无法用索引 {last_key} 赋值"
            if last_key < 0 or last_key >= len(parent):
                return False, f"索引 {last_key} 越界（列表长度 {len(parent)}）"
            parent[last_key] = value
        else:
            if not isinstance(parent, dict):
                return False, f"父节点不是字典，无法用键 '{last_key}' 赋值"
            parent[last_key] = value

        return True, ""

    def _op_add(
        self,
        spec: dict[str, Any],
        keys: list[str | int],
        value: Any,
    ) -> tuple[bool, str]:
        """add：向目标列表追加元素"""
        try:
            target = self._resolve_path(spec, keys)
        except (KeyError, IndexError, TypeError) as exc:
            return False, f"路径不可访问：{exc}"

        if not isinstance(target, list):
            return False, f"add 操作目标不是列表（实际为 {type(target).__name__}）"

        target.append(value)
        return True, ""

    def _op_remove(
        self,
        spec: dict[str, Any],
        keys: list[str | int],
    ) -> tuple[bool, str]:
        """remove：从父列表中删除指定索引元素，或从字典中删除指定键"""
        if not keys:
            return False, "路径为空，无法 remove"

        parent_keys = keys[:-1]
        last_key = keys[-1]

        try:
            parent = self._resolve_path(spec, parent_keys) if parent_keys else spec
        except (KeyError, IndexError, TypeError) as exc:
            return False, f"父路径不可访问：{exc}"

        if isinstance(last_key, int):
            if not isinstance(parent, list):
                return False, f"父节点不是列表，无法按索引 {last_key} 删除"
            if last_key < 0 or last_key >= len(parent):
                return False, f"索引 {last_key} 越界（列表长度 {len(parent)}）"
            parent.pop(last_key)
        else:
            if not isinstance(parent, dict):
                return False, f"父节点不是字典，无法删除键 '{last_key}'"
            if last_key not in parent:
                return False, f"键 '{last_key}' 不存在，无法删除"
            del parent[last_key]

        return True, ""

    def _op_replace(
        self,
        spec: dict[str, Any],
        keys: list[str | int],
        value: Any,
    ) -> tuple[bool, str]:
        """replace：替换目标路径的整个元素（与 set 语义相同，但强调整体替换）"""
        # replace 与 set 逻辑相同，只是语义层面强调整体替换
        return self._op_set(spec, keys, value)
