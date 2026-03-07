# SchemaForge Phase 5/6 — Agent 执行指南

本文档是给 Claude Code / AI Agent 每次启动时阅读的引导文档。你是一个执行 SchemaForge Phase 5/6 开发任务的 AI Agent。

**你已获得完整权限**，可以自由读写文件、执行命令、git 操作等，无需等待用户确认。请高效自主地完成任务。

---

## 第一步：了解你要做什么

1. 阅读方案设计文档：`docs/schemaforge-implementation.md`
2. 阅读任务拆分文档：`docs/schemaforge-tasks.md`

## 第二步：确定当前任务

扫描 `docs/schemaforge-tasks.md` 中的验收标准 checkbox：

- `- [ ]` 表示未完成
- `- [x]` 表示已完成

找到**第一个存在未完成 checkbox 的 Task**，这就是你本次要执行的任务。

如果一个 Task 的所有 checkbox 都已勾选，说明该任务已完成，跳过它。

**检查依赖**：确认你要执行的 Task 的依赖任务（看"依赖"字段）都已全部完成（所有 checkbox 都是 `[x]`）。如果依赖未完成，**停下来告诉用户**，不要强行开始。

## 第三步：执行任务

### 准备工作

1. **先 Read 现有代码**：阅读该 Task "涉及文件"中列出的所有现有文件，理解当前的代码模式和风格
2. **理解上下文**：如果依赖的 Task 已完成，Read 它们新建/修改的文件，了解可以复用的内容

### 代码规范

- **数据模型**：使用 Pydantic `BaseModel` 或 `@dataclass`，必须有完整 type hints
- **文档字符串**：所有公开类/方法必须有中文 docstring
- **错误处理**：使用 `schemaforge/common/errors.py` 中的统一错误模型
- **事件系统**：状态变更通过 `schemaforge/common/events.py` 的事件系统通知
- **测试**：使用 pytest，测试文件命名 `tests/test_*.py`
- **Import 风格**：`from __future__ import annotations` 放首行，使用绝对导入
- **AI 客户端**：使用 `schemaforge/ai/client.py` 中的 `call_llm` / `call_llm_json`，支持 mock 模式
- **GUI**：使用 PySide6 (Qt6)，遵循现有 `gui/widgets/` 中的模式

### 执行过程

1. 按照 Task 的"具体工作"逐项完成
2. 每完成一个可验证的步骤，检查对应的验收标准
3. 确保编译和测试通过：
   - 测试：`python -m pytest -q`
   - Lint：`python -m ruff check schemaforge/`

## 第四步：验收并打勾

所有工作完成后：

1. 逐条检查 Task 的验收标准
2. 对每一条通过的验收标准，在 `docs/schemaforge-tasks.md` 中将 `- [ ]` 改为 `- [x]`
3. 如果某条验收标准无法通过，保留 `- [ ]` 并在旁边加注释说明原因

**打勾示例**：
```markdown
# 改前
- [ ] 编译通过，测试全部 PASS

# 改后
- [x] 编译通过，测试全部 PASS
```

## 第五步：原子化 Git Commit

每个 Task 完成后必须创建一个独立的 commit，确保可以随时回退。

### Commit 规范

1. **分支**：在当前分支上提交
2. **只 commit 本 Task 的变更**：用 `git add` 精确添加本次修改/新建的文件，不要 `git add -A`
3. **Commit message 格式**：
   ```
   feat(schemaforge): Task N - 简要描述

   - 具体改动点 1
   - 具体改动点 2
   ```
4. **打勾文件也要 commit**：`docs/schemaforge-tasks.md` 的 checkbox 变更也包含在本次 commit 中
5. **不要 push**：只做本地 commit，push 由用户手动决定

### 示例

```bash
git add schemaforge/design/patch_engine.py tests/test_patch_engine.py docs/schemaforge-tasks.md
git commit -m "feat(schemaforge): Task 1 - PatchEngine PatchOp 执行器

- 新增 PatchEngine 类：apply / validate / preview
- 支持 set / add / remove / replace 四种操作
- JSON path 风格路径解析
- 10 个单元测试全 PASS"
```

### 出问题时的回退方式

用户可以通过以下命令回退某个 Task 的 commit：
```bash
# 查看 commit 历史，找到 Task N 的 commit hash
git log --oneline

# 回退某个 Task（保留工作区文件）
git revert <commit-hash>

# 或者硬回退到某个 Task 之前的状态
git reset --hard <commit-hash-before-task>
```

## 第六步：总结

输出一段简洁的完成报告：

```
## Task N 完成报告

**状态**：全部完成 / 部分完成
**Commit**：<commit hash 前 7 位>

**新建文件**：
- path/to/new/file1.py
- path/to/new/file2.py

**修改文件**：
- path/to/modified/file.py

**验收结果**：X/Y 项通过

**未通过项**（如有）：
- 原因说明

**下一个可执行的 Task**：Task M（依赖已满足）
```

---

## 关键文件速查

| 类别 | 路径 |
|---|---|
| 方案文档 | `docs/schemaforge-implementation.md` |
| 任务文档 | `docs/schemaforge-tasks.md` |
| 本指南 | `docs/schemaforge-agent-guide.md` |
| AI 客户端 | `schemaforge/ai/client.py` |
| AI Prompt | `schemaforge/ai/prompts.py` |
| Agent 协议 (PatchOp) | `schemaforge/agent/protocol.py` |
| Agent 编排器 | `schemaforge/agent/orchestrator.py` |
| 工具注册表 | `schemaforge/agent/tool_registry.py` |
| 设计规划器 | `schemaforge/design/planner.py` |
| 器件检索 | `schemaforge/design/retrieval.py` |
| 拓扑适配 | `schemaforge/design/topology_adapter.py` |
| 合理性检查 | `schemaforge/design/rationality.py` |
| 设计会话 | `schemaforge/workflows/design_session.py` |
| 状态机 | `schemaforge/workflows/state_machine.py` |
| 器件库模型 | `schemaforge/library/models.py` |
| 器件库存储 | `schemaforge/library/store.py` |
| 核心引擎 | `schemaforge/core/engine.py` |
| 数据模型 | `schemaforge/core/models.py` |
| 事件系统 | `schemaforge/common/events.py` |
| 错误模型 | `schemaforge/common/errors.py` |
| 进度追踪 | `schemaforge/common/progress.py` |
| GUI 主窗口 | `gui.py` |
| GUI 对话面板 | `schemaforge/gui/widgets/chat_panel.py` |
| GUI 器件库页面 | `schemaforge/gui/pages/library_page.py` |
| 渲染器 | `schemaforge/schematic/renderer.py` |
| 拓扑布局 | `schemaforge/schematic/topology.py` |

### 测试命令

```bash
# 运行全部测试
python -m pytest -q

# 运行单个测试文件
python -m pytest tests/test_patch_engine.py -v

# Lint 检查
python -m ruff check schemaforge/

# 启动 GUI
python gui.py

# CLI 演示
python main.py --demo
```
