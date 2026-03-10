"""系统级布局规格（相对约束，不含绝对坐标）。

本模块定义 ``SystemLayoutSpec``，描述模块排列的 *相对* 约束：
- 电源链模块的左→右顺序
- 控制模块的放置提示
- 间距参数

绝对坐标由渲染器根据此规格计算（Task 9）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from schemaforge.system.models import SystemDesignIR


# ============================================================
# 布局规格
# ============================================================


@dataclass
class SystemLayoutSpec:
    """系统级布局的相对约束规格。

    不包含绝对坐标——只描述模块排列顺序、放置提示和间距参数。
    渲染器可通过 ``getattr(spec, "module_positions", None)`` 读取
    已计算的绝对位置（由 Task 9 的 ``compute_positions`` 填充）。
    """

    # 电源链模块的左→右排列顺序（module_id 列表）
    module_order: list[str] = field(default_factory=list)

    # 控制模块放置提示: module_id → hint
    # hint 示例: "below_center", "right_of:mcu1"
    control_modules: dict[str, str] = field(default_factory=dict)

    # 间距参数
    module_spacing: float = 20.0       # 电源链模块间水平间距
    control_y_gap: float = 16.0        # 控制行与电源链的垂直间距
    canvas_padding: float = 2.0        # 画布边缘留白

    # 已计算的绝对位置 (由 compute_positions 填充)
    module_positions: dict[str, tuple[float, float]] | None = None

    # --- 模块尺寸估算 (与 rendering._estimate_module_bbox 保持一致) ---
    _SIZE_ESTIMATES: dict[str, tuple[float, float]] = field(
        default_factory=lambda: {
            "buck": (10.0, 4.5),
            "ldo": (10.0, 4.5),
            "boost": (10.0, 4.5),
            "flyback": (10.0, 4.5),
            "sepic": (10.0, 4.5),
            "mcu": (6.0, 4.0),
            "led": (2.5, 4.5),
            "_default": (4.0, 3.0),
        },
        repr=False,
    )

    def _module_size(self, category: str) -> tuple[float, float]:
        """Return (width, height) estimate for *category*."""
        return self._SIZE_ESTIMATES.get(
            category.lower(), self._SIZE_ESTIMATES["_default"],
        )

    # ------------------------------------------------------------------
    # compute_positions — resolve relative constraints to (x, y)
    # ------------------------------------------------------------------

    def compute_positions(
        self,
        ir: SystemDesignIR | None = None,
    ) -> dict[str, tuple[float, float]]:
        """Resolve relative constraints to absolute ``(x, y)`` coordinates.

        * Power chain modules are laid out left → right.
        * Control modules are placed below the power chain center,
          with LED modules relocated next to their controlling MCU.
        * Results are stored in ``self.module_positions`` for the renderer.

        Parameters
        ----------
        ir : SystemDesignIR | None
            If provided, used to look up ``resolved_category`` for LED
            golden-path detection.  Optional — if absent, categories are
            inferred from module_id heuristics.

        Returns
        -------
        dict[str, tuple[float, float]]
            module_id → (x, y)
        """
        positions: dict[str, tuple[float, float]] = {}

        # --- 1. Power chain (left → right) ---
        for idx, mid in enumerate(self.module_order):
            x = self.canvas_padding + idx * self.module_spacing
            positions[mid] = (x, 0.0)

        # --- 2. Determine power chain center_x ---
        if self.module_order:
            first_x = positions[self.module_order[0]][0]
            last_x = positions[self.module_order[-1]][0]
            center_x = (first_x + last_x) / 2.0
        else:
            center_x = self.canvas_padding

        # --- 3. Build category lookup ---
        def _category_of(mid: str) -> str:
            """Best-effort category for *mid*."""
            if ir is not None:
                inst = ir.module_instances.get(mid)
                if inst is not None:
                    return inst.resolved_category.lower()
            # Heuristic fallback: derive from module_id prefix
            for prefix in ("led", "mcu", "buck", "ldo", "boost"):
                if mid.lower().startswith(prefix):
                    return prefix
            return ""

        # --- 4. Identify MCU in control modules (for LED golden-path) ---
        mcu_mid: str | None = None
        for mid in self.control_modules:
            if _category_of(mid) == "mcu":
                mcu_mid = mid
                break

        # --- 5. Control modules ---
        # Collect control module IDs, rewriting hints for LED golden path.
        control_items: list[tuple[str, str]] = []
        for mid, hint in sorted(self.control_modules.items()):
            cat = _category_of(mid)
            # Golden path: LED placed right of MCU (if MCU exists)
            if cat == "led" and hint == "below_center" and mcu_mid is not None:
                hint = f"right_of:{mcu_mid}"
            control_items.append((mid, hint))

        # First pass: place "below_center" modules (spreading horizontally).
        below_center_ids: list[str] = [
            mid for mid, hint in control_items if hint == "below_center"
        ]
        if below_center_ids:
            total_span = (len(below_center_ids) - 1) * (self.module_spacing / 2.0)
            start_x = center_x - total_span / 2.0
            for i, mid in enumerate(below_center_ids):
                x = start_x + i * (self.module_spacing / 2.0)
                y = -self.control_y_gap
                positions[mid] = (x, y)

        # Second pass: place "right_of:REF" modules.
        for mid, hint in control_items:
            if hint.startswith("right_of:"):
                ref_id = hint.split(":", 1)[1]
                if ref_id in positions:
                    ref_x, ref_y = positions[ref_id]
                    x = ref_x + self.module_spacing
                    positions[mid] = (x, ref_y)
                else:
                    # Fallback: put next to last placed control module
                    x = center_x + self.module_spacing
                    positions[mid] = (x, -self.control_y_gap)

        # --- 6. Overlap resolution ---
        self._resolve_overlaps(positions, ir)

        self.module_positions = positions
        return positions

    # ------------------------------------------------------------------

    def _resolve_overlaps(
        self,
        positions: dict[str, tuple[float, float]],
        ir: SystemDesignIR | None,
    ) -> None:
        """Nudge modules so that no bounding boxes overlap."""

        def _cat(mid: str) -> str:
            if ir is not None:
                inst = ir.module_instances.get(mid)
                if inst is not None:
                    return inst.resolved_category.lower()
            for prefix in ("led", "mcu", "buck", "ldo", "boost"):
                if mid.lower().startswith(prefix):
                    return prefix
            return ""

        # Build list of (mid, x, y, w, h) sorted by x then y.
        items: list[tuple[str, float, float, float, float]] = []
        for mid, (x, y) in positions.items():
            w, h = self._module_size(_cat(mid))
            items.append((mid, x, y, w, h))
        items.sort(key=lambda t: (t[2], t[1]))  # sort by y then x

        # Simple greedy: for each pair, if they overlap push the right one.
        changed = True
        max_iters = len(items) * 3
        iters = 0
        while changed and iters < max_iters:
            changed = False
            iters += 1
            for i in range(len(items)):
                for j in range(i + 1, len(items)):
                    mi, xi, yi, wi, hi = items[i]
                    mj, xj, yj, wj, hj = items[j]
                    # Overlap test (AABB)
                    margin = 1.0  # extra breathing room
                    if (
                        xi < xj + wj + margin
                        and xj < xi + wi + margin
                        and yi - hi < yj + margin
                        and yj - hj < yi + margin
                    ):
                        # Push j to the right of i
                        new_xj = xi + wi + margin
                        if new_xj != xj:
                            positions[mj] = (new_xj, yj)
                            items[j] = (mj, new_xj, yj, wj, hj)
                            changed = True


# ============================================================
# 默认布局工厂
# ============================================================


def create_default_layout(ir: SystemDesignIR) -> SystemLayoutSpec:
    """根据 SystemDesignIR 生成默认布局规格。

    分类逻辑：
    - ``resolved_category`` 属于电源类别 → 电源链，按拓扑排序
    - 其余 → 控制模块，默认放置提示 ``"below_center"``

    拓扑排序使用 ``ir.connections`` 中 ``RULE_POWER_SUPPLY`` 连接
    推导上下游关系；无连接信息时回退到 module_id 字典序。
    """
    from schemaforge.system.rendering import _POWER_CATEGORIES

    # --- 分类 ---
    power_ids: list[str] = []
    control_ids: list[str] = []

    for mid, inst in ir.module_instances.items():
        if inst.resolved_category.lower() in _POWER_CATEGORIES:
            power_ids.append(mid)
        else:
            control_ids.append(mid)

    # --- 电源链拓扑排序 ---
    module_order = _topo_sort_power(power_ids, ir)

    # --- 控制模块默认提示 ---
    control_modules: dict[str, str] = {
        mid: "below_center" for mid in sorted(control_ids)
    }

    spec = SystemLayoutSpec(
        module_order=module_order,
        control_modules=control_modules,
    )
    spec.compute_positions(ir)
    return spec


# ============================================================
# 内部: 电源链拓扑排序
# ============================================================


def _topo_sort_power(
    power_ids: list[str],
    ir: SystemDesignIR,
) -> list[str]:
    """按供电依赖关系对电源链模块做拓扑排序。

    与 ``rendering._order_power_chain`` 逻辑一致：
    通过 ``RULE_POWER_SUPPLY`` 连接构建 dst→src 映射，
    先访问上游再访问自身，确保确定性（字典序遍历）。
    """
    if not power_ids:
        return []

    power_set = set(power_ids)

    # dst_module → src_module（电源供给方向）
    fed_by: dict[str, str] = {}
    for conn in ir.connections:
        if conn.rule_id == "RULE_POWER_SUPPLY":
            src_mod = conn.src_port.module_id
            dst_mod = conn.dst_port.module_id
            fed_by[dst_mod] = src_mod

    ordered: list[str] = []
    visited: set[str] = set()
    in_progress: set[str] = set()  # 检测循环依赖

    def _visit(mid: str) -> None:
        if mid in visited or mid not in power_set:
            return
        if mid in in_progress:
            # 检测到循环依赖，跳过避免无限递归
            return
        in_progress.add(mid)
        upstream = fed_by.get(mid)
        if upstream and upstream not in visited:
            _visit(upstream)
        in_progress.discard(mid)
        visited.add(mid)
        ordered.append(mid)

    for mid in sorted(power_set):
        _visit(mid)

    return ordered
