# SchemaForge AI-Only 改造 — 任务拆分

> 基于 `schemaforge-implementation.md`，共 8 个任务。
> 每个任务独立可交付，有明确的验收标准。

---

## Task 1: 删除 GUI 中的 Mock/链路/Demo 选择，统一到 SchemaForgeSession

**目标**：GUI 只有一条路径 — SchemaForgeSession（始终用真实 AI）。删除模式选择、链路选择、Demo 按钮。

**涉及文件**：
- `schemaforge/gui/pages/design_page.py` — 删除 `_mode_combo`, `_chain_combo`, Demo 按钮; `_on_generate` 始终走 SchemaForgeWorker
- `schemaforge/gui/workers/engine_worker.py` — 删除 `ClassicEngineWorker`, `RetryDesignWorker` 的 `use_mock` 参数; SchemaForgeWorker 删除 `use_mock`
- `tests/test_gui_wiring.py` — 更新结构测试以匹配新 UI

**具体工作**：
1. `design_page.py`: 删除 `_mode_combo` (离线Mock/在线LLM)、`_chain_combo` (4 条链路)、Demo 按钮 (`_btn_demo`, `_on_demo`)
2. `_on_generate`: 不再判断 `chain_index`，始终创建 `SchemaForgeWorker(user_input=user_input)` 并连接 `_on_sf_worker_finished`
3. `engine_worker.py`: 所有 Worker 删除 `use_mock` 参数。`SchemaForgeWorker.run()` 不再传 `use_mock` 给 `SchemaForgeSession`
4. 保留 `SchemaForgeOrchestratedWorker` 和 `SchemaForgeReviseWorker`（AI 编排和多轮修改仍需要）
5. 保留 `_template_combo`（快捷模板，只填充文本，不影响后端路径）
6. 更新 `test_gui_wiring.py` 中受影响的测试

**验收标准**：
- [x] `design_page.py` 中无 `_mode_combo`、`_chain_combo`、`_btn_demo` 相关代码
- [x] `_on_generate` 始终走 `SchemaForgeWorker`，无 `chain_index` 分支判断
- [x] `engine_worker.py` 所有 Worker 无 `use_mock` 参数
- [x] `pytest tests/test_gui_wiring.py -q` 全绿
- [x] `ruff check schemaforge/gui/` 全绿

---

## Task 2: 删除后端 Mock 代码，SchemaForgeSession 始终用真实 AI

**目标**：移除所有 `use_mock` 参数和 Mock 分支，让后端始终调用真实 LLM。

**涉及文件**：
- `schemaforge/ai/client.py` — 删除 `DEMO_RESPONSES`, `call_llm_mock()`
- `schemaforge/design/planner.py` — 删除 `use_mock` 参数和 `_plan_mock()` 方法
- `schemaforge/design/clarifier.py` — 删除 `use_mock` 参数和 Mock 分支
- `schemaforge/design/topology_draft.py` — 删除 Mock 分支
- `schemaforge/agent/orchestrator.py` — 删除 `use_mock` 参数和 `_mock_turn()`
- `schemaforge/workflows/schemaforge_session.py` — 删除 `use_mock` 参数
- `schemaforge/workflows/design_session.py` — 删除 `use_mock` 参数
- `schemaforge/core/engine.py` — 删除 `use_mock` 参数

**具体工作**：
1. `ai/client.py`: 删除 `DEMO_RESPONSES` dict 和 `call_llm_mock()` 函数
2. `planner.py`: 删除 `use_mock` 构造参数、`_plan_mock()` 方法；`plan()` 直接调用 `_plan_ai()`
3. `clarifier.py`: 删除 `use_mock`；Mock 分支代码删除
4. `topology_draft.py`: 删除 Mock 分支
5. `orchestrator.py`: 删除 `use_mock` 参数和 `_mock_turn()` 方法
6. `schemaforge_session.py`: 删除 `use_mock` 参数及所有 `self.use_mock` 引用
7. `design_session.py`: 删除 `use_mock` 参数
8. `core/engine.py`: 删除 `use_mock` 参数

**依赖**：Task 1

**验收标准**：
- [x] 全项目无 `use_mock` 参数（`grep -r "use_mock" schemaforge/` 无结果）
- [x] `ai/client.py` 无 `DEMO_RESPONSES` 和 `call_llm_mock`
- [x] `ruff check schemaforge/` 全绿
- [x] 编译无错（`python -c "import schemaforge"` 成功）

---

## Task 3: CLI 改造 — 统一到 SchemaForgeSession，删除旧入口

**目标**：CLI 默认走 SchemaForgeSession，删除 `--demo`、`--new-chain`、`--online`，保留 `--orchestrated`。

