# SchemaForge 技术交接文档

> 最后更新: 2026-03-10  
> 分支: `schemaforge-mvp`

---

## 1. 项目概述

SchemaForge 是一个 **AI 驱动的电路原理图生成器**。用户输入自然语言需求（如"用 TPS54202 把 5V 降到 3.3V"），系统自动完成：

1. 器件匹配（查本地器件库）
2. 连接规则推导（电源链、GPIO 等）
3. 外围元件参数计算（电感值、电容值、分压电阻等）
4. 原理图 SVG 渲染
5. BOM 清单 + SPICE 网表导出

两种使用方式：
- **GUI**：`python gui.py` — PySide6 桌面应用，含原理图预览、器件库管理、多轮对话修订
- **CLI**：`python main.py` — 命令行交互

---

## 2. 核心架构

### 2.1 AI Agent 架构（v3）

```
用户输入 → Orchestrator → AI (kimi-k2.5) ←→ 13 个原子工具
                                              ↓
                                         SystemDesignSession
                                              ↓
                                   IR → 渲染 → SVG/PNG + BOM + SPICE
```

**Orchestrator** (`schemaforge/agent/orchestrator.py`):
- 封装 OpenAI function calling 协议
- 支持 `kimi-k2.5` 和 `qwen3-coder-plus` 模型
- API endpoint: `coding.dashscope.aliyuncs.com/v1`
- `max_tokens=98304`（kimi-k2.5 上限）

**13 个原子工具** (`schemaforge/agent/design_tools_v3.py`):

| # | 工具名 | 类别 | 功能 |
|---|--------|------|------|
| 1 | `resolve_modules` | design | 提交模块意图，查器件库 |
| 2 | `resolve_connections` | design | 提交连接意图，规则引擎解析 |
| 3 | `synthesize_parameters` | design | 公式引擎计算外围元件参数 |
| 4 | `render_schematic` | design | schemdraw 模式渲染 |
| 5 | `export_outputs` | design | 导出 BOM + SPICE |
| 6 | `search_device_library` | library | 搜索器件库 |
| 7 | `get_design_status` | design | 查设计 IR 状态 |
| 8 | `review_design` | design | 工程审查规则检查 |
| 9 | `revise_module_param` | design | 修改模块参数后重新综合 |
| 10 | `get_svg_template` | design | 获取坐标模板（内部用） |
| 11 | `render_schematic_ai` | design | 本地确定性 SVG 渲染 |
| 12 | `review_schematic_visual` | design | Vision AI 视觉审查 |
| 13 | `get_device_datasheet` | library | 获取已存储 PDF datasheet 文本 |

### 2.2 两种渲染模式

GUI 提供一个 **"AI SVG 绘图模式"** 复选框切换：

| | schemdraw 模式（默认） | AI SVG 模式 |
|---|---|---|
| 渲染器 | `system/rendering.py` (schemdraw) | `design_tools_v3.py` (_build_svg_template + _render_svg_from_template) |
| 工具 | `render_schematic` (tool 4) | `render_schematic_ai` (tool 11) |
| 特点 | 成熟稳定，支持所有拓扑 | 确定性坐标，Pin+Body 分离，无重叠 |
| 修订 | `session.revise()` → schemdraw 重渲染 | `session.revise()` → 本地 SVG 重渲染 |
| System Prompt | `AGENT_SYSTEM_PROMPT` | `AGENT_SYSTEM_PROMPT_AI_SVG` |

### 2.3 SystemDesignSession

`schemaforge/system/session.py` — 系统设计会话，核心状态机：

```
start(user_input)          → 全新设计（AI 解析 → 完整管线）
revise(user_input)         → 多轮修订（解析修订意图 → 增量更新 IR → 重渲染）
ingest_asset(filepath)     → 中途导入器件（PDF/图片 → 草稿）
confirm_import(answers)    → 确认入库（校验 → 生成符号 → 保存 → 重跑管线）
```

关键属性：
- `_ir: SystemDesignIR` — 设计中间表示，包含所有模块实例、连接、网络
- `_ai_svg_mode: bool` — 控制 `_regenerate_outputs()` 使用哪个渲染器
- `_store: ComponentStore` — 器件库存储

