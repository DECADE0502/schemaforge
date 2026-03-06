# SchemaForge — AI 辅助电子原理图设计工具

> 约束驱动的桌面端 AI 原理图生成器。从自然语言需求到 SVG 原理图 + BOM + SPICE 网表，全流程本地化。

## 项目定位

SchemaForge 是一个 **两个子系统** 的桌面端 AI 辅助电子原理图设计工具：

1. **器件符号库管理系统** — 支持 PDF datasheet 上传、图片上传、手动录入、在线搜索 (EasyEDA)，AI 交互式质疑补全，入库
2. **原理图绘制系统** — 从器件库读取符号+拓扑，AI 驱动生成原理图 SVG + BOM + SPICE，支持多轮迭代修改

核心理念：**AI 只做决策和提问，本地工具负责执行**。模型不直接控制状态持久化，所有状态流转由本地状态机控制。

## 技术栈

| 层级 | 技术 |
|------|------|
| GUI | PySide6 (Qt6 桌面应用) |
| AI 模型 | kimi-k2.5 (DashScope OpenAI 兼容接口) |
| 原理图渲染 | schemdraw (Python SVG 生成) |
| 数据存储 | JSON 文件 + SQLite 索引 |
| 在线数据 | EasyEDA 开放 API |
| PDF 解析 | pdfplumber |
| 测试 | pytest + ruff |

## 快速开始

```bash
# 安装依赖
pip install pyside6 schemdraw pydantic openai pdfplumber

# 启动 GUI
python gui.py

# 运行测试
python -m pytest -q

# Lint 检查
python -m ruff check schemaforge/ gui.py tests/
```

## 当前状态：399 tests passed

### 已完成阶段

| 阶段 | 内容 | 测试数 |
|------|------|--------|
| P0 修复 | 根因修复模板占位符解析、去硬编码 | 83 |
| Phase 1 | 共享基础设施 (events, errors, progress, state_machine, agent protocol) | 59 |
| Phase 2 | 器件库导入 v1 (validator, dedupe, service, GUI 表单/搜索面板) | 64 |
| Phase 3 | PDF/图片多模态导入 (ai_analyzer, datasheet_extractor, import_wizard) | 32 |
| Phase 4 | 库驱动设计 v1 (retrieval, planner, topology_adapter, rationality, design_session) | 99 |

### 后续阶段 (未实施)

| 阶段 | 内容 |
|------|------|
| Phase 5 | 多轮修改与自动拓扑 (PatchOp, TopologyDraft) |
| Phase 6 | 系统收尾与稳定化 |

## 项目结构

```
schemaforge/
├── ai/                         # AI 客户端 (kimi-k2.5)
│   ├── client.py               # LLM API 封装 (硬编码 kimi-k2.5)
│   ├── prompts.py              # Prompt 模板
│   └── validator.py            # AI 输出验证
├── agent/                      # AI 编排层
│   ├── protocol.py             # AgentStep/AgentAction 协议
│   ├── tool_registry.py        # 工具注册表
│   └── orchestrator.py         # AI 多轮对话循环
├── common/                     # 共享基础设施
│   ├── errors.py               # 统一错误模型
│   ├── events.py               # 事件系统
│   ├── progress.py             # 进度追踪
│   └── session_store.py        # 会话存储
├── core/                       # 核心引擎 (旧路径)
│   ├── engine.py               # SchemaForgeEngine 主引擎
│   ├── models.py               # 数据模型 (PinType, CircuitTemplate, ...)
│   ├── templates.py            # 电路模板定义
│   ├── calculator.py           # 参数计算器
│   ├── erc.py                  # 电气规则检查
│   └── exporter.py             # BOM/SPICE 导出
├── design/                     # 设计编排层 (Phase 4 新路径)
│   ├── retrieval.py            # 器件库检索 (评分排序)
│   ├── planner.py              # AI 设计规划 (自然语言→模块需求)
│   ├── topology_adapter.py     # 拓扑适配 (DeviceModel→可渲染格式)
│   └── rationality.py          # 合理性检查 (电压/电流/功率)
├── gui/                        # GUI 组件
│   ├── widgets/                # 可复用控件
│   │   ├── chat_panel.py       # AI 对话面板
│   │   ├── progress_header.py  # 进度头
│   │   ├── device_form.py      # 器件录入表单
│   │   ├── device_search_panel.py  # EasyEDA 搜索面板
│   │   └── import_wizard.py    # PDF/图片导入向导
│   ├── pages/
│   │   └── library_page.py     # 器件库管理页面
│   └── workers/
│       └── workflow_worker.py  # 工作流线程
├── ingest/                     # 数据导入
│   ├── pdf_parser.py           # PDF 解析
│   ├── image_recognizer.py     # 图片识别
│   ├── easyeda_provider.py     # EasyEDA API
│   ├── ai_analyzer.py          # AI 文本/视觉分析
│   └── datasheet_extractor.py  # Datasheet 提取编排
├── library/                    # 器件库
│   ├── models.py               # DeviceModel, SymbolDef, TopologyDef
│   ├── store.py                # JSON + SQLite 存储
│   ├── validator.py            # DeviceDraft 校验
│   ├── dedupe.py               # 重复检测
│   └── service.py              # 服务层 CRUD
├── render/                     # 渲染器 (旧路径)
├── schematic/                  # 通用渲染 (新路径)
│   ├── renderer.py             # TopologyRenderer
│   └── topology.py             # 5 种布局策略
├── store/devices/              # 器件库 JSON 数据
│   ├── AMS1117-3.3.json
│   ├── LED_INDICATOR.json
│   ├── RC_LOWPASS.json
│   └── VOLTAGE_DIVIDER.json
└── workflows/                  # 工作流编排
    ├── state_machine.py        # 通用状态机
    └── design_session.py       # 设计会话 (Phase 4 端到端)
```

## GPT 路线方案

**位置**: [`docs/roadmap_next_phases.md`](docs/roadmap_next_phases.md)

该文档由用户提供给 GPT 生成，作为后续开发的**执行约束和验收标准**。包含：
- 分阶段开发路线图
- 每阶段的目标、非目标、验收标准
- 绝对禁止事项 (§2.2)
- 执行纪律约束 (§18)

> ⚠️ 注意: 该文件在保存时可能被截断，只保留了前 32 行。完整内容在对话上下文中，关键约束已被严格遵守。

## 核心设计约束

1. **AI 只做决策，不控制状态** — `AgentStep` 结构化输出，`Orchestrator` 控制循环
2. **模板约束安全** — AI 只能选择模板 + 填参数，不能自己发明连接
3. **器件库驱动** — 所有器件数据从库中读取，不硬编码
4. **本地工具封装** — AI 通过 `ToolRegistry` 调用本地工具，不直接操作文件
5. **合理性检查** — 渲染前强制执行电压/电流/功率/兼容性检查

## AI 配置

模型配置硬编码在 `schemaforge/ai/client.py`：

| 配置项 | 值 |
|--------|-----|
| Model | kimi-k2.5 |
| Base URL | `https://coding.dashscope.aliyuncs.com/v1` |
| API Key | 硬编码在代码中 |

> 交接后需要更换为你自己的 API Key。

## 交接须知

1. **Python 版本**: 3.14 (开发环境), 建议 3.11+
2. **验证命令**: `python -m pytest -q` (应为 399 passed)
3. **GUI 启动**: `python gui.py`
4. **下一步开发**: Phase 5 — 多轮修改与自动拓扑
5. **GPT 方案**: `docs/roadmap_next_phases.md` (截断版，关键约束在 README 和代码注释中)
6. **UI 语言**: 全中文
