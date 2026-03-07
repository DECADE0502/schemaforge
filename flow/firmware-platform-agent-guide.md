# 固件平台改造 — Agent 执行指南

本文档是给 Claude Code / happy 每次启动时阅读的引导文档。你是一个执行固件平台改造任务的 AI Agent。

**你已获得完整权限（yolo mode）**，可以自由读写文件、执行 bash 命令、git 操作等，无需等待用户确认。请高效自主地完成任务。

---

## 第一步：了解你要做什么

1. 阅读方案设计文档：`docs/firmware-platform-implementation.md`
2. 阅读任务拆分文档：`docs/firmware-platform-tasks.md`

## 第二步：确定当前任务

扫描 `docs/firmware-platform-tasks.md` 中的验收标准 checkbox：

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

- **后端**：遵循 go-zero 框架模式（handler → logic 分层），响应使用 `BaseRsp` 格式
- **前端**：使用 Ant Design 组件，遵循 `use*` hooks 模式，API 定义在 `front/src/api/` 下
- **路由注册**：在 `go-backend/server/package_manager_svr/internal/handler/routes.go` 中添加
- **类型定义**：在 `go-backend/server/package_manager_svr/internal/types/types.go` 中添加

### 执行过程

1. 按照 Task 的"具体工作"逐项完成
2. 每完成一个可验证的步骤，检查对应的验收标准
3. 确保编译通过：
   - Go：`go build ./...`（在 go-backend 目录下）
   - 前端：`npx tsc --noEmit`（在 front 目录下）

### 本地开发服务

执行以下命令可一键启动前后端联调环境（含真实数据）：

```bash
cd go-backend/server/package_manager_svr && bash dev.sh
```

- 前端：`http://localhost:18080`
- 后端：`http://localhost:18890`
- `Ctrl-C` 停止所有进程

**注意**：Playwright MCP 端到端验证时应使用 `http://localhost:18080` 访问页面，该环境前后端均可用，无需手动分别启动。

## 第四步：验收并打勾

所有工作完成后：

1. 逐条检查 Task 的验收标准
2. 对每一条通过的验收标准，在 `docs/firmware-platform-tasks.md` 中将 `- [ ]` 改为 `- [x]`
3. 如果某条验收标准无法通过，保留 `- [ ]` 并在旁边加注释说明原因

**打勾示例**：
```markdown
# 改前
- [ ] 编译通过，无 lint 错误

# 改后
- [x] 编译通过，无 lint 错误
```

## 第五步：原子化 Git Commit

每个 Task 完成后必须创建一个独立的 commit，确保可以随时回退。

### Commit 规范

1. **分支**：在当前分支 `feature/caozhengyang_6552758290_betterPackageManager` 上提交
2. **只 commit 本 Task 的变更**：用 `git add` 精确添加本次修改/新建的文件，不要 `git add -A`
3. **Commit message 格式**：
   ```
   feat(firmware-platform): Task N - 简要描述

   - 具体改动点 1
   - 具体改动点 2
   ```
4. **打勾文件也要 commit**：`docs/firmware-platform-tasks.md` 的 checkbox 变更也包含在本次 commit 中
5. **不要 push**：只做本地 commit，push 由用户手动决定

### 示例

```bash
git add go-backend/util/oss/oss.go go-backend/util/oss/oss_meta.go docs/firmware-platform-tasks.md
git commit -m "feat(firmware-platform): Task 1 - OSS 元数据读写工具函数

- 新增 GetObjectMeta / SetObjectMeta / CopyObjectWithMeta / ListObjectsWithMeta
- 新增 FirmwareMeta 结构体及 ToOSSHeaders / FromOSSHeaders 转换
- release note 超长截断 + .releasenote.md 回读逻辑
- ListObjectsWithMeta 并发获取 meta（上限 10）"
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
- path/to/new/file1.go
- path/to/new/file2.go

**修改文件**：
- path/to/modified/file.go

**验收结果**：X/Y 项通过

**未通过项**（如有）：
- 原因说明

**下一个可执行的 Task**：Task M（依赖已满足）
```

---

## 关键文件速查

| 类别 | 路径 |
|---|---|
| 方案文档 | `docs/firmware-platform-implementation.md` |
| 任务文档 | `docs/firmware-platform-tasks.md` |
| 本指南 | `docs/firmware-platform-agent-guide.md` |
| OSS 工具 | `go-backend/util/oss/oss.go` |
| 后端 handler | `go-backend/server/package_manager_svr/internal/handler/` |
| 后端 logic | `go-backend/server/package_manager_svr/internal/logic/` |
| 后端类型 | `go-backend/server/package_manager_svr/internal/types/types.go` |
| 路由注册 | `go-backend/server/package_manager_svr/internal/handler/routes.go` |
| Worker | `go-backend/server/package_manager_svr/internal/worker/worker.go` |
| 前端 API | `front/src/api/package-manager/index.ts` |
| 前端 Hooks | `front/src/hooks/usePackageManager.ts` |
| 前端页面 | `front/src/pages/PackageManagement.tsx` |
