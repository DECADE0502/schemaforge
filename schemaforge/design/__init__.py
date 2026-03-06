"""SchemaForge 设计编排层

器件检索、方案规划、拓扑适配、合理性检查。

Phase 4: 库驱动设计 v1
- retrieval: 器件库检索（关键字+分类+评分排序）
- planner: AI 设计规划（自然语言→模块需求列表）
- topology_adapter: 拓扑适配（DeviceModel→可渲染格式）
- rationality: 合理性检查（电压/电流/功率/兼容性）

Phase 5: PatchEngine
- patch_engine: PatchOp 执行器（set/add/remove/replace）

Phase 6: 需求澄清
- clarifier: 需求澄清器（约束检测/假设生成/问题产出）
"""

from schemaforge.design.clarifier import ClarificationResult, RequirementClarifier
from schemaforge.design.patch_engine import PatchEngine, PatchResult
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
from schemaforge.design.topology_draft import (
    NetDraft,
    TopologyDraft,
    TopologyDraftGenerator,
)

__all__ = [
    "AdaptationResult",
    "AdaptedModule",
    "ClarificationResult",
    "DesignPlan",
    "DesignPlanner",
    "DeviceRequirement",
    "DeviceRetriever",
    "ModuleRequirement",
    "NetDraft",
    "PatchEngine",
    "PatchResult",
    "RationalityChecker",
    "RationalityIssue",
    "RationalityReport",
    "RequirementClarifier",
    "RetrievalResult",
    "TopologyAdapter",
    "TopologyDraft",
    "TopologyDraftGenerator",
]
