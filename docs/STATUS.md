# SchemaForge 项目状态报告

> 最后更新: 2026-03-07 | 分支: main | 提交: e40b9a5  
> 测试: **1191 passed** | Ruff: **全绿** | 源码: 79 文件 / 27,667 行 | 测试: 43 文件

---

## 一、最终目标

构建一个**真正的 AI 驱动 EDA 原理图设计助手**，核心能力:

1. 用户说自然语言（"用 TPS54202 搭一个 20V转5V 的 DCDC 电路"）
2. 系统精确识别器件型号（不替换、不近似）
3. 自动检测缺失器件 → 引导用户上传 PDF/datasheet → AI 提取引脚与参数 → 入库
4. 根据 datasheet 公式计算外围元件参数（不猜测）
5. 生成完整原理图（SVG + BOM + SPICE），支持任意电路类型
6. 支持多轮对话修改（"把输出电压改成 5V"、"换成 TPS5430"）
7. AI 编排模式: AI 决策 + 本地工具执行，Orchestrator 控制循环
8. GUI / CLI / Agent 三入口共享同一后端

**核心哲学: AI 只做决策和提问，本地工具负责执行。**

---

## 二、已完成的工作

### 2.1 基础设施层

| 模块 | 文件 | 状态 | 说明 |
|------|------|------|------|
| 事件系统 | `common/events.py` | ✅ 完成 | EventType + 多种事件类型 |
| 错误模型 | `common/errors.py` | ✅ 完成 | 统一错误层次 |
| 进度追踪 | `common/progress.py` | ✅ 完成 | ProgressTracker |
| 状态机 | `workflows/state_machine.py` | ✅ 完成 | 通用状态机框架 |
| AI 客户端 | `ai/client.py` | ✅ 完成 | call_llm / call_llm_json，硬编码 kimi-k2.5 |
| Agent 协议 | `agent/protocol.py` | ✅ 完成 | AgentStep / AgentAction / PatchOp / ToolCallRequest |
| 工具注册表 | `agent/tool_registry.py` | ✅ 完成 | ToolRegistry + merge() 合并 |

### 2.2 器件库系统

| 模块 | 文件 | 状态 | 说明 |
|------|------|------|------|
| 器件模型 | `library/models.py` | ✅ 完成 | DeviceModel + 8 个设计知识字段 |
| 存储层 | `library/store.py` | ✅ 完成 | JSON + SQLite 双写 |
| 校验器 | `library/validator.py` | ✅ 完成 | DeviceDraft 校验 |
| 去重检测 | `library/dedupe.py` | ✅ 完成 | 重复检测 |
| 服务层 | `library/service.py` | ✅ 完成 | CRUD 操作 |
| 符号构建 | `library/symbol_builder.py` | ✅ 完成 | 引脚→符号自动生成 |
| 参考设计 | `library/reference_models.py` | ✅ 完成 | 6 个参考设计 |

**器件库内容（6 个器件）:**

| 型号 | 类型 | 拓扑 |
|------|------|------|
| AMS1117-3.3 | LDO | ✅ 有 |
| TPS5430 | Buck | ✅ 有 |
| LED_INDICATOR | LED | ✅ 有 |
| VOLTAGE_DIVIDER | 分压器 | ✅ 有 |
| RC_LOWPASS | RC 滤波器 | ✅ 有 |
| W25Q32JV | Flash 存储 | ❌ 无拓扑 |

### 2.3 数据导入系统

| 模块 | 文件 | 状态 | 说明 |
|------|------|------|------|
| EasyEDA API | `ingest/easyeda_provider.py` | ✅ 完成 | 在线搜索器件 |
| AI 分析器 | `ingest/ai_analyzer.py` | ✅ 完成 | PDF/图片 → 文本/视觉分析 |
| Datasheet 提取 | `ingest/datasheet_extractor.py` | ✅ 完成 | 提取引脚、参数、应用电路 |
| 应用电路提取 | `ingest/datasheet_extractor.py` | ✅ 完成 | 从 datasheet 提取 recipe 并附加到器件 |

### 2.4 设计管线（新主链 `design/*`）