**涉及文件**：
- `main.py` — 重写 CLI 入口

**具体工作**：
1. 删除 `--demo`, `--new-chain`, `--online`, `--unified` 参数
2. 保留 `--orchestrated`, `-i/--input`, `--store`
3. 默认行为：`SchemaForgeSession` 交互模式（支持多轮修改）
4. `-i "需求"`: 单次模式
5. `--orchestrated`: AI 编排交互模式
6. 删除 `run_demo()`, `process_and_display()` (经典链显示)
7. 保留并精简 `process_and_display_unified()`, `run_interactive_unified()`
8. 删除 `from schemaforge.core.engine import SchemaForgeEngine` 等旧引擎导入

**依赖**：Task 2

**验收标准**：
- [x] `python main.py --help` 无 `--demo`, `--new-chain`, `--online`, `--unified`
- [x] `python main.py --help` 有 `--orchestrated`, `-i`, `--store`
- [x] `ruff check main.py` 全绿

---

## Task 4: AI 驱动需求解析 — 替换正则匹配

**目标**：用真实 LLM 解析用户需求为结构化 `UserDesignRequest`，正则作为 fallback。修改请求解析同理。

**涉及文件**：
- `schemaforge/design/synthesis.py` — `parse_design_request()` 和 `parse_revision_request_v2()` 增加 AI 解析路径
- `schemaforge/ai/prompts.py` — 新增需求解析和修改解析的 prompt

**具体工作**：
1. 在 `ai/prompts.py` 新增 `PARSE_REQUEST_PROMPT` — 指导 AI 从自然语言提取 part_number, category, v_in, v_out, i_out, wants_led 等字段
2. 在 `ai/prompts.py` 新增 `PARSE_REVISION_PROMPT` — 指导 AI 从修改请求提取 param_updates, request_updates, replace_device, structural_ops
3. `parse_design_request()`: 先调 `call_llm_json(PARSE_REQUEST_PROMPT, user_input)`，成功则构造 `UserDesignRequest`；失败则 fallback 到现有正则逻辑
4. `parse_revision_request_v2()`: 同理
5. 异常保护：AI 调用失败时静默 fallback 到正则，不崩溃

**依赖**：Task 2

**验收标准**：
- [x] `parse_design_request("用 TPS54202 搭一个 20V转5V 的 DCDC 电路")` 通过 AI 解析出 `part_number="TPS54202", category="buck", v_in="20", v_out="5"`
- [x] `parse_revision_request_v2("把输出电压改成 3.3V")` 通过 AI 解析出 `param_updates={"v_out": "3.3"}`
- [x] AI 调用失败时不崩溃，fallback 到正则
- [x] `ruff check schemaforge/design/synthesis.py schemaforge/ai/prompts.py` 全绿

---

## Task 5: 统一工作台集成 Clarifier + Review

**目标**：`SchemaForgeSession._build_from_device()` 集成需求澄清（Clarifier）和工程审查（Review），提升设计质量。

**涉及文件**：
- `schemaforge/workflows/schemaforge_session.py` — `_build_from_device()` 增加澄清和审查步骤
- `schemaforge/design/synthesis.py` — `DesignBundle` 增加 `review_report` 字段（如果没有）

**具体工作**：
1. 在 `_build_from_device()` 中，在 synthesizer.build_bundle() 之后：
   - 调用 `Clarifier` 检查缺失约束，将补全的约束追加到 bundle 的 warnings
   - 调用 `ReviewEngine.review()` 对生成的设计进行 42 条规则审查
   - 将审查结果附加到 bundle（new field `review_report`）
2. 如果审查有 blocking issue，在 `SchemaForgeTurnResult.warnings` 中报告
3. GUI `_display_bundle()` 中展示审查结果到 ERC 标签页

**依赖**：Task 2

**验收标准**：
- [x] `SchemaForgeSession.start()` 返回的 bundle 包含审查报告
- [x] blocking 审查 issue 在 GUI ERC 标签页中显示
- [x] `ruff check schemaforge/workflows/schemaforge_session.py` 全绿

---

## Task 6: GUI 导入闭环 — 接入 ingest_asset/confirm_import

**目标**：GUI 器件补录流程走 `SchemaForgeSession.ingest_asset()` → 展示预览 → `confirm_import()`，而非直接 LibraryService 入库。

**涉及文件**：
- `schemaforge/gui/pages/design_page.py` — `_on_resolve_device()` 改为调用 session
- `schemaforge/gui/workers/engine_worker.py` — 新增 `IngestAssetWorker` 和 `ConfirmImportWorker`

