"""SchemaForge 参考设计库数据模型

定义参考设计库的核心模型：
- ReferenceDesign -- 经过验证的成熟电路设计骨架
- ReferenceDesignStore -- 参考设计库存储与检索
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


# ============================================================
# 参考设计模型
# ============================================================


class ReferenceDesign(BaseModel):
    """参考设计 — 经过验证的成熟电路设计骨架"""

    ref_id: str  # 唯一标识 ("ref_ldo_basic", "ref_led_indicator", etc.)
    name: str  # 名称 ("基础LDO稳压电路")
    description: str = ""  # 描述

    # 适用条件
    applicable_categories: list[str] = Field(default_factory=list)
    # ["ldo"], ["led"], ["ldo", "led"], etc.
    applicable_roles: list[str] = Field(default_factory=list)
    # ["main_regulator"], ["power_led"], etc.
    applicable_scenarios: list[str] = Field(default_factory=list)
    # ["低压差稳压", "电池供电", etc.]

    # 约束范围
    constraints: dict[str, str] = Field(default_factory=dict)
    # {"v_in_range": "3.5V-15V", "v_out": "3.3V", "i_out_max": "1A"}

    # 拓扑骨架
    module_roles: list[str] = Field(default_factory=list)
    # 包含的模块角色 ["main_regulator", "power_led"]
    topology_template: dict[str, Any] = Field(default_factory=dict)
    # 拓扑描述 {"modules": [...], "connections": [...]}

    # 外围件
    required_components: list[str] = Field(default_factory=list)
    # ["输入电容10uF", "输出电容22uF"]
    optional_components: list[str] = Field(default_factory=list)
    # ["TVS二极管（输入保护）"]

    # 可替换部分
    replaceable_devices: dict[str, list[str]] = Field(default_factory=dict)
    # {"main_regulator": ["AMS1117-3.3", "LM1117-3.3", "ME6211"]}

    # 经验注释
    design_notes: list[str] = Field(default_factory=list)
    # ["输入输出电容需紧贴IC", "注意散热"]
    layout_tips: list[str] = Field(default_factory=list)
    bringup_tips: list[str] = Field(default_factory=list)

    # 元数据
    confidence: float = 1.0  # 0-1, how battle-tested this design is
    source: str = "manual"  # "manual", "extracted", "community"
    tags: list[str] = Field(default_factory=list)


# ============================================================
# 参考设计库存储
# ============================================================


class ReferenceDesignStore:
    """参考设计库存储与检索"""

    def __init__(self, store_dir: Path) -> None:
        """初始化参考设计库存储

        Args:
            store_dir: 参考设计JSON文件目录
        """
        self.store_dir = Path(store_dir)
        self.store_dir.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, ReferenceDesign] = {}
        self._loaded = False

    # ----------------------------------------------------------
    # 加载与缓存
    # ----------------------------------------------------------

    def load_all(self) -> list[ReferenceDesign]:
        """加载所有参考设计

        Returns:
            参考设计列表（按ref_id排序）
        """
        self._cache.clear()
        for fp in sorted(self.store_dir.glob("*.json")):
            try:
                raw = fp.read_text(encoding="utf-8")
                design = ReferenceDesign.model_validate(json.loads(raw))
                self._cache[design.ref_id] = design
            except Exception:
                # 跳过无法解析的文件
                continue
        self._loaded = True
        return list(self._cache.values())

    def _ensure_loaded(self) -> None:
        """确保设计库已加载"""
        if not self._loaded:
            self.load_all()

    # ----------------------------------------------------------
    # CRUD
    # ----------------------------------------------------------

    def get(self, ref_id: str) -> ReferenceDesign | None:
        """按ID获取参考设计

        Args:
            ref_id: 参考设计唯一标识

        Returns:
            ReferenceDesign 或 None（找不到时）
        """
        self._ensure_loaded()
        return self._cache.get(ref_id)

    def save(self, design: ReferenceDesign) -> Path:
        """保存参考设计到JSON文件

        Args:
            design: 要保存的参考设计

        Returns:
            保存的JSON文件路径
        """
        filepath = self.store_dir / f"{design.ref_id}.json"
        data = design.model_dump()
        filepath.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        self._cache[design.ref_id] = design
        self._loaded = True
        return filepath

    # ----------------------------------------------------------
    # 检索
    # ----------------------------------------------------------

    def search(
        self,
        category: str = "",
        role: str = "",
        scenario: str = "",
    ) -> list[ReferenceDesign]:
        """按条件搜索参考设计

        Args:
            category: 按类别筛选（匹配 applicable_categories）
            role: 按角色筛选（匹配 applicable_roles）
            scenario: 按场景筛选（匹配 applicable_scenarios）

        Returns:
            匹配的参考设计列表
        """
        self._ensure_loaded()
        results: list[ReferenceDesign] = []

        for design in self._cache.values():
            if category and category not in design.applicable_categories:
                continue
            if role and role not in design.applicable_roles:
                continue
            if scenario and scenario not in design.applicable_scenarios:
                continue
            results.append(design)

        return results

    def find_best_match(
        self,
        categories: list[str],
        roles: list[str],
    ) -> ReferenceDesign | None:
        """找到最佳匹配的参考设计（按匹配度打分）

        打分规则：
        - 每匹配一个 category: +2分
        - 每匹配一个 role: +1分

        Args:
            categories: 目标类别列表
            roles: 目标角色列表

        Returns:
            得分最高的参考设计，或 None（无匹配时）
        """
        self._ensure_loaded()

        best: ReferenceDesign | None = None
        best_score = 0

        for design in self._cache.values():
            cat_overlap = len(set(categories) & set(design.applicable_categories))
            role_overlap = len(set(roles) & set(design.applicable_roles))
            score = cat_overlap * 2 + role_overlap

            if score > best_score:
                best_score = score
                best = design

        return best if best_score > 0 else None
