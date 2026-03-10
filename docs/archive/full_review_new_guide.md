# SchemaForge 全量 Review 与新开发指南

> 日期：2026-03-06  
> 适用范围：当前仓库完整代码、测试、文档与后续迭代  
> 目标：给出一份基于现状的全量判断，并形成后续开发时应统一遵循的新指南

---

## 1. 结论先行

SchemaForge 当前已经不再是“功能演示脚本”，而是一个具备明确分层、稳定测试基线、开始进入第二阶段架构演进的工程原型。

当前结论如下：

- 主方向正确：不是让 AI 直接画图，而是让 AI 负责理解、规划、补全，本地代码负责约束、审查、渲染、导出
- 主干已成型：器件库、设计规划、合理性检查、IR、Patch、参考设计这些关键层已经进入代码，而不是停留在文档层
- 质量基线较好：当前 `python -m pytest -q` 为 `749 passed`
- 仍有工程化缺口：`ruff` 还有遗留问题，README 与实际状态不同步，依赖声明不完整，部分旧/新主链路并存
- 下一阶段重点不是“再接更多 AI”，而是“收敛真主链、统一中间表示、降低分支实现漂移”

---

## 2. 当前仓库的真实状态

### 2.1 已经形成的主能力

#### A. 旧主链路

- `schemaforge/core/engine.py`
- `schemaforge/core/templates.py`
- `schemaforge/render/`
- `schemaforge/core/exporter.py`
- `schemaforge/core/erc.py`

这一条链路更接近“模板驱动 + 渲染输出 + 导出”，是最早稳定可跑通的部分。

#### B. 新主链路

- `schemaforge/design/clarifier.py`
- `schemaforge/design/ir.py`
- `schemaforge/design/candidate_solver.py`
- `schemaforge/design/review.py`
- `schemaforge/design/topology_draft.py`
- `schemaforge/design/patch_engine.py`
- `schemaforge/workflows/ir_patch.py`
- `schemaforge/workflows/design_session.py`

这一条链路明显在向“可澄清、可审查、可 patch、可多轮修改”的方向演进，路线判断是对的。

#### C. 器件知识库与参考设计层

- `schemaforge/library/models.py`
- `schemaforge/library/reference_models.py`
- `schemaforge/store/devices/*.json`
- `schemaforge/store/reference_designs/*.json`

这意味着仓库已经从单纯的器件元数据，开始向“器件知识 + 参考拓扑知识”扩展。

#### D. 多入口交互层

- CLI：`main.py`
- GUI：`gui.py`、`schemaforge/gui/`
- Agent：`schemaforge/agent/orchestrator.py`

说明系统已经具备三类入口，但这也会带来“多入口共享同一主链”的治理问题。

---

## 3. 本次全量 review 的结果

### 3.1 强项

#### 1) 架构演进方向是对的

从 `schemaforge/design/ir.py`、`schemaforge/design/clarifier.py`、`schemaforge/design/review.py` 可以看出，项目已经从“生成结果”转向“治理生成过程”。

这是 AI 原理图设计系统能否走向实用的分水岭。

#### 2) 测试覆盖非常扎实

当前测试总量已经达到 `749`，涵盖：

- core 模型与计算
- library 存储、验证、去重、服务
- planner / retrieval / rationality / review
- IR / patch / topology draft / reference design
- engine / workflow / renderer / exporter

这让后续做重构成为可能。

#### 3) 器件知识层已经开始长出来

当前器件 JSON 已包含 `design_roles`、`required_companions` 等高层字段，说明项目开始摆脱“只有 symbol/pin 参数”的低维器件模型。

#### 4) 参考设计库方向很有价值

`schemaforge/store/reference_designs/` 的加入是很好的信号。原理图自动化的稳定性，往往来自复用成熟拓扑骨架，而不是完全自由生成。

### 3.2 当前主要问题

#### 1) 仓库存在“双主链并存”问题

目前旧主链 `core/engine` 与新主链 `design + workflows` 同时存在。

这本身不是错误，但如果没有明确规则，后续容易出现：

- 新功能写在新链路
- 旧入口仍走旧链路
- 测试分别保护不同世界
- 文档描述与真实执行路径逐渐偏离

这是当前最大的架构治理问题。

#### 2) README 与实际状态不同步

当前 README 仍写着 `399 tests passed`，但实际已是 `749 passed`；并且 Phase 5/6 相关能力已经部分进仓，README 仍偏旧阶段描述。

#### 3) 依赖声明不完整

`requirements.txt` 只有：

- `schemdraw`
- `pydantic`
- `openai`
- `rich`

但实际代码还依赖：