| 模块 | 文件 | 行数 | 状态 | 说明 |
|------|------|------|------|------|
| 规划器 | `planner.py` | 424 | ✅ 完成 | NL → ModuleRequirement（Mock + AI 双模式） |
| 澄清器 | `clarifier.py` | 631 | ✅ 完成 | 缺失约束检测 + AI 增强 |
| 检索器 | `retrieval.py` | — | ✅ 完成 | 评分排序 + 角色匹配 |
| 候选求解器 | `candidate_solver.py` | 1264 | ✅ 完成 | 6 维评分，8 种电路类别 |
| 审查引擎 | `review.py` | 1632 | ✅ 完成 | 42 条工程审查规则 |
| Design IR | `ir.py` | 532 | ✅ 完成 | 跨阶段唯一真值层 |
| Patch 引擎 | `patch_engine.py` | — | ✅ 完成 | 6 种修改操作 |
| 拓扑草稿 | `topology_draft.py` | 648 | ✅ 完成 | Mock + LLM 生成 |
| 合成层 | `synthesis.py` | 1665 | ✅ 完成 | 解析 → 匹配 → 计算 → 渲染 → 打包 |
| 公式引擎 | `formula_eval.py` | — | ✅ 完成 | FormulaEvaluator（datasheet 公式驱动） |

**电路类别支持（12 种）:** buck, ldo, boost, flyback, sepic, charge_pump, opamp, mcu, sensor, connector, mosfet, diode

**候选求解器评分覆盖:** buck, ldo, boost, flyback, sepic, opamp（6 种完整评分）

**审查规则覆盖:** buck(10), ldo(7), boost(5), flyback(4), sepic(3), opamp(4), rc_filter(4), generic(5) = **42 条**

### 2.5 渲染系统

| 模块 | 文件 | 状态 | 说明 |
|------|------|------|------|
| 拓扑渲染器 | `schematic/renderer.py` | ✅ 完成 | TopologyRenderer + 通用 fallback |
| 拓扑布局 | `schematic/topology.py` | ✅ 完成 | 5 种硬编码布局 + layout_generic() |
| 旧渲染器 | `render/` | ✅ 遗留 | 模板驱动渲染（旧链用） |

### 2.6 工作流编排

| 模块 | 文件 | 行数 | 状态 | 说明 |
|------|------|------|------|------|
| 设计会话 | `design_session.py` | 643 | ✅ 完成 | 端到端编排（旧主链入口） |
| 统一工作台 | `schemaforge_session.py` | 600 | ✅ 完成 | start/revise/ingest_asset/confirm_import/run_orchestrated |
| IR Patch | `ir_patch.py` | 604 | ✅ 完成 | IR 级 6 种修改操作 |

### 2.7 Agent / Orchestrator

| 模块 | 文件 | 状态 | 说明 |
|------|------|------|------|
| 全局工具 | `agent/tools.py` | ✅ 完成 | 9 个全局工具（default_registry） |
| 会话工具 | `agent/design_tools.py` | ✅ 完成 | 9 个会话绑定工具 |
| Orchestrator | `agent/orchestrator.py` | ✅ 完成 | AI 多轮循环 + 工具调用 + 自动执行 |
| 系统提示词 | `ai/prompts.py` | ✅ 完成 | build_design_workbench_prompt() |
| 工具合并 | `agent/tool_registry.py` | ✅ 完成 | ToolRegistry.merge() |
| Session 接入 | `schemaforge_session.py` | ✅ 完成 | get_orchestrator() / run_orchestrated() |

### 2.8 GUI (PySide6)

| 模块 | 文件 | 行数 | 状态 | 说明 |
|------|------|------|------|------|
| 设计页面 | `pages/design_page.py` | 1022 | ✅ 完成 | 三条链路 + 缺失面板 + chat |
| 器件库页 | `pages/library_page.py` | 1735 | ✅ 完成 | 搜索/导入/编辑 |
| Chat 面板 | `widgets/chat_panel.py` | 267 | ✅ 完成 | 双向消息 + message_sent 信号 |
| SVG 查看器 | `widgets/svg_viewer.py` | — | ✅ 完成 | 缩放/适应 |
| 进度条 | `widgets/progress_header.py` | — | ✅ 完成 | 进度横幅 |
| Workers | `workers/engine_worker.py` | 277 | ✅ 完成 | 6 个 QThread Worker |

**GUI 已实现的链路:**

| # | 链路名 | Worker 类 | 完成回调 |
|---|--------|-----------|----------|
| 0 | 经典链（模板驱动） | ClassicEngineWorker | _on_worker_finished |
| 1 | 新主链（库驱动+IR+审查） | DesignSessionWorker | _on_worker_finished |
| 2 | 统一工作台（推荐）| SchemaForgeWorker | _on_sf_worker_finished |
| — | 多轮修改 | SchemaForgeReviseWorker | _on_sf_revise_finished |
| — | AI 编排 | SchemaForgeOrchestratedWorker | _on_orchestrated_finished |

