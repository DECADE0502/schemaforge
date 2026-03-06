# SchemaForge Phase 5/6 — 任务拆分

> 基于 `schemaforge-implementation.md` 拆分，共 8 个任务。
> 每个任务独立可交付，有明确的验收标准。

---

## Task 1: PatchEngine — PatchOp 执行器

**目标**：实现 `PatchOp` 执行引擎，将 AI 生成的修改指令应用到 `DesignSessionResult` 上。

**涉及文件**：
- 新建 `schemaforge/design/patch_engine.py` — 核心执行引擎
- `schemaforge/agent/protocol.py` — PatchOp 模型（已有，可能需微调）
- 新建 `tests/test_patch_engine.py` — 单元测试

**具体工作**：

1. 实现 `PatchEngine` 类，包含以下方法：
   - `apply(result: DesignSessionResult, ops: list[PatchOp]) -> PatchResult` — 应用 PatchOp 列表
   - `validate(ops: list[PatchOp], result: DesignSessionResult) -> list[str]` — 校验合法性
   - `preview(result: DesignSessionResult, ops: list[PatchOp]) -> dict` — 预览修改效果（dry-run）

2. 支持 4 种操作类型：
   - `set`: 设置参数值，路径如 `"modules[0].parameters.c_out"` → `"47μF"`
   - `add`: 添加模块到 `modules` 列表
   - `remove`: 移除指定索引的模块
   - `replace`: 替换模块的器件（重新检索+适配）

3. 路径解析：实现 JSON path 风格的路径解析（支持 `modules[0].parameters.key` 格式），操作 `DesignSessionResult.design_spec` 字典

4. 定义 `PatchResult` 数据类：
   - `success: bool`
   - `modified_result: DesignSessionResult`
   - `applied_ops: list[PatchOp]` — 实际执行的操作
   - `rejected_ops: list[tuple[PatchOp, str]]` — 被拒绝的操作及原因
   - `warnings: list[str]`

5. 错误处理：
   - 路径不存在 → reject 该 op，不中断其他 op
   - 类型不匹配 → reject 并报告
   - `remove` 越界 → reject

**验收标准**：
- [ ] 编译通过，`python -m pytest tests/test_patch_engine.py -v` 全部 PASS
- [ ] `set` 操作能正确修改嵌套字典中的值
- [ ] `add` 操作能向 modules 列表追加新模块
- [ ] `remove` 操作能删除指定索引的模块
- [ ] `replace` 操作能替换模块的器件信息
- [ ] 非法路径不崩溃，返回 rejected_ops 并附带原因
- [ ] `validate()` 能预先检测非法操作而不执行
- [ ] `preview()` 返回修改后的预览但不改变原始 result
- [ ] 测试覆盖 ≥ 10 个用例（每种 op 至少 2 个 + 边界情况）

---

## Task 2: DesignHistory — 设计历史快照

**目标**：实现设计修改历史管理，支持快照、恢复和 diff 对比。

**涉及文件**：
- 新建 `schemaforge/design/history.py` — 历史管理
- 新建 `tests/test_design_history.py` — 单元测试

**具体工作**：

1. 实现 `DesignHistory` 类：
   - `snapshot(result: DesignSessionResult, label: str) -> str` — 创建快照，返回快照 ID（UUID）
   - `restore(snapshot_id: str) -> DesignSessionResult` — 恢复到指定快照（深拷贝返回）
   - `diff(snap_a: str, snap_b: str) -> list[PatchOp]` — 对比两个快照差异，输出 PatchOp 列表
   - `undo() -> DesignSessionResult | None` — 撤销到上一个快照
   - `revisions -> list[SnapshotInfo]` — 所有快照信息列表

2. 定义 `SnapshotInfo` 数据类：
   - `snapshot_id: str`
   - `label: str`
   - `timestamp: str` (ISO 8601)
   - `module_count: int`
   - `svg_count: int`

3. 存储策略：内存存储（`dict[str, DesignSessionResult]`），使用 `copy.deepcopy` 隔离快照

4. `diff()` 实现：递归对比 `design_spec` 字典，生成 `PatchOp(op="set", path=..., value=...)` 列表

