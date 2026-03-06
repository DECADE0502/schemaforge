"""SchemaForge 器件库模块

提供器件库的数据模型、存储层、校验、去重和服务层。
"""

from schemaforge.library.models import (
    DeviceModel,
    ExternalComponent,
    PinSide,
    SymbolDef,
    SymbolPin,
    TopologyConnection,
    TopologyDef,
)
from schemaforge.library.store import ComponentStore
from schemaforge.library.validator import (
    DeviceDraft,
    PinDraft,
    ValidationReport,
    ValidationIssue,
    Severity,
    validate_draft,
    draft_to_device_model_dict,
)
from schemaforge.library.dedupe import (
    DuplicateCheckResult,
    DuplicateMatch,
    check_duplicate,
)
from schemaforge.library.service import (
    AddDeviceResult,
    LibraryService,
)

__all__ = [
    # models
    "ComponentStore",
    "DeviceModel",
    "ExternalComponent",
    "PinSide",
    "SymbolDef",
    "SymbolPin",
    "TopologyConnection",
    "TopologyDef",
    # validator
    "DeviceDraft",
    "PinDraft",
    "ValidationReport",
    "ValidationIssue",
    "Severity",
    "validate_draft",
    "draft_to_device_model_dict",
    # dedupe
    "DuplicateCheckResult",
    "DuplicateMatch",
    "check_duplicate",
    # service
    "AddDeviceResult",
    "LibraryService",
]