**Chat 驱动路径:** `_on_chat_send` → 有设计 → `_start_revise` → SchemaForgeReviseWorker

### 2.9 CLI (`main.py`)

| 功能 | 状态 | 说明 |
|------|------|------|
| 经典链 | ✅ 可用 | 默认模式 |
| 新主链 | ✅ 可用 | `--new-chain` 参数 |
| Demo 模式 | ✅ 可用 | `--demo` |
| 交互模式 | ✅ 可用 | 默认 |
| 统一工作台 | ❌ 不可用 | 缺 `--unified` 参数 |
| AI 编排 | ❌ 不可用 | 缺 `--orchestrated` 参数 |

---

## 三、距离最终目标的差距

### 🔴 阻塞级问题（必须修复）

#### B1: RetryDesignWorker 走错链路

**位置:** `design_page.py` L954-981  
**问题:** 用户在统一工作台模式下发现器件缺失 → 补录完成 → `_on_retry_design()` 创建 `RetryDesignWorker`（继承自 `DesignSessionWorker`，走旧链）。用户被静默地从统一链路踢到旧链路，丢失多轮修改能力。  
**根因:** `_on_retry_design` 硬编码使用 `RetryDesignWorker`，没有判断当前链路。  
**修复方案:** 当 `_sf_session` 存在时，创建 `SchemaForgeWorker(session=self._sf_session)` 并连接 `_on_sf_worker_finished`；否则回退到 `RetryDesignWorker`。  
**工作量:** ~30 分钟

#### B2: Orchestrator 在 GUI 中没有入口

**位置:** `design_page.py` L166-171 (chain_combo) + L396-425 (_on_generate)  
**问题:** `SchemaForgeOrchestratedWorker` 已导入、已实现 handler（`_on_orchestrated_finished`），但 chain_combo 只有 3 个选项，`_on_generate` 中没有 `chain_index == 3` 的分支。用户无法触发 AI 编排模式。  
**根因:** 添加 Worker 和 handler 时遗漏了 GUI 入口。  
**修复方案:** chain_combo 增加 "AI 编排（高级）"；`_on_generate` 增加 `chain_index == 3` 分支，创建 session 后启动 `SchemaForgeOrchestratedWorker`。  
**工作量:** ~1 小时

#### B3: CLI 缺少统一工作台和 AI 编排入口

**位置:** `main.py`  
**问题:** CLI 只支持 `--new-chain`（DesignSession）和默认经典链。`SchemaForgeSession` 和 Orchestrator 在 CLI 中不可达。违反 Rule 6（所有入口共用同一后端）。  
**修复方案:** 增加 `--unified` 和 `--orchestrated` 两个 flag。`--unified` 走 `SchemaForgeSession.start()` + 交互式 `revise()` 循环。`--orchestrated` 走 `run_orchestrated()` + 交互式多轮。  
**工作量:** ~1 小时

#### B4: 器件库太小

**位置:** `schemaforge/store/devices/`  
**问题:** 只有 6 个器件。项目的标准 demo 示例 "用 TPS54202 搭一个 20V转5V 的 DCDC 电路" 立即命中 `needs_asset`。核心卖点（精确型号匹配 + 公式驱动）在开箱体验中完全无法展现。  
**修复方案:** 至少添加:
- **TPS54202** (Buck, 4.5V-28V, 2A) — 标准 demo 器件
- **TPS61023** (Boost, 0.5V-5.5V→5V, 500mA) — 覆盖 boost 拓扑
- **OPA2277** (OpAmp) — 覆盖 opamp 类别  

**工作量:** ~30 分钟（按现有 TPS5430.json 格式编写）

---

### 🟡 重要改进项（显著提升体验）

#### I1: SchemaForge 路径跳过了澄清/候选/审查

