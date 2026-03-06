# SchemaForge Phase 5/6 — 实现文档

## 一、目标

在现有 Phase 4（库驱动设计 v1）基础上：

1. **Phase 5: 多轮修改与自动拓扑** — 支持用户对已生成的设计进行多轮迭代修改（PatchOp），支持 AI 自动生成拓扑连接（TopologyDraft）
2. **Phase 6: 系统收尾与稳定化** — GUI 集成完善、端到端测试、错误处理加固、文档更新

核心原则不变：**AI 只做决策和提问，本地工具负责执行。** 模型不直接控制状态持久化。

---

## 二、现状分析

### 已有基础

| 模块 | 文件 | 状态 | 说明 |
|------|------|------|------|
| PatchOp 协议 | `agent/protocol.py` | **已定义，未实现** | `PatchOp(op, path, value, reason)` 已有 Pydantic 模型，但无执行器 |
| 状态机 revision 态 | `workflows/state_machine.py` | **已定义，未触发** | `DESIGN_SESSION_TRANSITIONS` 包含 `revision` 状态，但 `DesignSession.run()` 从不进入 |
| TopologyAdapter | `design/topology_adapter.py` | **单次适配** | 只支持从器件库读取已有拓扑，不支持 AI 生成新拓扑 |
| Orchestrator | `agent/orchestrator.py` | **可用** | 多轮 AI 对话循环已实现，支持工具调用和用户提问 |
| DesignSession | `workflows/design_session.py` | **单次执行** | `run()` 是线性流程，无修改循环 |
| GUI 对话面板 | `gui/widgets/chat_panel.py` | **可用** | 支持用户输入和消息显示 |

### 核心缺口

1. **PatchOp 执行器** — 需要一个引擎把 `PatchOp` 应用到 `DesignSessionResult` 上
2. **多轮修改循环** — `DesignSession` 需要 `revise()` 方法，进入 revision 状态
3. **TopologyDraft** — AI 生成的拓扑草稿（连接关系），替代固定模板
4. **设计历史** — 修改前后的快照对比，支持撤销
5. **GUI 修改入口** — 对话面板中支持 "修改需求"，触发 revision 循环

---

## 三、Phase 5 架构设计

### 3.1 PatchOp 执行器

```python
# 新建 schemaforge/design/patch_engine.py

class PatchEngine:
    """PatchOp 执行器 — 将 AI 的修改指令应用到设计结果上"""
    
    def apply(self, result: DesignSessionResult, ops: list[PatchOp]) -> PatchResult:
        """应用一批 PatchOp 到设计结果
        
        支持的操作:
        - set: 设置参数值 ("modules[0].parameters.c_out" = "47μF")
        - add: 添加模块 ("modules" += new_module)
        - remove: 移除模块 ("modules[1]" 删除)
        - replace: 替换器件 ("modules[0].device" = "AMS1117-5.0")
        """
    
    def validate(self, ops: list[PatchOp], result: DesignSessionResult) -> list[str]:
        """校验 PatchOp 合法性（路径存在、值类型正确）"""
    
    def preview(self, result: DesignSessionResult, ops: list[PatchOp]) -> dict:
        """预览修改效果（dry-run），不实际修改"""
```

### 3.2 多轮修改循环（DesignSession 扩展）

```python
# 扩展 schemaforge/workflows/design_session.py

class DesignSession:
    # 现有 run() 方法不变
    
    def revise(self, user_input: str, previous_result: DesignSessionResult) -> DesignSessionResult:
        """多轮修改入口
        
        流程:
        1. revision 态 — 接收用户修改需求
        2. 调用 AI 生成 PatchOps（或重新规划）
        3. 应用 PatchOps → 重新走 validating → compiling → rendering
        4. 返回更新后的 DesignSessionResult
        """
    
    def _ai_generate_patches(self, user_input: str, current_design: dict) -> list[PatchOp]:
        """AI 分析修改需求，生成 PatchOps"""
```

### 3.3 TopologyDraft（AI 生成拓扑）

```python
# 新建 schemaforge/design/topology_draft.py

class TopologyDraft:
    """AI 生成的拓扑草稿
    
    当器件库中没有现成拓扑时，AI 根据器件 datasheet 的
    引脚定义和典型应用电路，自动生成连接关系。
    """
    
    name: str                    # 拓扑名称
    connections: list[NetDraft]  # 连接列表
    layout_hints: list[dict]     # 布局提示
    confidence: float            # AI 置信度 (0-1)
    evidence: list[EvidenceRef]  # 生成依据
    
class TopologyDraftGenerator:
    """拓扑草稿生成器"""
    
    def generate(self, device: DeviceModel, context: dict) -> TopologyDraft:
        """根据器件信息生成拓扑草稿
        
        输入: DeviceModel (含引脚定义、spec)
        输出: TopologyDraft (连接关系)
        """
    
    def validate_draft(self, draft: TopologyDraft) -> list[str]:
        """校验草稿合理性（引脚匹配、无浮空、无短路）"""
```