**依赖**：Task 1（使用 PatchOp 模型）

**验收标准**：
- [ ] 编译通过，`python -m pytest tests/test_design_history.py -v` 全部 PASS
- [ ] `snapshot()` 创建快照后 `revisions` 列表长度 +1
- [ ] `restore()` 返回的结果与快照时一致，且是深拷贝（修改不影响原快照）
- [ ] `undo()` 正确恢复到上一个快照
- [ ] `undo()` 在无历史时返回 None
- [ ] `diff()` 能检测参数值变更并生成正确的 PatchOp
- [ ] 测试覆盖 ≥ 8 个用例

---

## Task 3: DesignSession.revise() — 多轮修改循环

**目标**：扩展 `DesignSession`，支持基于已有设计的多轮迭代修改。

**涉及文件**：
- `schemaforge/workflows/design_session.py` — 扩展现有类
- `schemaforge/design/patch_engine.py` — 使用 Task 1 的 PatchEngine
- `schemaforge/design/history.py` — 使用 Task 2 的 DesignHistory
- `schemaforge/ai/prompts.py` — 新增修改场景的 prompt 模板
- 新建 `tests/test_design_revision.py` — 集成测试

**具体工作**：

1. 在 `DesignSession` 中新增方法：
   - `revise(user_input: str, previous_result: DesignSessionResult) -> DesignSessionResult`
     - 进入 `revision` 状态（使用已有的状态转换）
     - 调用 AI 分析修改需求，生成 PatchOps
     - 通过 PatchEngine 应用修改
     - 重新执行 validating → compiling → rendering 流程
     - 在 DesignHistory 中创建快照
   - `undo() -> DesignSessionResult | None` — 委托给 DesignHistory
   - `get_history() -> list[SnapshotInfo]` — 获取修改历史

2. AI 修改分析 prompt：
   - 输入：当前设计的 design_spec JSON + 用户修改需求
   - 输出：PatchOp 列表（结构化 JSON）
   - 在 `ai/prompts.py` 中新增 `REVISION_SYSTEM_PROMPT` 和 `build_revision_message()`

3. 修改策略判断：
   - 小修改（参数调整）→ PatchOp `set`
   - 添加模块 → PatchOp `add` + 重新走 planner（部分规划）
   - 删除模块 → PatchOp `remove`
   - 替换器件 → PatchOp `replace` + 重新检索
   - AI 判断不了 → 全部重新规划（fallback 到 `run()`）

4. 状态机集成：
   - `revision` → `planning`（需要重新规划时）
   - `revision` → `compiling`（只需重新适配时）
   - `revision` → `done`（无需重新渲染，如只是添加注释）

**依赖**：Task 1, Task 2

**验收标准**：
- [ ] 编译通过，`python -m pytest tests/test_design_revision.py -v` 全部 PASS
- [ ] `revise("把输出电容改成47μF", prev_result)` 能正确修改参数并重新渲染
- [ ] `revise("加一个LED指示灯", prev_result)` 能添加新模块
- [ ] `revise("去掉LED", prev_result)` 能移除模块
- [ ] 修改后自动创建历史快照
- [ ] `undo()` 能恢复到修改前的状态
- [ ] 状态机正确经过 `revision` 状态
- [ ] AI prompt 中包含当前设计的上下文信息
- [ ] Mock 模式下可走通完整修改流程
- [ ] 测试覆盖 ≥ 8 个用例（含 mock AI + 各种修改场景）

---

## Task 4: TopologyDraft — AI 自动生成拓扑

**目标**：当器件库中没有现成拓扑时，AI 根据器件引脚和 datasheet 信息自动生成拓扑连接草稿。

**涉及文件**：
- 新建 `schemaforge/design/topology_draft.py` — 拓扑草稿模型 + 生成器
- `schemaforge/design/topology_adapter.py` — 扩展支持 TopologyDraft 输入
- `schemaforge/ai/prompts.py` — 新增拓扑生成 prompt
- `schemaforge/library/models.py` — 可能需要扩展 TopologyDef（参考）
- 新建 `tests/test_topology_draft.py` — 单元测试