- `PySide6`
- `PyMuPDF` / `fitz`
- 可能还有与图像/PDF 解析相关的运行时依赖

这会直接影响新环境安装成功率。

#### 4) 工程卫生未完全收尾

`ruff` 仍有 17 个问题，主要集中在：

- 测试文件的 unused import
- `main.py` 中多余 f-string

这不是严重 bug，但反映出“测试通过”和“代码整洁”还没有收口为同一质量门。

#### 5) 入口层仍有打补丁式路径处理

`main.py` 和 `gui.py` 仍通过 `sys.path.insert(...)` 处理导入路径，这说明项目的包化与入口管理还没有完全正规化。

#### 6) 旧文档、任务文档、实现状态存在轻度漂移

当前有多份文档：

- `README.md`
- `plan.md`
- `docs/roadmap_next_phases.md`
- `docs/schemaforge-implementation.md`
- `docs/schemaforge-tasks.md`
- `docs/schemaforge-agent-guide.md`
- `docs/painpoint_execution_plan.md`

文档量不是问题，问题是它们现在分别承担“历史记录、任务拆分、未来路线、执行指南”等不同角色，容易让后续开发者不知道哪个才是最新真相。

---

## 4. 当前最重要的判断

### 4.1 不要再把重点放在“让 AI 更自由生成”

SchemaForge 现在最需要的，不是放宽 AI 输出自由度，而是继续增强以下能力：

- 缺失约束识别
- 假设管理
- 候选解排序
- 器件知识化
- 设计审查
- 局部 patch
- 参考设计复用

### 4.2 后续核心问题不是“能不能生成”，而是“生成后能否稳定演进”

真正决定项目质量的，是这三个问题：

1. 用户改一个约束后，系统能不能局部重算
2. 系统能不能解释为什么选这个器件/参数
3. 不同入口是不是共享同一套设计真值

只要这三点抓住了，项目就会持续变稳；抓不住，功能越多越容易分裂。

---

## 5. 新指南：后续开发必须遵守的规则

以下内容是这份文档最重要的部分。

### Rule 1：统一“唯一真主链”

从现在开始，应明确：

- **旧主链** `core/engine.py` 负责兼容、回归和已有模板能力
- **新主链** `design/* + workflows/*` 负责未来所有新增能力

新增功能若涉及以下任一能力，必须优先落在新主链：

- 需求澄清
- 多轮修改
- 候选方案
- 设计审查
- 参考设计
- IR / history / snapshot / patch

禁止新增复杂能力继续直接堆进 `core/engine.py`。

### Rule 2：Design IR 是唯一中间真值

凡是跨阶段流转的信息，都应该进入 IR，而不是散落在临时 dict、GUI state、LLM 原始响应中。

必须优先进入 IR 的信息包括：

- 原始需求与规范化需求
- known constraints / missing constraints / assumptions
- module intents
- 候选器件与最终选择
- derived parameters
- review findings
- reference design 使用记录
- patch history

如果一个新功能无法自然映射到 IR，先不要急着写功能，先补 IR 模型。

### Rule 3：AI 输出永远不能直接成为最终真值

AI 可以输出：

- 澄清问题
- 模块建议
- 器件候选
- 方案排序依据
- patch 意图
- 解释文本

AI 不应直接决定：

- 最终网络连接真值
- 最终参数真值
- 持久化真值
- 渲染真值

所有 AI 输出必须经过本地模型层和规则层吸收后，才能进入设计结果。

### Rule 4：新增功能必须同时回答“如何审查”

以后新增任何设计能力时，不能只实现“生成”，还必须同步考虑：

- 怎么验证
- 怎么审查
- 怎么解释
- 怎么 patch

例如新增一个 `buck` 类设计，不应只新增 render 和 template，还至少要补：

- clarifier 所需关键约束
- candidate solver 的评分维度
- review 规则
- IR 字段映射
- patch 影响分析

### Rule 5：优先扩深，不优先扩宽

短期内不要急着扩很多新电路品类。

更推荐的做法是把已有高频题材做深：

- LDO 电源
- LED 指示
- 分压采样
- RC 滤波
- LDO + LED 组合

把这几类做到：

- 能澄清
- 能出候选
- 能审查
- 能 patch
- 能参考设计复用

之后再扩到更复杂品类。

### Rule 6：所有入口共用同一条后端规则

CLI、GUI、Agent 只是入口，不应该各自长出不同的业务逻辑。

理想结构应是：

- CLI 负责输入输出展示
- GUI 负责交互与可视化
- Agent 负责对话编排
- 真实业务逻辑统一进入 `design/*` 和 `workflows/*`

如果某个规则只存在于 GUI worker 或 CLI 分支里，这是坏味道。