### 2.4 设计 IR（Intermediate Representation）

`schemaforge/system/models.py`:

```
SystemDesignIR
├── request: SystemDesignRequest      # 用户原始意图
├── module_instances: dict[str, ModuleInstance]  # 已解析的模块
│   └── ModuleInstance
│       ├── device: DeviceModel       # 匹配的器件
│       ├── resolved_category: str    # buck/ldo/mcu/led
│       ├── parameters: dict          # v_in, v_out 等
│       ├── external_components: []   # 外围元件（电感、电容、电阻）
│       └── status: ModuleStatus      # RESOLVED/SYNTHESIZED/NEEDS_ASSET
├── connections: list[ResolvedConnection]  # 引脚级连接
├── nets: dict[str, SystemNet]        # 网络（VIN, VOUT, SW, GND 等）
└── unresolved_items: list            # 未解析项
```

---

## 3. 本地确定性 SVG 渲染器（Pin + Body 架构）

这是项目的核心创新，解决了 AI 生成 SVG 时的 **"重叠 + 断开"** 问题。

### 3.1 设计原则

AI 只做设计决策（选型、参数、连接），**不写任何 SVG 代码**。SVG 由本地渲染器确定性生成。

### 3.2 Pin + Body 分离

每个器件符号有两部分：
- **Pins**: 端点坐标 `{"1": {x, y}, "2": {x, y}}`，导线只能连到 pin
- **Body**: 内部绘图元素，带 `body_bbox: {x1, y1, x2, y2}`，导线不能穿过 body

标准尺寸（单位：px）:

| 器件 | Body 尺寸 | Lead 长度 | Pin-to-Pin 距离 |
|------|-----------|-----------|-----------------|
| 电容（竖） | 10px gap | 35px × 2 | 80px |
| 电阻（竖） | 40px rect | 30px × 2 | 100px |
| 二极管（竖） | 35px triangle | 30px × 2 | 94px |
| 电感（横） | 120-140px arcs | 20px × 2 | 160px |
| IC | 矩形 + 引脚名 | 40px stubs | 按引脚分布 |

### 3.3 No-Rail 设计

- 没有全局 VIN/GND 横线
- 每个接地引脚：25px 短线 + 本地 GND 符号（三条递减横线）
- VIN/VOUT：power flag 标签（短横线 + 文字）
- EN 上拉：40px 短线 + VIN power flag

### 3.4 布局引擎

`_build_svg_template(ir)` 根据器件类别分发：
- `"buck"` → Buck 专用布局（IC + Cin + D1 + L1 + Cout + FB 分压 + BST 电容 + EN 上拉）
- `"ldo"` → LDO 专用布局（IC + Cin + Cout + EN 上拉）

坐标布局完全确定性，**零 AI 参与**。

### 3.5 导线路由

所有导线只连接 pin-to-pin，避让策略：
- **SW 路由**：SW pin → 水平左移到 D1 body 外 → 竖直上到 sw_rail_y → 水平右到 sw_node_x
- **BOOT 路由**：BOOT pin → 竖直上到 C3 pin1 y 高度 → 水平到 C3 pin1

### 3.6 Junction Dots

自动检测：当某坐标有 3 条或更多导线端点时，画 filled circle (r=3)。

---

## 4. 器件库系统

### 4.1 存储结构

```
schemaforge/store/
├── library.db              # SQLite 索引（快速搜索）
├── devices/                # 每个器件一个 JSON
│   ├── TPS54202.json
│   ├── AMS1117-3.3.json
│   ├── STM32F103C8T6.json
│   └── ...
├── datasheets/             # 入库时保存的 PDF datasheet
│   └── TPS54202.pdf
└── reference_designs/      # 预制参考设计模板
    ├── ref_buck_basic.json
    └── ref_ldo_basic.json
```

### 4.2 DeviceModel

`schemaforge/library/models.py` — 器件模型核心字段：

```python
class DeviceModel:
    part_number: str        # "TPS54202"
    category: str           # "buck" / "ldo" / "mcu" / "led"
    symbol: SymbolDef       # 符号引脚定义
    topology: TopologyDef   # 推荐电路拓扑
    design_recipe: DesignRecipe  # 设计公式 + 参考值
    specs: dict             # 电气参数
    datasheet_path: str     # PDF datasheet 相对路径
```

