# SchemaForge — AI 辅助电子原理图设计工具

> 约束驱动的桌面端 AI 原理图生成器。从自然语言需求到 SVG 原理图 + BOM + SPICE 网表，全流程本地化。

## 项目定位

SchemaForge 是一个桌面端 AI 辅助电子原理图设计工具，包含：

1. **器件符号库管理系统** — 支持手动录入、在线搜索 (EasyEDA)，AI 交互式质疑补全，入库
2. **原理图设计系统** — 从器件库读取符号+拓扑，AI 驱动需求澄清→多候选→工程审查→渲染→多轮修改

核心理念：**AI 只做决策和提问，本地工具负责执行**。模型不直接控制状态持久化，所有状态流转由本地规则层和状态机控制。

## 技术栈

| 层级 | 技术 |
|------|------|
| GUI | PySide6 (Qt6 桌面应用) |
| AI 模型 | kimi-k2.5 (DashScope OpenAI 兼容接口) |
| 原理图渲染 | schemdraw (Python SVG 生成) |
| 数据模型 | Pydantic v2 (强类型) |
| 数据存储 | JSON 文件 + SQLite 索引 |
| 在线数据 | EasyEDA 开放 API |
| 测试 | pytest + ruff |

## 快速开始

```bash
# 安装依赖
pip install -r requirements.txt

# 启动 GUI
python gui.py

# 运行测试
python -m pytest -q

# Lint 检查（质量门）
python -m ruff check schemaforge gui.py tests main.py
```

## 当前状态：878 tests passed, ruff 全绿

### 已完成阶段

| 阶段 | 内容 | 测试数 |
|------|------|--------|
| P0 修复 | 根因修复模板占位符解析、去硬编码 | 83 |
| Phase 1 | 共享基础设施 (events, errors, progress, state_machine, agent protocol) | 59 |
| Phase 2 | 器件库导入 v1 (validator, dedupe, service, GUI 表单/搜索面板) | 64 |
| Phase 3 | PDF/图片多模态导入 (ai_analyzer, datasheet_extractor, import_wizard) | 32 |
| Phase 4 | 库驱动设计 v1 (retrieval, planner, topology_adapter, rationality, design_session) | 99 |
| Phase A | Design IR（中间真值层）+ PatchEngine + TopologyDraft | 73 |
| Phase B | 需求澄清器（Clarifier）— 缺失约束检测、假设管理 | 55 |
| Phase C | 器件库升级 — 8 个设计知识字段、角色化检索 | 31 |
| Phase D | 候选方案求解器 — 6 维评分、多候选排序 | 47 |
| Phase E | 设计审查引擎 — 18 条工程审查规则 | 47 |
| Phase F | IR 级 Patch 引擎 — 6 种多轮修改操作 | 54 |
| Phase G | 参考设计库 — 5 个经过验证的参考设计 | 43 |
| Step 6 | Buck降压转换器 — 第二个复杂电路类型 + RC滤波器审查规则 | 47 |

### 双主链架构

当前存在两条主链路，各有明确职责：

- **旧主链** `core/engine.py` → 模板驱动 + 渲染 + 导出，负责兼容和已有模板能力
- **新主链** `design/* + workflows/*` → 需求澄清 → 候选方案 → 审查 → Patch → IR，负责未来所有新增能力

**规则：新增复杂能力必须落在新主链。禁止继续往 `core/engine.py` 堆叠新能力。**

## 项目结构