**具体工作**：
1. 新增 `IngestAssetWorker(QThread)`: 调用 `session.ingest_asset(filepath)` 返回 `SchemaForgeTurnResult`
2. 新增 `ConfirmImportWorker(QThread)`: 调用 `session.confirm_import(answers)` 返回 `SchemaForgeTurnResult`
3. `_on_resolve_device()`: 不再直接打开 `PdfImportDialog`，而是打开文件选择器获取 PDF 路径，然后启动 `IngestAssetWorker`
4. `IngestAssetWorker` 完成后：
   - `needs_confirmation` → 展示 ImportPreview（引脚列表 + 待确认问题）
   - `error` → 显示错误
5. 用户确认后启动 `ConfirmImportWorker`
6. `ConfirmImportWorker` 完成后：
   - `generated` → 器件入库成功，自动续设计（_on_sf_worker_finished）
   - `needs_confirmation` → 校验未通过，展示补充问题
   - `error` → 显示错误

**依赖**：Task 1, Task 2

**验收标准**：
- [x] GUI 补录器件时调用 `session.ingest_asset()` 而非 `PdfImportDialog`
- [x] 导入预览（引脚列表）正确显示
- [x] `confirm_import()` 成功后自动续设计
- [x] `ruff check schemaforge/gui/` 全绿

---

## Task 7: 修复测试 — 全量测试适配 AI-Only 架构

**目标**：所有测试适配删除 Mock 后的架构。需要 Mock 的测试用 `unittest.mock.patch` 拦截 AI 调用。

**涉及文件**：
- `tests/` — 所有涉及 `use_mock` 的测试文件

**具体工作**：
1. 扫描所有测试文件中的 `use_mock` 引用
2. 将 `use_mock=True` 替换为 `unittest.mock.patch("schemaforge.ai.client.call_llm")` 或 `patch("schemaforge.ai.client.call_llm_json")`
3. Mock 的返回值使用原来 Mock 模式输出的等效数据
4. 确保 `conftest.py` 中有通用的 AI mock fixture
5. 目标：`pytest -q` 全绿，零失败

**依赖**：Task 1, Task 2, Task 3, Task 4, Task 5, Task 6

**验收标准**：
- [x] `python -m pytest -q` 全绿（0 failed）
- [x] `python -m ruff check schemaforge gui.py tests main.py` 全绿
- [x] 测试中无 `use_mock` 硬编码（全部通过 mock.patch 拦截）

---

## Task 8: 端到端验证 — GUI 截图 + CLI 测试

**目标**：启动 GUI 截图验证全流程，CLI 端到端测试。

**具体工作**：

### 8.1 GUI 验证
1. 启动 GUI (`python gui.py`)
2. 截图验证：主界面布局（无模式选择、无链路选择、无 Demo 按钮）
3. 输入 "5V转3.3V稳压电路" → 点击生成 → 截图验证 SVG 输出
4. 输入 "用 TPS54202 搭 20V转5V DCDC" → 验证 needs_asset 流程 → 截图
5. 在 Chat 面板输入修改指令 → 验证多轮修改 → 截图

### 8.2 CLI 验证
1. `python main.py -i "5V转3.3V稳压电路"` → 验证输出
2. `python main.py` → 交互模式 → 输入需求 → 验证多轮
3. `python main.py --orchestrated -i "设计一个 LED 指示灯"` → 验证 AI 编排

### 8.3 问题记录
测试中发现的问题记录在本节表格中。

| # | 测试步骤 | 问题描述 | 严重程度 | 关联 Task | 状态 |
|---|---------|---------|---------|----------|------|

**依赖**：Task 7

**验收标准**：
- [x] GUI 主界面截图：无模式选择、无链路选择
- [x] GUI 生成流程截图：输入需求 → SVG 输出
- [x] GUI 缺失器件截图：needs_asset → 导入提示
- [x] CLI 单次模式正常运行
- [x] CLI 交互模式正常运行
- [x] 所有 P0/P1 问题已修复

---

## 任务依赖关系

```
Task 1 (GUI 删除 Mock/链路)
  └── Task 2 (后端删除 Mock) ──┬── Task 3 (CLI 改造)
                                ├── Task 4 (AI 需求解析)
                                ├── Task 5 (集成 Clarifier/Review)
                                └── Task 6 (GUI 导入闭环)
                                         │
                                    Task 7 (修复测试)
                                         │
                                    Task 8 (端到端验证)
```

**推荐执行顺序**：Task 1 → Task 2 → Task 3 + Task 4 + Task 5 + Task 6 (并行) → Task 7 → Task 8
