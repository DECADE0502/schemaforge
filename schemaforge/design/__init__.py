"""SchemaForge 设计编排层

器件检索、方案规划、拓扑适配、合理性检查。

Phase 4: 库驱动设计 v1
- retrieval: 器件库检索（关键字+分类+评分排序）
- planner: AI 设计规划（自然语言→模块需求列表）
- topology_adapter: 拓扑适配（DeviceModel→可渲染格式）
- rationality: 合理性检查（电压/电流/功率/兼容性）
"""

from schemaforge.design.planner import DesignPlan, DesignPlanner, ModuleRequirement
from schemaforge.design.rationality import (
    RationalityChecker,
    RationalityIssue,
    RationalityReport,
)
from schemaforge.design.retrieval import (
    DeviceRequirement,
    DeviceRetriever,
    RetrievalResult,
)
from schemaforge.design.topology_adapter import (
    AdaptationResult,
    AdaptedModule,
    TopologyAdapter,
)

__all__ = [
    "AdaptationResult",
    "AdaptedModule",
    "DesignPlan",
    "DesignPlanner",
    "DeviceRequirement",
    "DeviceRetriever",
    "ModuleRequirement",
    "RationalityChecker",
    "RationalityIssue",
    "RationalityReport",
    "RetrievalResult",
    "TopologyAdapter",
]