**位置:** `schemaforge_session.py` → `_build_from_device()`  
**问题:** 统一工作台的 `start()` 流程是: parse → resolve → synthesize → bundle。它跳过了 DesignSession 中的 Clarifier（需求澄清）、CandidateSolver（多候选评分）、Review（工程审查）三个阶段。这意味着统一工作台输出的设计没有经过需求补全和工程审查。  
**影响:** 设计质量不如旧主链（缺少审查报告），用户拿到的设计可能有工程问题但不被告知。  
**修复方案:** 在 `_build_from_device()` 中集成 Clarifier（填补缺失约束）、CandidateSolver（评估方案质量）、Review（生成审查报告并附加到 bundle）。  
**工作量:** 高（~3 小时），需要协调多个模块的输入/输出格式

#### I2: planner._plan_ai() 缺少异常保护

**位置:** `planner.py` L176-190  
**问题:** `_plan_ai()` 调用 `call_llm_json()` 时，对 `result is None` 有处理，但 `call_llm_json()` 抛出异常（网络错误、API 超时等）时会直接崩溃，没有 try/except。  
**影响:** 在线模式下网络波动会导致规划阶段崩溃，用户看到 traceback。  
**修复方案:** 包裹 try/except，异常时返回空 DesignPlan 并附加错误说明。  
**工作量:** 低（~10 分钟）

#### I3: GUI 导入流程未接入 ingest_asset/confirm_import

**位置:** `design_page.py` → `_on_resolve_device()`  
**问题:** 器件补录使用 `PdfImportDialog` + `LibraryService` 直接入库。`SchemaForgeSession.ingest_asset()` 和 `confirm_import()` 从未被 GUI 调用。这意味着 GUI 导入路径不经过 SchemaForgeSession 的校验→预览→确认三步流程，也不会自动附加 datasheet 提取的应用电路 recipe。  
**影响:** 通过 GUI 补录的器件可能缺少应用电路信息，导致后续合成使用默认配方而非 datasheet 推荐的参数。  
**修复方案:** 补录完成后调用 `self._sf_session.ingest_asset(filepath)` → 展示预览 → `confirm_import(answers)`。  
**工作量:** 中（~1.5 小时）

#### I4: Orchestrator 没有 Mock 模式

**位置:** `orchestrator.py`  
**问题:** Orchestrator 始终调用真实 AI (`call_llm`)。在 `use_mock=True` 模式下无法测试 AI 编排路径，离线环境完全不可用。  
**影响:** 无法在不联网时演示 AI 编排功能；无法为 Orchestrator 写自动化测试。  
**修复方案:** 增加 `use_mock` 参数；Mock 模式返回预设的 `AgentStep`（如直接调用 `parse_and_synthesize` 工具后 finalize）。  
**工作量:** 低（~30 分钟）

#### I5: parse_design_request / parse_revision_request_v2 是纯正则

**位置:** `synthesis.py`  
**问题:** 需求解析完全靠正则和关键字匹配，无法处理复杂/模糊的自然语言（如"帮我设计一个给单片机供电的电源"、"功率要大一些"）。  
**影响:** 复杂需求被误解或无法识别，用户需要用非常精确的措辞。  
**修复方案:** 在线模式下用 AI 辅助解析，正则作为 fallback。  
**工作量:** 高（~3 小时）

#### I6: 单器件架构，不支持多模块分解

**位置:** `schemaforge_session.py`  
**问题:** 统一工作台只能处理单个核心器件。"设计一个 LDO + LED 指示灯电路" 这种多模块需求只能通过 `wants_led` flag 间接处理，不能真正分解为独立模块各自检索+合成。  
**影响:** 无法处理真正的多器件系统设计（如 DCDC + LDO + MCU + LED）。  
**修复方案:** 引入 Planner 将需求分解为多个 ModuleRequirement，每个模块独立走 resolve → synthesize 流程，最后合并 bundle。  
**工作量:** 很高（~5+ 小时），架构变更

---

### 🟢 可选优化项

| # | 项目 | 说明 | 工作量 |
|---|------|------|--------|
| P1 | SPICE 模型精确化 | 当前 SPICE 输出使用 `XU{ref}` 占位，缺少真实 `.model/.subckt` | 中 |
| P2 | 通用布局渲染改进 | `layout_generic()` 只做基础布局，美观度不足 | 中 |
| P3 | 真实 AI 集成测试 | 所有测试都跑 mock，缺少 `use_mock=False` 的集成测试 | 低 |
| P4 | 清理 candidate_solver 死代码 | `_use_mock` flag 从未使用 | 低 |
| P5 | 链路选择锁定 | 会话开始后应禁用/灰化 mode_combo 和 chain_combo | 低 |

---

## 四、Mock vs 真实 AI 状态矩阵