### 3.4 设计历史

```python
# 新建 schemaforge/design/history.py

class DesignHistory:
    """设计修改历史 — 支持快照和撤销"""
    
    def snapshot(self, result: DesignSessionResult, label: str) -> str:
        """创建快照，返回快照 ID"""
    
    def restore(self, snapshot_id: str) -> DesignSessionResult:
        """恢复到指定快照"""
    
    def diff(self, snap_a: str, snap_b: str) -> list[PatchOp]:
        """对比两个快照的差异"""
    
    @property
    def revisions(self) -> list[SnapshotInfo]:
        """所有快照列表"""
```

### 3.5 数据流（修改后）

```
[首次设计]
用户自然语言 → planner → retriever → adapter → checker → renderer → SVG

[多轮修改]
用户修改需求 → AI → PatchOps
                      ↓
              patch_engine.apply()
                      ↓
              [重新执行] checker → adapter → renderer → SVG（增量）
                      ↓
              history.snapshot()
```

---

## 四、Phase 6 架构设计

### 4.1 GUI 集成

在 `gui/pages/` 中创建新的设计页面，串联完整流程：

```
┌────────────────────────────────────────────────────┐
│  SchemaForge — AI 原理图设计                         │
├────────────┬───────────────────────────────────────┤
│  器件库     │  设计工作区                              │
│  ├─ AMS1117│  ┌─────────────────────────────────┐  │
│  ├─ LED    │  │  [SVG 原理图预览]                 │  │
│  └─ ...    │  │                                  │  │
│            │  └─────────────────────────────────┘  │
│  设计历史   │  ┌─────────────────────────────────┐  │
│  ├─ v1     │  │  [AI 对话面板]                    │  │
│  ├─ v2     │  │  用户: 帮我设计5V转3.3V稳压电路   │  │
│  └─ v3     │  │  AI: 已生成方案...                │  │
│            │  │  用户: 把输出电容改成47μF          │  │
│            │  │  AI: 已修改，重新渲染...           │  │
│            │  └─────────────────────────────────┘  │
│            │  [BOM] [SPICE] [导出]                  │
└────────────┴───────────────────────────────────────┘
```

### 4.2 错误处理加固

- 所有 AI 调用添加超时 + 重试（已有基础，需统一）
- PatchOp 应用失败时自动回滚到上一快照
- GUI 中显示友好的错误消息（非堆栈跟踪）

### 4.3 测试覆盖

- PatchEngine 单元测试：每种 op 类型
- DesignSession.revise() 集成测试
- TopologyDraft 生成 + 校验测试
- GUI 交互测试（Playwright 或手动）

---

## 五、API 设计

### DesignSession 扩展接口

| 方法 | 入参 | 出参 | 说明 |
|------|------|------|------|
| `run(user_input)` | 自然语言 | `DesignSessionResult` | 首次设计（已有） |
| `revise(user_input, prev_result)` | 修改需求 + 当前结果 | `DesignSessionResult` | 多轮修改 |
| `undo()` | 无 | `DesignSessionResult` | 撤销到上一版本 |
| `get_history()` | 无 | `list[SnapshotInfo]` | 获取修改历史 |

### PatchEngine 接口

| 方法 | 入参 | 出参 | 说明 |
|------|------|------|------|
| `apply(result, ops)` | 设计结果 + PatchOp 列表 | `PatchResult` | 应用修改 |
| `validate(ops, result)` | PatchOp 列表 + 当前结果 | `list[str]` | 校验合法性 |
| `preview(result, ops)` | 设计结果 + PatchOp 列表 | `dict` | 预览修改 |

### TopologyDraftGenerator 接口

| 方法 | 入参 | 出参 | 说明 |
|------|------|------|------|
| `generate(device, context)` | 器件模型 + 上下文 | `TopologyDraft` | AI 生成拓扑 |
| `validate_draft(draft)` | 拓扑草稿 | `list[str]` | 校验合理性 |
| `draft_to_topology(draft)` | 拓扑草稿 | `TopologyDef` | 转为器件库格式 |

---

## 六、实现顺序

```
Phase 5 (4 个 Task):
  Task 1: PatchEngine — PatchOp 执行器 + 校验 + 预览
  Task 2: DesignHistory — 快照/恢复/diff
  Task 3: DesignSession.revise() — 多轮修改循环 + AI 生成 PatchOps
  Task 4: TopologyDraft — AI 自动生成拓扑

Phase 6 (4 个 Task):
  Task 5: GUI 设计页面 — 完整设计工作区 (SVG预览 + 对话 + 历史)
  Task 6: GUI 多轮修改交互 — 修改需求输入 + 实时更新
  Task 7: 错误处理加固 + 测试覆盖
  Task 8: 文档更新 + 全量回归测试
```
