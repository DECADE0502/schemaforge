"""共享数据模型

跨子系统复用的数据类，避免循环依赖。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class MissingModule:
    """缺失的器件模块 — 用于 GUI 展示待办卡片

    当设计会话在检索阶段发现某些模块在器件库中没有匹配时，
    将缺失信息打包为 MissingModule 返回给 GUI，供用户补录。
    """

    role: str = ""              # 模块角色 (e.g. "main_regulator")
    category: str = ""          # 期望分类 (e.g. "buck")
    description: str = ""       # 功能描述
    part_number: str = ""       # 指定料号（如有）
    parameters: dict[str, str] = field(default_factory=dict)
    search_error: str = ""      # 检索失败原因

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "category": self.category,
            "description": self.description,
            "part_number": self.part_number,
            "parameters": self.parameters,
            "search_error": self.search_error,
        }