| 组件 | Mock | 真实 AI | 异常保护 | 测试覆盖 |
|------|------|---------|----------|----------|
| planner | ✅ 完整 | ✅ 实现 | ⚠ 部分（None→fallback, 异常→崩溃） | Mock ✅ / AI ❌ |
| clarifier | ✅ 完整 | ✅ 实现（增强型） | ✅ 异常→静默退化 | Mock ✅ / AI 最小 |
| topology_draft | ✅ 完整 | ✅ 实现 | ✅ 4 层 fallback | Mock ✅ / AI ✅ |
| candidate_solver | ✅ 全规则 | N/A（纯规则） | N/A | ✅ 103 tests |
| review | ✅ 全规则 | N/A（纯规则） | N/A | ✅ 94 tests |
| orchestrator | ❌ 无 Mock | ✅ 始终调真实 AI | ✅ AI 失败→FAIL step | ❌ 无自动化测试 |
| ai_analyzer | ✅ 完整 | ✅ 实现 | ⚠ 异常→返回 error，非 mock fallback | Mock ✅ / AI ❌ |
| synthesis | ✅ 纯规则 | N/A | N/A | ✅ 充分 |

---

## 五、架构概览

```
用户输入 (自然语言)
    │
    ├─[CLI]── main.py ──────────────┐
    ├─[GUI]── design_page.py ───────┤
    └─[Agent]── orchestrator.py ────┤
                                    │
                    ┌───────────────┴───────────────┐
                    │                               │
              SchemaForgeSession              DesignSession
              (统一工作台 - 推荐)             (旧主链 - 兼容)
                    │                               │
                    │                    planner → clarifier →
                    │                    retrieval → candidate_solver →
                    │                    review → render
                    │
            parse_design_request
                    │
              ExactPartResolver ─── ComponentStore ─── devices/*.json
                    │
              (找到器件?)
               ╱        ╲
            是             否
             │              │
    _build_from_device   needs_asset
             │              │
    DesignRecipeSynthesizer  (用户上传 PDF)
             │              │
    FormulaEvaluator    ingest_asset()
             │              │
    TopologyRenderer    confirm_import()
             │              │
    DesignBundle        → 回到 _build_from_device
    (SVG+BOM+SPICE)
             │
        revise() ← 多轮修改 ← parse_revision_request_v2
```

---

## 六、文件结构索引

```
schemaforge/                        # 27,667 行源码
├── ai/                             # AI 客户端层
│   ├── client.py                   # LLM API 封装 (kimi-k2.5 硬编码)  ⚠ 勿改 L14-17
│   ├── prompts.py                  # Prompt 模板 + design workbench prompt
│   └── validator.py                # AI 输出校验
│
├── agent/                          # AI 编排层
│   ├── protocol.py                 # AgentStep/AgentAction (值为小写字符串)
│   ├── tool_registry.py            # ToolRegistry + merge()
│   ├── tools.py                    # 9 个全局工具 (default_registry)
│   ├── design_tools.py             # 9 个会话绑定工具
│   ├── orchestrator.py             # AI 多轮循环控制器
│   └── symbol_pipeline.py          # 符号导入管线
│
├── common/                         # 共享基础设施
│   ├── errors.py, events.py, progress.py, session_store.py
│
├── core/                           # 旧主链（仅维护，不新增功能）
│   ├── engine.py                   # SchemaForgeEngine
│   ├── models.py, templates.py, calculator.py, erc.py, exporter.py
│
├── design/                         # 新主链设计治理层
│   ├── synthesis.py                # 1665 行: 解析→匹配→计算→渲染→打包
│   ├── review.py                   # 1632 行: 42 条审查规则
│   ├── candidate_solver.py         # 1264 行: 6 维候选评分
│   ├── clarifier.py                # 631 行: 需求澄清 + AI 增强
│   ├── topology_draft.py           # 648 行: 拓扑草稿生成
│   ├── ir.py                       # 532 行: Design IR
│   ├── planner.py                  # 424 行: NL → ModuleRequirement
│   ├── patch_engine.py             # PatchOp 执行
│   ├── retrieval.py                # 器件检索
│   ├── rationality.py              # 合理性检查
│   ├── topology_adapter.py         # 拓扑适配
│   └── formula_eval.py             # 公式计算引擎
│
├── gui/                            # PySide6 GUI
│   ├── pages/design_page.py        # 1022 行: 核心设计页面
│   ├── pages/library_page.py       # 1735 行: 器件库管理页
│   ├── widgets/chat_panel.py       # 267 行: AI 对话面板
│   ├── widgets/svg_viewer.py       # SVG 缩放查看器
│   ├── widgets/progress_header.py  # 进度横幅
│   ├── widgets/symbol_editor.py    # 符号编辑器
│   └── workers/engine_worker.py    # 277 行: 6 个 QThread Worker
│
├── ingest/                         # 数据导入
│   ├── ai_analyzer.py              # PDF/图片 AI 分析
│   ├── datasheet_extractor.py      # Datasheet 提取编排
│   └── easyeda_provider.py         # EasyEDA API
│
├── library/                        # 器件库
│   ├── models.py                   # DeviceModel (含 8 设计知识字段)
│   ├── store.py, service.py, validator.py, dedupe.py
│   ├── symbol_builder.py           # 引脚→符号
│   └── reference_models.py         # 参考设计模型
│
├── render/                         # 旧渲染器
├── schematic/                      # 新渲染器
│   ├── renderer.py                 # TopologyRenderer + 通用 fallback
│   └── topology.py                 # 5 种布局 + layout_generic()
│
├── store/
│   ├── devices/                    # 6 个器件 JSON
│   └── reference_designs/          # 6 个参考设计
│
└── workflows/                      # 工作流编排
    ├── schemaforge_session.py      # 600 行: 统一工作台入口
    ├── design_session.py           # 643 行: 旧主链入口
    ├── ir_patch.py                 # IR Patch 引擎
    └── state_machine.py            # 通用状态机

tests/                              # 43 个测试文件, 1191 个测试用例
main.py                             # CLI 入口 (338 行)
gui.py                              # GUI 入口
```