**具体工作**：

1. 定义 `TopologyDraft` 数据模型：
   ```python
   @dataclass
   class NetDraft:
       name: str                    # 网络名 ("VIN", "VOUT", "GND", ...)
       pin_connections: list[str]   # 引脚连接 ("U1.VIN", "C1.1", ...)
       is_power: bool = False
       is_ground: bool = False
   
   @dataclass
   class TopologyDraft:
       name: str
       description: str
       nets: list[NetDraft]
       components: list[dict]       # 组件实例 (ref, type, parameters)
       layout_hints: list[dict]
       confidence: float = 0.8
       evidence: list[str] = field(default_factory=list)
   ```

2. 实现 `TopologyDraftGenerator` 类：
   - `generate(device: DeviceModel, context: dict) -> TopologyDraft`
     - 读取器件引脚定义（PinDef 列表）
     - 构建 prompt：引脚信息 + 器件规格 + 应用场景
     - AI 输出结构化的连接关系
     - 解析为 TopologyDraft
   - `validate_draft(draft: TopologyDraft) -> list[str]`
     - 检查所有 required 引脚已连接
     - 检查无悬空网络
     - 检查电源/地网络的引脚类型匹配
   - `draft_to_topology(draft: TopologyDraft, device: DeviceModel) -> TopologyDef`
     - 将草稿转换为器件库的 TopologyDef 格式
     - 可选：写回器件库（丰富库数据）

3. Mock 生成器：基于器件分类（ldo, led, divider）返回预设拓扑草稿

4. 在 `TopologyAdapter` 中增加 fallback 逻辑：
   - 优先使用器件库已有拓扑
   - 若无拓扑 → 调用 TopologyDraftGenerator 生成
   - 生成结果经过 validate 后使用

**依赖**：无（与 Task 1-3 并行）

**验收标准**：
- [ ] 编译通过，`python -m pytest tests/test_topology_draft.py -v` 全部 PASS
- [ ] `TopologyDraft` 模型能正确序列化/反序列化
- [ ] Mock 生成器能为 LDO 类器件生成合理的拓扑草稿
- [ ] `validate_draft()` 能检测悬空引脚并报告
- [ ] `validate_draft()` 能检测电源/地类型不匹配
- [ ] `draft_to_topology()` 输出的 TopologyDef 可被现有 TopologyAdapter 使用
- [ ] TopologyAdapter fallback 逻辑工作：有库拓扑用库的，没有则生成
- [ ] 测试覆盖 ≥ 10 个用例

---

## Task 5: GUI 设计页面 — 完整设计工作区

**目标**：创建新的设计页面，集成 SVG 预览、AI 对话、设计历史，形成完整的交互界面。

**涉及文件**：
- 新建 `schemaforge/gui/pages/design_page.py` — 设计工作区页面
- 新建 `schemaforge/gui/widgets/svg_preview.py` — SVG 预览控件
- 新建 `schemaforge/gui/widgets/history_panel.py` — 设计历史面板
- `schemaforge/gui/widgets/chat_panel.py` — 复用现有对话面板
- `gui.py` — 主窗口集成新页面

**具体工作**：

1. **SVG 预览控件** (`svg_preview.py`)：
   - 基于 `QSvgWidget` 显示渲染后的 SVG
   - 支持缩放、平移
   - 支持加载本地 SVG 文件路径
   - 多个 SVG 时用 Tab 页切换

2. **设计历史面板** (`history_panel.py`)：
   - `QListWidget` 显示快照列表（版本号、标签、时间）
   - 点击快照恢复到对应版本
   - 当前版本高亮

3. **设计工作区页面** (`design_page.py`)：
   - 左侧：器件库浏览 + 设计历史
   - 中央：SVG 预览
   - 右侧/底部：AI 对话面板
   - 底部工具栏：BOM、SPICE、导出按钮
   - 使用 `QSplitter` 实现可调整布局

4. **主窗口集成** (`gui.py`)：
   - 新增 Tab 或导航入口："设计工作区"
   - 与现有 "器件库管理" 并列

5. **工作流线程** — 设计执行在后台线程（复用 `gui/workers/workflow_worker.py` 模式），通过信号更新 GUI

