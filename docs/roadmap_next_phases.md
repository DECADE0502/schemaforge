# SchemaForge 后续开发超级详细路线文档

> 此文档由用户提供，作为后续开发的执行约束和验收标准。

## 0. 文档用途

这份文档不是概念讨论，而是 **执行路线图 + 开发约束 + 阶段验收清单**。

---

## 当前进度对照

### Phase 0 已完成项 ✅
- `schemaforge/common/events.py` ✅
- `schemaforge/common/errors.py` ✅
- `schemaforge/common/progress.py` ✅
- `schemaforge/common/session_store.py` ✅
- `schemaforge/agent/protocol.py` ✅
- `schemaforge/agent/tool_registry.py` ✅
- `schemaforge/agent/orchestrator.py` ✅
- `schemaforge/ingest/pdf_parser.py` ✅
- `schemaforge/ingest/image_recognizer.py` ✅
- `schemaforge/ingest/easyeda_provider.py` ✅
- 目录结构: common/, agent/, ingest/, workflows/, design/ ✅

### 当前阶段: Phase 1 — 共享基础设施
需要补充:
- `schemaforge/workflows/state_machine.py`
- GUI 日志/进度/对话面板
- `schemaforge/gui/` 拆分
- 基础单测
- 全量回归验证