---

## 七、关键约束（开发必读）

1. **`schemaforge/ai/client.py` L14-17 禁止修改** — AI 配置硬编码 kimi-k2.5
2. **UI 文字全部使用中文**
3. **AI 只做决策，本地工具执行** — 不允许 AI 直接操作状态
4. **新功能必须走新主链** — 禁止向 `core/engine.py` 添加新能力
5. **Design IR 是唯一中间真值**
6. **所有入口共用同一后端** — CLI/GUI/Agent 用同一套规则
7. **质量门: `pytest -q` 全绿 + `ruff check` 全绿**
8. **AgentAction 枚举值是小写**: `"call_tools"`, `"ask_user"`, `"finalize"`, `"fail"` 等
9. **根因修复优先，禁止表层补丁**

---

## 八、推进优先级建议

```
第一优先级 (阻塞/必须):
  B1 RetryDesignWorker 走错链路      ~30min   ← 影响补录后的用户体验
  B2 Orchestrator GUI 入口            ~1hr     ← AI 编排能力完全不可触达
  B3 CLI --unified / --orchestrated   ~1hr     ← 违反 Rule 6
  B4 器件库扩充 (TPS54202+boost+opamp) ~30min  ← demo 跑不通

第二优先级 (显著改进):
  I2 planner 异常保护                 ~10min   ← 在线模式稳定性
  I4 Orchestrator Mock 模式           ~30min   ← 离线可测试
  I1 统一工作台集成澄清/候选/审查      ~3hr    ← 设计质量对齐
  I3 GUI 导入流程接入 session         ~1.5hr   ← 应用电路 recipe 不丢失

第三优先级 (完善):
  I5 AI 辅助需求解析                  ~3hr
  I6 多模块分解                       ~5+hr
  P1-P5 可选优化
```

---

## 九、测试分布

| 测试文件 | 测试数 | 覆盖模块 |
|----------|--------|----------|
| test_candidate_solver.py | 103 | 候选求解器 (6 维评分, 8 种类别) |
| test_design_review.py | 94 | 审查引擎 (42 条规则) |
| test_schemaforge_session.py | 60 | 统一工作台 (start/revise/ingest/confirm) |
| test_design_clarifier.py | 55 | 需求澄清器 |
| test_planner.py | 47 | 设计规划器 |
| test_formula_eval.py | 39 | 公式引擎 |
| test_gui_wiring.py | 35 | GUI 信号连接/Worker 调度 |
| test_ir_patch.py | 34 | IR Patch 引擎 |
| test_topology_draft.py | 30 | 拓扑草稿生成 |
| 其余 34 个文件 | 694 | engine, templates, models, store, validator, ... |
| **总计** | **1191** | |