**依赖**：Task 3（DesignSession.revise）, Task 2（DesignHistory）

**验收标准**：
- [ ] 编译通过，GUI 启动无异常 (`python gui.py`)
- [ ] 设计工作区页面可见，布局与方案文档一致
- [ ] SVG 预览控件能加载并显示 SVG 文件
- [ ] 设计历史面板显示快照列表
- [ ] 对话面板可输入文字并显示消息
- [ ] BOM/SPICE 导出按钮可点击（即使功能暂未串联）
- [ ] 页面间切换（器件库 ↔ 设计工作区）正常

---

## Task 6: GUI 多轮修改交互

**目标**：在设计工作区中实现完整的多轮修改交互流程。

**涉及文件**：
- `schemaforge/gui/pages/design_page.py` — 交互逻辑
- 新建 `schemaforge/gui/workers/design_worker.py` — 设计执行后台线程
- `schemaforge/gui/widgets/chat_panel.py` — 可能需扩展支持修改上下文

**具体工作**：

1. **首次设计流程**：
   - 用户在对话面板输入需求 → 后台调用 `DesignSession.run()`
   - 进度回调更新 GUI（progress bar + 对话消息）
   - 完成后加载 SVG 到预览 + 显示 BOM/SPICE
   - 自动创建 v1 快照

2. **修改流程**：
   - 用户在对话面板输入修改需求 → 后台调用 `DesignSession.revise()`
   - 实时显示 AI 的修改分析过程
   - 完成后更新 SVG 预览 + 刷新历史列表
   - 自动创建 vN+1 快照

3. **撤销流程**：
   - 用户点击历史面板的旧版本 → 调用 `DesignSession.undo()` 或 `restore()`
   - SVG 预览切换到旧版本
   - 对话面板显示 "已恢复到 vN"

4. **导出流程**：
   - BOM 按钮 → 弹出 BOM Markdown 文本（或保存文件选择器）
   - SPICE 按钮 → 弹出 SPICE 网表文本
   - "导出全部" → 保存 SVG + BOM + SPICE 到指定目录

5. **后台线程** (`design_worker.py`)：
   - 继承 `QThread`
   - 信号：`progress(str, int)`, `finished(DesignSessionResult)`, `error(str)`
   - 支持 `run` 模式和 `revise` 模式

**依赖**：Task 5, Task 3

**验收标准**：
- [ ] 编译通过，GUI 启动无异常
- [ ] 输入需求后能触发设计流程并显示进度
- [ ] 设计完成后 SVG 预览正确显示
- [ ] 输入修改需求后能触发修改流程并更新 SVG
- [ ] 历史面板显示所有版本，点击可恢复
- [ ] 撤销操作正常工作
- [ ] BOM/SPICE 导出弹窗或文件保存正常
- [ ] 后台线程不阻塞 GUI
- [ ] Mock 模式下可走通完整的 设计→修改→撤销 流程

---

## Task 7: 错误处理加固 + 测试覆盖

**目标**：全面加固错误处理，补充测试覆盖，确保系统健壮性。

**涉及文件**：
- `schemaforge/design/patch_engine.py` — 加固边界情况
- `schemaforge/workflows/design_session.py` — 加固异常路径
- `schemaforge/design/topology_draft.py` — 加固 AI 输出解析
- `schemaforge/agent/orchestrator.py` — 添加超时控制
- 新建 `tests/test_integration.py` — 端到端集成测试
- 现有测试文件 — 补充边界用例

**具体工作**：

1. **PatchEngine 加固**：
   - 嵌套路径中的数组越界保护
   - `replace` 操作时器件检索失败的 fallback
   - 同一路径的冲突操作检测

2. **DesignSession 加固**：
   - `revise()` 中 AI 返回非法 PatchOp 的处理
   - AI 建议全部重新规划时的 fallback
   - 修改后重新渲染失败的回滚策略（恢复到修改前快照）

3. **TopologyDraft 加固**：
   - AI 输出格式错误的容错解析
   - 生成的拓扑不合理时的报告机制