### 4.3 器件入库流程

两个入口：

**入口 A — 器件库管理页**：
```
PDF 上传 → AI 提取（文本+图片） → DeviceDraft → 用户确认 → build_symbol() → save_device()
                                                                    ↓
                                                            save_datasheet() → PDF 持久化
```

**入口 B — 设计中补录**：
```
设计中发现缺失器件 → 用户上传 PDF → ingest_asset() → confirm_import()
                                                         ↓
                                                 save_device() + save_datasheet()
                                                         ↓
                                                 重跑设计管线
```

### 4.4 PDF Datasheet 持久化

入库时 PDF 被复制到 `store/datasheets/`，DeviceModel 记录相对路径。设计时 AI 可通过 `get_device_datasheet` 工具获取 PDF 文本内容作为参考。

---

## 5. GUI 架构

```
gui.py → MainWindow
            ├── DesignPage     # 原理图设计标签页
            │   ├── 需求输入面板
            │   ├── GridCanvas（SVG 预览 + 缩放/平移）
            │   ├── ChatPanel（AI 对话 + 多轮修订）
            │   ├── BOM / SPICE / ERC / 设计摘要 标签页
            │   └── 运行日志
            └── LibraryPage    # 器件库管理标签页
                ├── 器件列表
                ├── PdfImportDialog（PDF 入库）
                ├── ManualEntryDialog（手动入库）
                └── EasyEDA 搜索导入
```

**Workers**（后台线程）：
- `SchemaForgeWorker` — 首次设计（创建 Orchestrator + 执行 AI agent loop）
- `SchemaForgeReviseWorker` — 多轮修订（直接调用 `session.revise()`）
- `IngestAssetWorker` — 器件补录
- `ConfirmImportWorker` — 确认入库

**关键 GUI 控件**：
- `ai_svg_mode` 复选框 — 切换渲染模式
- `review_rounds` Spinbox — 视觉审查轮次
- `model_name` 下拉 — 选择 AI 模型

---

## 6. 关键文件索引

### 6.1 入口

| 文件 | 作用 |
|------|------|
| `gui.py` | GUI 入口 |
| `main.py` | CLI 入口 |

### 6.2 AI Agent

| 文件 | 作用 |
|------|------|
| `agent/design_tools_v3.py` | 13 个原子工具 + 本地 SVG 渲染器 + System Prompts (~2400 行) |
| `agent/orchestrator.py` | AI function calling 编排器 |
| `agent/tool_registry.py` | 工具注册表 |
| `agent/tools.py` | 默认工具集（PDF 提取、器件库操作等） |
| `agent/protocol.py` | 协议定义 |

### 6.3 系统层（核心管线）

| 文件 | 作用 |
|------|------|
| `system/session.py` | SystemDesignSession — 设计会话主入口 (~1590 行) |
| `system/models.py` | SystemDesignIR, ModuleInstance 等数据模型 |
| `system/connection_rules.py` | 连接规则引擎（电源链、GPIO、SPI 等） |
| `system/synthesis.py` | 参数综合引擎（公式计算外围元件值） |
| `system/rendering.py` | schemdraw 渲染器 |
| `system/layout.py` | 系统级布局（模块排列 + 位置计算） |
| `system/resolver.py` | 器件解析（intent → DeviceModel） |
| `system/instances.py` | 组件实例收集 + 全局编号 |
| `system/export_bom.py` | BOM 导出（Markdown + CSV） |
| `system/export_spice.py` | SPICE 网表导出 |
| `system/ai_protocol.py` | AI 意图解析（自然语言 → SystemDesignRequest） |

### 6.4 器件库

| 文件 | 作用 |
|------|------|
| `library/models.py` | DeviceModel, SymbolDef, TopologyDef 等 |
| `library/store.py` | ComponentStore — JSON + SQLite 存储 |
| `library/service.py` | LibraryService — 入库/查询/删除的服务层 |
| `library/validator.py` | DeviceDraft 校验 + 转换 |
| `library/symbol_builder.py` | 确定性符号生成器（KLC 规范） |
| `library/dedupe.py` | 去重检测 |

### 6.5 器件提取