```
schemaforge/
├── ai/                         # AI 客户端 (kimi-k2.5)
│   ├── client.py               # LLM API 封装
│   ├── prompts.py              # Prompt 模板
│   └── validator.py            # AI 输出验证
├── agent/                      # AI 编排层
│   ├── protocol.py             # AgentStep/AgentAction/PatchOp 协议
│   ├── tool_registry.py        # 工具注册表
│   └── orchestrator.py         # AI 多轮对话循环
├── common/                     # 共享基础设施
│   ├── errors.py               # 统一错误模型
│   ├── events.py               # 事件系统
│   ├── progress.py             # 进度追踪
│   └── session_store.py        # 会话存储
├── core/                       # 旧主链（模板驱动渲染）
│   ├── engine.py               # SchemaForgeEngine 主引擎
│   ├── models.py               # 数据模型 (PinType, CircuitTemplate, ...)
│   ├── templates.py            # 电路模板定义
│   ├── calculator.py           # 参数计算器
│   ├── erc.py                  # 电气规则检查
│   └── exporter.py             # BOM/SPICE 导出
├── design/                     # 新主链（设计治理层）
│   ├── ir.py                   # Design IR — 唯一中间真值
│   ├── clarifier.py            # 需求澄清器（缺失约束检测）
│   ├── planner.py              # AI 设计规划（自然语言→模块需求）
│   ├── retrieval.py            # 器件库检索（评分排序+角色匹配）
│   ├── candidate_solver.py     # 候选方案求解器（多候选+6维评分）
│   ├── review.py               # 设计审查引擎（26条工程审查规则）
│   ├── rationality.py          # 合理性检查（电压/电流/功率）
│   ├── topology_adapter.py     # 拓扑适配（DeviceModel→可渲染格式）
│   ├── topology_draft.py       # 拓扑草稿生成器
│   └── patch_engine.py         # PatchOp 执行器（design_spec 级）
├── gui/                        # GUI 组件 (PySide6)
│   ├── widgets/                # 可复用控件
│   ├── pages/                  # 页面
│   └── workers/                # 工作流线程
├── ingest/                     # 数据导入
│   ├── easyeda_provider.py     # EasyEDA API
│   ├── ai_analyzer.py          # AI 文本/视觉分析
│   └── datasheet_extractor.py  # Datasheet 提取编排
├── library/                    # 器件库
│   ├── models.py               # DeviceModel（含8个设计知识字段）
│   ├── reference_models.py     # ReferenceDesign 参考设计模型
│   ├── store.py                # JSON + SQLite 存储
│   ├── validator.py            # DeviceDraft 校验
│   ├── dedupe.py               # 重复检测
│   └── service.py              # 服务层 CRUD
├── render/                     # 旧渲染器
├── schematic/                  # 新渲染器
│   ├── renderer.py             # TopologyRenderer
│   └── topology.py             # 5 种布局策略
├── store/
│   ├── devices/                # 器件库 JSON 数据（5个器件）
│   └── reference_designs/      # 参考设计库（6个参考设计）
└── workflows/                  # 工作流编排
    ├── state_machine.py        # 通用状态机
    ├── design_session.py       # 设计会话（端到端编排）
    └── ir_patch.py             # IR 级 Patch 引擎（6种修改操作）
```

## 核心设计约束

1. **AI 只做决策，不控制状态** — `AgentStep` 结构化输出，`Orchestrator` 控制循环
2. **模板约束安全** — AI 只能选择模板 + 填参数，不能自己发明连接
3. **器件库驱动** — 所有器件数据从库中读取，不硬编码
4. **本地工具封装** — AI 通过 `ToolRegistry` 调用本地工具，不直接操作文件
5. **合理性检查** — 渲染前强制执行电压/电流/功率/兼容性检查
6. **Design IR 是唯一中间真值** — 跨阶段流转的信息都进入 IR，不散落在临时 dict 中
7. **新增功能必须同时回答"如何审查"** — 不能只实现生成，还要验证、审查、解释、patch

## 质量门

```bash
# 以下命令必须全部通过才算质量合格
python -m pytest -q                                        # 878 passed
python -m ruff check schemaforge gui.py tests main.py     # All checks passed
```

## 开发指南

详细的开发规则和新功能准入清单见 [`docs/full_review_new_guide.md`](docs/full_review_new_guide.md)。

核心规则：
- **Rule 1**：新增功能走新主链（`design/*` + `workflows/*`）
- **Rule 2**：Design IR 是唯一中间真值
- **Rule 3**：AI 输出永远不能直接成为最终真值
- **Rule 4**：新增功能必须同时回答"如何审查"
- **Rule 5**：优先扩深，不优先扩宽
- **Rule 6**：所有入口（CLI/GUI/Agent）共用同一条后端规则
- **Rule 7**：测试与文档跟着主链一起迁移

## AI 配置

模型配置在 `schemaforge/ai/client.py`：

| 配置项 | 值 |
|--------|-----|
| Model | kimi-k2.5 |
| Base URL | `https://coding.dashscope.aliyuncs.com/v1` |
| API Key | 硬编码在代码中 |

> 交接后需要更换为你自己的 API Key。