4. **Orchestrator 超时**：
   - AI 调用添加可配置超时（默认 30s）
   - 超时时返回 `AgentStep.fail("AI 调用超时")`

5. **集成测试**：
   - 端到端 mock 测试：需求 → 设计 → 修改 → 撤销 → 再修改
   - 各种错误场景的恢复测试
   - 确保全量测试 (399+新增) 全部 PASS

**依赖**：Task 1, Task 2, Task 3, Task 4

**验收标准**：
- [ ] `python -m pytest -q` 全部 PASS（含 Phase 5 新增测试）
- [ ] `python -m ruff check schemaforge/` 无 violation
- [ ] 每个新增模块测试覆盖 ≥ 80%
- [ ] AI 调用超时有明确的错误消息
- [ ] PatchOp 非法操作不导致崩溃
- [ ] revise() 失败时能回滚到上一版本
- [ ] TopologyDraft 解析失败时有友好报告
- [ ] 集成测试覆盖完整的 设计→修改→撤销 流程

---

## Task 8: 文档更新 + 全量回归测试

**目标**：更新项目文档，执行全量回归测试，确保一切正常。

**涉及文件**：
- `README.md` — 更新项目状态、测试数量、Phase 5/6 说明
- `devlog.md` — 补录 Phase 5/6 开发日志
- `plan.md` — 更新阶段状态
- `docs/roadmap_next_phases.md` — 更新进度对照
- `ai_interaction.md` — 补录关键 AI 交互

**具体工作**：

1. **README.md 更新**：
   - 更新 "当前状态" 测试数量
   - Phase 5/6 从 "未实施" 移到 "已完成"
   - 更新项目结构（新增文件）
   - 更新 "下一步开发" 部分

2. **devlog.md 补录**：
   - 为每个 Task 写一条 Round 记录（Goal/Plan/Changes/Result）

3. **plan.md 更新**：
   - 新增 Phase 5/6 的阶段描述
   - 勾选所有完成项

4. **全量回归测试**：
   - `python -m pytest -v` — 全部 PASS
   - `python -m ruff check schemaforge/ gui.py tests/` — 无 violation
   - `python gui.py` — GUI 启动无异常（手动验证）
   - `python main.py --demo` — CLI demo 不受影响

**依赖**：Task 1-7（全部完成后执行）

**验收标准**：
- [ ] `python -m pytest -q` 全部 PASS
- [ ] `python -m ruff check schemaforge/ gui.py tests/` 无 violation
- [ ] `python gui.py` GUI 启动正常
- [ ] `python main.py --demo` CLI 演示正常
- [ ] README.md 项目状态已更新
- [ ] devlog.md 包含 Phase 5/6 所有 Round 记录
- [ ] plan.md 所有 Phase 已标记完成
- [ ] 本文件 (schemaforge-tasks.md) 所有 checkbox 已勾选

---

## 任务依赖关系

```
Task 1 (PatchEngine)
  └── Task 2 (DesignHistory) ──┐
       └── Task 3 (revise()) ──┼── Task 5 (GUI 设计页面)
                               │        └── Task 6 (GUI 多轮修改)
Task 4 (TopologyDraft) ────────┘                │
                                                ↓
                                    Task 7 (错误处理 + 测试)
                                                │
                                                ↓
                                    Task 8 (文档 + 回归测试)
```

**推荐执行顺序**：
1. **并行启动**：Task 1 + Task 4（互不依赖）
2. **顺序推进**：Task 2（依赖 Task 1）→ Task 3（依赖 Task 1 + 2）
3. **GUI 集成**：Task 5（依赖 Task 2 + 3）→ Task 6（依赖 Task 5 + 3）
4. **收尾**：Task 7（依赖 Task 1-4）→ Task 8（依赖全部）

---

## Agent 执行指南

每个 Task 的执行应包含：
1. 明确指出要修改/新建的文件路径
2. 要求先 Read 现有代码理解模式，再动手
3. 遵循现有代码风格（Pydantic 模型、dataclass、type hints、中文注释）
4. 完成后列出验收检查清单的完成情况
5. 确保编译和测试通过：`python -m pytest -q` + `python -m ruff check schemaforge/`