| 文件 | 作用 |
|------|------|
| `ingest/datasheet_extractor.py` | PDF/图片 → DeviceDraft 完整流程 |
| `ingest/ai_analyzer.py` | AI 分析器（文本/图片/融合） |
| `ingest/pdf_parser.py` | PDF 文本提取（PyMuPDF） |
| `ingest/easyeda_provider.py` | EasyEDA/JLCPCB 搜索 |

### 6.6 AI 客户端

| 文件 | 作用 |
|------|------|
| `ai/client.py` | LLM API 调用封装（**第 14-17 行不可修改**） |
| `ai/prompts.py` | 通用 prompt 构建 |

### 6.7 视觉审查

| 文件 | 作用 |
|------|------|
| `visual_review/loop.py` | 视觉审查主循环 |
| `visual_review/critic.py` | AI 视觉评审（vision API） |
| `visual_review/screenshot.py` | SVG → PNG 截图 |
| `visual_review/patch_planner.py` | 审查问题 → 修复计划 |
| `visual_review/patch_executor.py` | 执行修复 |
| `visual_review/scoring.py` | 评分系统 |

---

## 7. 数据流

### 7.1 首次设计（AI SVG 模式）

```
用户: "用 TPS54202 把 5V 降到 3.3V"
  │
  ├─→ SchemaForgeWorker (GUI 线程)
  │     ├─ 创建 SystemDesignSession(ai_svg_mode=True)
  │     ├─ 创建 Orchestrator + 注册 13 个工具
  │     └─ orch.run_turn(user_input)
  │
  ├─→ AI (kimi-k2.5) function calling loop:
  │     ├─ search_device_library("TPS54202")          → 确认库中有
  │     ├─ resolve_modules([{buck1, TPS54202, 5V→3.3V}]) → RESOLVED
  │     ├─ resolve_connections([{power_supply}])       → 引脚级连接
  │     ├─ synthesize_parameters()                     → L=3.3uH, Cin=10uF, ...
  │     ├─ get_device_datasheet("TPS54202")            → PDF 文本（若有）
  │     ├─ render_schematic_ai()                       → 本地 SVG + PNG
  │     ├─ review_schematic_visual()                   → Vision AI 审查
  │     └─ export_outputs()                            → BOM + SPICE
  │
  └─→ SystemDesignResult(bundle={svg, bom, spice})
```

### 7.2 多轮修订

```
用户: "去掉 D1"
  │
  ├─→ SchemaForgeReviseWorker
  │     └─ session.revise("去掉 D1")
  │
  ├─→ session._apply_direct_revision()
  │     ├─ 正则匹配: _REMOVE_MODULE_RE → "D1"
  │     ├─ 从 IR 中移除对应模块/连接
  │     └─ synthesize_all_modules() → 重新计算
  │
  └─→ session._regenerate_outputs()
        ├─ ai_svg_mode=True → _render_with_local_svg() → 本地 SVG 渲染器
        └─ ai_svg_mode=False → render_system_svg_with_metadata() → schemdraw
```

### 7.3 器件入库

```
用户上传 TPS54202.pdf
  │
  ├─→ extract_from_pdf(filepath)
  │     ├─ parse_pdf() → 文本提取
  │     ├─ analyze_combined() → AI 分析（引脚、参数、应用电路）
  │     └─ → DeviceDraft + application_circuit
  │
  ├─→ build_symbol() → SymbolDef（确定性生成）
  │
  ├─→ add_device_from_draft() → DeviceModel → JSON 文件
  │
  ├─→ save_datasheet() → PDF 复制到 store/datasheets/
  │
  └─→ build_recipe_from_application_circuit() → DesignRecipe
```

---

## 8. 约束与限制

### 8.1 不可修改
- `ai/client.py` 第 14-17 行（API key + model 配置）

### 8.2 模型限制
- `kimi-k2.5`: max_tokens 上限 98304
- `qwen3-coder-plus`: 备选模型
- API endpoint: `coding.dashscope.aliyuncs.com/v1`（仅支持以上两个模型）

### 8.3 渲染限制
- Cairo DLL 在 Windows 上不可用 → SVG→PNG 使用 Qt `QSvgRenderer`
- schemdraw 中文字体缺失 → CJK Glyph missing 警告（不影响功能）