### Rule 7：测试与文档要跟着主链一起迁移

以后每做一项新功能，都必须同步完成：

- 新主链测试
- README 状态更新
- 新指南或任务文档更新

避免出现“代码已经到 Phase 6，README 还写 Phase 4”的情况。

---

## 6. 推荐的开发工作流

以后新增一个能力，建议统一按这 8 步走：

1. 先定义该能力在 IR 中的数据位置
2. 定义澄清所需的输入约束
3. 定义候选方案与评分维度
4. 定义器件知识字段需求
5. 定义 review 规则
6. 定义 patch 影响范围
7. 再接入 GUI/CLI/Agent 展示
8. 最后更新 README 和任务文档

顺序不能反过来。

如果先做 GUI，再回头补 IR 和规则，最后一定会返工。

---

## 7. 建议的模块职责边界

### `schemaforge/design/clarifier.py`

只负责：

- 缺失约束识别
- assumptions 产出
- 是否允许继续设计

不要把具体器件选择逻辑塞进这里。

### `schemaforge/design/candidate_solver.py`

只负责：

- 候选方案生成
- 多维评分
- tradeoff 描述

不要直接承担渲染或 session 持久化。

### `schemaforge/design/review.py`

只负责：

- 工程审查规则
- warning / blocking / recommendation 产出

不要把交互性问答塞进这里。

### `schemaforge/design/topology_draft.py`

只负责：

- 拓扑草稿生成与校验
- 参考设计骨架映射

不要直接成为最终渲染器。

### `schemaforge/workflows/ir_patch.py`

只负责：

- PatchOp 校验
- IR 级别修改
- 影响范围判断

不要直接做复杂业务决策。

### `schemaforge/workflows/design_session.py`

只负责：

- 串联澄清、规划、选择、审查、patch
- 维护 session 状态与快照

不要在这里重复实现 domain 规则。

---

## 8. 近期优先级建议

### Priority 1：统一主链与文档真相

应优先完成：

- README 更新到当前架构现状
- 明确“旧主链兼容 / 新主链演进”的边界
- 统一当前推荐入口和推荐开发入口

### Priority 2：收口工程卫生

应优先完成：

- 修复 `ruff` 全量问题
- 补齐 `requirements.txt`
- 校正文档中旧测试数与旧阶段表述

### Priority 3：让 IR 真正成为主心骨

检查以下能力是否已完全围绕 IR 工作：

- clarifier 输出
- candidate solver 输出
- review 输出
- patch history
- reference design 记录

凡是还漂浮在外部数据结构里的内容，都建议回收到 IR。

### Priority 4：推进复杂度而不是功能数量

优先把已有几类设计做成“完整闭环”而不是再开新品类。

---

## 9. 推荐的短期执行顺序

如果接下来只做最有价值的 6 步，我建议顺序是：

1. 修 README、依赖、lint，统一项目表层真相
2. 明确旧主链与新主链边界
3. 审核 IR 是否已经覆盖 clarifier / review / candidate / patch
4. 用一个题材做全链路闭环样板，例如 `LDO + LED`
5. 让 GUI/CLI/Agent 全部走同一条后端设计链
6. 再扩展第二个复杂题材

这一顺序比继续平铺新功能更稳。

---

## 10. 新功能准入清单

以后每加一个新题材或新能力，必须先回答以下问题：

- 它在 IR 里怎么表示？
- 它需要哪些必须澄清字段？
- 它的候选解评分维度是什么？
- 它依赖哪些器件知识字段？
- 它需要哪些 review 规则？
- 它支持哪些 patch 操作？
- 它是否有参考设计骨架？
- 它的测试最少覆盖哪些场景？

如果这些问题答不全，不建议开工。

---

## 11. 建议的质量门

建议把以下内容作为默认质量门：

- `python -m pytest -q` 全绿
- `python -m ruff check schemaforge gui.py tests main.py` 全绿
- README 与当前测试数一致
- 新增功能至少包含：单元测试 + 一份文档更新
- 新主链功能不得绕开 IR

---

## 12. 最终建议

SchemaForge 当前最值得珍惜的不是“功能多”，而是**已经开始形成一条正确的工程化演进路径**。

后续最怕的不是做慢，而是：

- 多入口各自演化
- 新旧主链继续混写
- 文档与实现逐渐漂移
- 新功能绕过 IR 和 review

所以新的开发策略应该很明确：

- 统一真主链
- 统一中间真值
- 统一质量门
- 统一文档真相

只要坚持这 4 点，SchemaForge 就会从“能跑的 AI 原理图原型”逐步升级成“能持续迭代的 AI 设计系统”。