### 8.4 AI SVG 模式限制
- 目前仅支持 Buck 和 LDO 拓扑布局
- Boost、MCU、LED、多模块布局尚未实现
- 修订操作走正则匹配（`_apply_direct_revision`），复杂修订可能回退到 AI 解析

---

## 9. 测试

```bash
# 运行全部测试
python -m pytest tests/ -q

# 当前状态: 1332 passed, 1 warning
```

测试按模块组织，覆盖：
- 系统管线 e2e（`test_system_session.py`, `test_golden_path_e2e.py`）
- 各子系统单元测试（连接规则、综合引擎、布局、渲染、导出）
- 器件库 CRUD + 校验 + 去重
- AI 分析器 + PDF 提取
- 设计层（澄清器、候选求解、审查引擎、参考设计）
- 视觉审查循环

---

## 10. 启动与开发

```bash
# 安装依赖
pip install -r requirements.txt

# 启动 GUI
python gui.py

# 启动 CLI
python main.py

# 运行测试
python -m pytest tests/ -q

# 运行特定测试
python -m pytest tests/test_system_session.py -v
```

---

## 11. 本次清理记录

### 新增功能
- **PDF Datasheet 持久化**：入库时保存 PDF，设计时 AI 可引用
- **`get_device_datasheet` 工具**：AI 按需获取 PDF 文本
- **修订渲染一致性**：AI SVG 模式下修订也使用本地渲染器
- **拓扑排序循环检测**：`layout.py` 防止无限递归

### 删除的遗留代码

| 模块 | 行数 | 原因 |
|------|------|------|
| `agent/design_tools.py` (v1) | ~750 | 被 v3 完全替代 |
| `agent/symbol_pipeline.py` | ~660 | 从未被导入 |
| `workflows/schemaforge_session.py` | ~540 | 标记 LEGACY_COMPAT_ONLY，被 SystemDesignSession 替代 |
| `workflows/design_session.py` | ~380 | 旧版单模块设计会话 |
| `workflows/ir_patch.py` | ~350 | IR 级修改引擎，无生产代码使用 |
| `workflows/state_machine.py` | ~180 | 状态机，仅被 design_session 使用 |
| `common/session_store.py` | ~120 | 会话持久化，无生产代码使用 |
| `core/engine.py` | ~310 | v1 渲染引擎，被系统层替代 |
| `render/composite.py` | ~140 | 从未被导入 |
| `render/divider.py` | ~100 | 被 TopologyRenderer 替代 |
| `render/ldo.py` | ~100 | 被 TopologyRenderer 替代 |
| `render/led.py` | ~100 | 被 TopologyRenderer 替代 |
| `render/rc_filter.py` | ~100 | 被 TopologyRenderer 替代 |
| `design/patch_engine.py` | ~360 | PatchOp 执行器，无生产代码使用 |
| `gui/widgets/svg_viewer.py` | ~250 | 被 GridCanvas 替代 |
| `flow/` 目录 | N/A | 不相关项目（固件管理） |

删除的测试文件：`test_design_tools.py`, `test_legacy_freeze.py`, `test_state_machine.py`, `test_ir_patch.py`, `test_design_session.py`, `test_session_pipeline_integration.py`, `test_engine.py`, `test_patch_engine.py`

从现有测试文件中剔除了所有依赖已删除模块的测试用例，保留了独立有效的测试（如 ToolRegistry、DesignRecipeSynthesizer、ERCChecker、TopologyRenderer 等）。

测试数量：1566 → 1332（-234 个遗留测试），0 failures。

---

## 12. 待开发功能

| 功能 | 优先级 | 说明 |
|------|--------|------|
| Boost 拓扑布局 | 高 | `_layout_boost()` — 类似 Buck 但输入输出互换 |
| MCU 最小系统布局 | 中 | `_layout_mcu()` — MCU + 去耦电容 + 晶振 |
| LED 指示灯布局 | 中 | `_layout_led()` — LED + 限流电阻 |
| 多模块布局 | 中 | 多个电源模块并排渲染在同一画布 |
| Boost/Flyback/Sepic 综合器 | 低 | `system/synthesis.py` 需要新公式引擎 |
