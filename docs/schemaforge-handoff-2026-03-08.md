# SchemaForge 严厉 Handoff 文档（2026-03-08）

## 文档目的

这份文档不是宣传材料，也不是 roadmap 美化稿。

这是一份给下一个 AI / 工程师的**真实接管说明**，目标只有两个：

1. 说清楚 SchemaForge 的**最终目标到底是什么**。
2. 说清楚它**现在到底烂在哪、真到哪、假到哪**。

本文件优先基于以下来源：

- 代码直接检查
- 当前仓库中的现有文档
- 当前测试结构
- 当前 GUI / CLI 入口

本文件**没有**在本轮重新跑完整 `pytest` / `ruff`，因此凡是涉及“测试是否通过”的内容，除非特别注明，均视为**来自仓库现有叙述和代码结构判断，不是本轮重新验证结果**。

---

## 一、项目最终目标

SchemaForge 的最终目标不是“根据模板拼几张图”，也不是“识别一下型号然后选现成 reference design”。

它的真正目标应该是：

> 用户只用自然语言描述需求，系统自动完成从需求理解、器件识别、器件补录、参数计算、规则约束、系统连线、原理图渲染、BOM 导出、SPICE 导出，到多轮修改的全过程。

### 这个目标的最低正确解释

系统必须同时满足下面这些条件：

1. **精确型号识别**
   - 用户说 `TPS54202`，系统就必须按 `TPS54202` 处理。
   - 不能偷换近似料号，不能模糊命中，不能“差不多”。

2. **AI 只负责理解，不负责乱画**
   - AI 负责理解用户意图、提取结构化请求、识别模块关系、必要时分析图片和 datasheet。
   - 本地代码负责器件库、引脚映射、参数计算、约束规则、绘图、BOM、SPICE。

3. **器件库缺失时自动补录**
   - 本地库没有器件时，系统应引导用户提供 PDF / 图片。
   - AI 提取引脚和关键参数，本地生成 KiCad 兼容 symbol / 结构化器件记录，用户确认后入库。

4. **外围元件必须有工程依据**
   - Buck / LDO / 运放 / MCU 最小系统等外设计算，必须来自 datasheet、公式、规则，而不是“拍脑袋默认值”。

5. **输出必须是完整系统原理图，不是单模块示意图**
   - 多模块场景下，必须输出一张系统图。
   - 模块之间的电气连接必须真实存在，而不是视觉上摆在一起。

6. **支持多轮修改**
   - 文本修改必须基于已有设计状态增量修订。
   - 图片反馈也必须进入真实修订闭环，而不是只显示缩略图。

7. **不限死板模板拓扑**
   - 不能只支持几种硬编码电源拓扑。
   - 系统应围绕“器件信息 + 模块规则 + 连接规则 + 约束规则”工作，而不是围绕“预制现成图”。

### 一个能代表最终目标的典型输入

> `20V 输入，用 TPS54202 降压到 5V，再用 AMS1117 降到 3.3V，给 STM32F103C8T6 供电，并且用 MCU 的 PA1 控制一颗 LED`

对应的正确输出应该至少包含：

- `TPS54202` Buck 模块及其外围
- `AMS1117` LDO 模块及其外围
- `STM32F103C8T6` 至少电源与 `PA1`
- `LED + 限流电阻`
- `Buck.VOUT -> LDO.VIN`
- `LDO.VOUT -> MCU.VCC`
- `MCU.PA1 -> LED`
- 合并后的全局 BOM
- 合并后的共享网络 SPICE

如果做不到这一条，就不能说 SchemaForge 达到了系统级目标。

---

## 二、我本轮实际核实到的事实

### 2.1 真的存在的东西

1. **本地器件库是存在的**
   - `schemaforge/store/devices` 当前有 9 个 JSON 器件文件。
   - 直接可见的器件包括：`TPS54202`、`TPS5430`、`AMS1117-3.3`、`STM32F103C8T6`、`TPS61023` 等。

2. **新的系统级会话层是存在的**
   - `schemaforge/system/session.py:75` 定义了 `SystemDesignSession`。
   - 它有 `start()` 与 `revise()`，路径上也接了 `resolver`、`connection_rules`、`synthesis`、`export`、`rendering`。

3. **GUI 生成流程已经部分切到新系统路径**
   - `schemaforge/gui/workers/engine_worker.py:74` 在 `SchemaForgeWorker` 中实例化的是 `SystemDesignSession`。
   - `schemaforge/gui/pages/design_page.py:376` 生成时使用 `SchemaForgeWorker`。
   - `schemaforge/gui/pages/design_page.py:380` 保存 `session_ready` 回来的会话实例。

4. **系统级渲染器确实存在**
   - `schemaforge/system/rendering.py:841` 定义了 `render_system_svg()`。
   - 它尝试在一张图里布局多个模块并连接线。

5. **系统级 visual review 代码确实存在**
   - `schemaforge/visual_review/loop.py:51` 有 `run_visual_review_loop()`。
   - `schemaforge/visual_review/patch_executor.py:42` 有 `create_layout_state_from_metadata()`。
   - `schemaforge/visual_review/patch_executor.py:115` 有 `apply_visual_patches()`。

### 2.2 明确是假的、断的、或者名不副实的东西

1. **GUI 图片粘贴功能没有接到任何真实分析链路**
   - `schemaforge/gui/pages/design_page.py:313` 的 `_on_image_pasted()` 只记日志。
   - 它没有把 `base64_png` 送给任何 AI、任何 patch planner、任何 revise 流程。
   - 所以“支持图片反馈修图”当前是假的。

2. **视觉审稿闭环当前被主流程直接禁用**
   - `schemaforge/system/session.py:237` 附近把 visual review 整段注释掉了。
   - 所以“系统会自动视觉审稿并迭代改善布局”当前不是主链路能力。

3. **就算启用 visual review，它现在也是假闭环**
   - `schemaforge/visual_review/loop.py:85` 从 `bundle.render_metadata` 构造 `layout_state`。
   - `schemaforge/visual_review/loop.py:145` 应用 patch 修改 `layout_state`。
   - 但 `schemaforge/visual_review/loop.py:149` 重新渲染时调用的是 `render_system_svg(ir)`。
   - `render_system_svg()` 没有接收也没有消费 `layout_state`。
   - 这意味着 patch 改的是一个 renderer 根本不看的对象。
   - 所以“视觉 patch 能影响最终 SVG”当前是假的。

4. **系统渲染器不是稳健布局引擎，而是启发式硬编码摆放**
   - `schemaforge/system/rendering.py:41` 使用 `_MODULE_X_SPACING`。
   - `schemaforge/system/rendering.py:43` 使用 `_CONTROL_Y_OFFSET`。
   - `schemaforge/system/rendering.py:573`、`schemaforge/system/rendering.py:666` 明显是固定间距 / 固定偏移布局。
   - 这类做法适合 demo，不适合系统级多模块可读布局。

5. **旧架构没有真正退出**
   - `main.py:217` 仍在实例化 `SchemaForgeSession`。
   - `schemaforge/workflows/schemaforge_session.py:165`、`schemaforge/workflows/schemaforge_session.py:441` 旧单器件工作台仍然活着。
   - 这意味着仓库里同时并存“旧单器件路径”和“新系统路径”。

6. **文档与现实明显不同步**
   - `README.md` 仍然带有明显的宣传式叙述，例如“AI-Only 架构（无 Mock/Demo）”。
   - 但 `tests/conftest.py:82` 明确写着“全局 mock AI 调用”。
   - 这说明仓库文档已经不能被当成可靠现状说明。

7. **`render_metadata` 目前更像愿景接口，不像真实生产产物**
   - `schemaforge/system/models.py:275` 的 `SystemBundle` 声明了 `render_metadata`。
   - 但我本轮没有在系统生产路径里找到对它的真实填充逻辑。
   - 视觉 review、截图裁切、评分系统大量依赖它，但生产渲染侧没有对应落地证据。
   - 这高度可疑，说明相关测试可能主要验证的是手工构造 metadata 的逻辑，而不是主链路结果。

---

## 三、目前项目的真实定位

一句难听但准确的话：

> SchemaForge 现在不是一个“已经成型的 AI 自动电路设计系统”，而是一套**单器件能力部分可用、系统级能力半成品、视觉闭环和图片反馈带有明显架构表演成分的原型仓库**。

更直白一点：

- 它不是纯假的。
- 但它也绝对没有达到“用户说一句话就自动出完整系统图”的成熟度。
- 目前最像真的，是：**器件库 + 单器件图 + 一部分系统 IR / 解析 / 连接 / 导出骨架**。
- 目前最像假的，是：**多模块高质量布局、视觉审稿闭环、图片修图闭环、文档里暗示的完整 AI 工作链**。

---

## 四、这个项目目前最严重的问题

## 4.1 最大问题不是模型，而是架构断裂

现在项目的根病不是“AI 不够聪明”，而是：

1. **没有一个单一可信的主入口**
2. **没有一个单一可信的系统 IR -> 渲染 IR -> SVG 的链路**
3. **没有一个 renderer 真正消费的布局状态对象**
4. **没有一个 GUI 文本 / GUI 图片 / CLI 都共用的统一 revise 管道**

只要这四个问题不解决，继续堆功能只会继续堆出新的假闭环。

## 4.2 项目存在明显“架构剧场”

下面这些模块/能力，当前更像“架构剧场”而不是真实闭环：

- `visual_review/*`
- `render_metadata`
- GUI 图片粘贴修图
- README 中暗示的完整 AI 自动链

这些东西的共同问题是：

- 看起来有结构
- 看起来有测试
- 看起来有对象
- 但没有接到用户真正能感受到的闭环里

## 4.3 测试数量很多，但测试质量与真实用户路径脱节

从代码结构上看，当前测试至少存在三个问题：

1. **默认全局 mock AI**
   - `tests/conftest.py:82` 说明大多数测试不走真实 AI。

2. **旧路径与新路径都在被测**
   - 仓库里同时有 `test_schemaforge_session.py`、`test_system_session.py`、`test_design_session.py` 等多套会话测试。
   - 这会让测试看起来很多，但并不说明主链路是干净的。

3. **visual review 相关测试的现实意义存疑**
   - 当主流程里 visual review 都被注释掉时，这些测试更多证明的是“该子系统的局部函数能跑”，不是“系统真的靠它改善了图”。

换句话说：

> 现在的测试规模并不能证明项目成熟，只能证明项目里有很多可单独调用的函数。

## 4.4 文档已经带有误导性

现有文档至少有两个明显问题：

1. **叙事过于乐观**
   - README 强调完整能力、AI-Only、无 Mock/Demo，但代码和测试结构不支持这种说法。

2. **架构冻结文档和现实已不完全同步**
   - `docs/architecture-freeze.md`、`docs/system-target.md` 等文件描述的是方向和设计意图，不是当前成品状态。

如果把这些文档直接交给下一个 AI，它很容易被带偏，以为很多能力已经接好了。

---

## 五、当前哪些东西可以保留，哪些应该冻结，哪些应该直接下重手

## 5.1 可以保留并继续投资的部分

下面这些模块至少具备继续打磨的价值：

- `schemaforge/system/models.py`
- `schemaforge/system/resolver.py`
- `schemaforge/system/connection_rules.py`
- `schemaforge/system/synthesis.py`
- `schemaforge/system/instances.py`
- `schemaforge/system/export_bom.py`
- `schemaforge/system/export_spice.py`
- `schemaforge/system/session.py`
- `schemaforge/store/devices/*.json`

原因很简单：

- 这些模块描述的是系统级的真实问题
- 它们至少方向上贴近最终目标
- 它们有机会成为单一主链路的骨架

## 5.2 应冻结为兼容层，不应继续扩张的部分

- `schemaforge/workflows/schemaforge_session.py`
- `main.py` 中围绕 `SchemaForgeSession` 的旧 CLI 路径
- 旧单器件工作流及其配套测试，若不服务系统级主链路，应逐步降级为兼容层

如果继续在旧路径上补功能，结果只会是：

- GUI 走一套
- CLI 走一套
- 测试测三套
- 用户以为是一套

这会让仓库越来越难救。

## 5.3 需要重写或至少大改的部分

1. **`schemaforge/system/rendering.py`**
   - 当前最大工程瓶颈之一。
   - 它需要从“单个巨大 Drawing + 启发式偏移”变成“模块渲染 + 系统布局 + 连接绘制”的明确分层。

2. **`schemaforge/visual_review/*`**
   - 只有在 renderer 真正消费 `LayoutSpec` 或等价布局状态后，这套东西才有意义。
   - 在那之前，继续修补它只是在修假闭环。

3. **GUI 图片反馈流程**
   - 现在必须二选一：
   - 要么明确标注“未实现”
   - 要么真的把它接进统一 revise 管线

4. **测试结构**
   - 需要从“模块存在性 + mock 成功”转向“高价值集成测试 + 端到端金路径测试”。

---

## 六、下一个 AI 接手时必须知道的非谈判事实

### 6.1 不能再把这些能力当作“已完成”

下一个 AI 必须停止把以下能力称为完成：

- 多模块高质量系统渲染
- 视觉审稿闭环
- 图片反馈闭环
- 文本和图片统一 revise
- 文档中描述的完整 AI 自动链路

### 6.2 不能再扩大旧路径

下一个 AI 不应该继续给 `SchemaForgeSession` 旧路径叠功能。

因为真正的最终目标是系统级设计，不是继续把单器件工作台打磨得更花。

### 6.3 不能继续用“测试很多”来证明成熟

这个仓库最需要的不是再加 300 个测试，而是增加下面这种测试：

> 输入一条真实系统级需求，走真实主路径，得到一张肉眼可读的系统图，并且 BOM/SPICE/连接关系都正确。

只要这种测试没有，项目就没有资格宣称“系统级设计已完成”。

---

## 七、建议下一个 AI 的接管顺序

## 阶段 1：承认现实，先收口，不要再扩张

第一步不是做新功能，而是：

1. 标记或冻结旧路径
2. 在文档中明确哪些能力是假闭环
3. 明确主入口：`SystemDesignSession`
4. 明确主渲染入口：`render_system_svg()` 或其重构后的替代

## 阶段 2：做一个最小真实金路径

只支持这一条：

> `Buck -> LDO -> MCU 供电 -> GPIO 控制 LED`

先把它做成真的，再谈通用化。

最小验收标准：

- 输入长句后，能在一张 SVG 上看到 4 个模块
- 模块之间连接正确
- BOM 全局编号不冲突
- SPICE 网络名共享正确
- GUI 与 CLI 走的是同一条会话链路

## 阶段 3：把布局状态变成 renderer 的真实输入

没有这一步，视觉审稿永远是假闭环。

建议的接口形态应该类似：

- `render_system_svg(ir, layout_spec) -> RenderResult`
- `plan_visual_patches(review_report, render_metadata) -> PatchPlan`
- `apply_patch_plan(layout_spec, patch_plan) -> layout_spec`

## 阶段 4：统一 revise

统一处理以下来源：

- 文本修订
- 图片修订
- 器件补录后继续设计

它们都应落到同一个系统会话对象上，而不是各走各的分叉。

---

## 八、下一个 AI 不应该再做的蠢事

1. **不要再堆新 dataclass 证明“架构更完整了”**
2. **不要再写新 patch executor 而 renderer 还是不读**
3. **不要再给 GUI 加按钮/状态标签来制造“已经支持”的错觉**
4. **不要再写只验证 mock 调用次数的测试冒充集成测试**
5. **不要再在 README 里写超过代码现实的能力宣称**
6. **不要继续维护两套会话体系还假装它们等价**

说得更狠一点：

> 这个项目现在最不需要的，就是新的“看起来更高级”的层。它最需要的是删掉幻觉，打通一条真链路。

---

## 九、建议立即补上的验收标准

下一个 AI 的任何阶段性汇报，都应该强制回答这 7 个问题：

1. 这次改动影响了哪条真实用户路径？
2. 哪些输出是本轮真实生成并检查过的？
3. 哪些功能仍然是假的、半假的、或未接通的？
4. 当前 GUI 和 CLI 是否使用同一条后端路径？
5. 当前 renderer 是否真的消费了本轮引入的状态对象？
6. 当前是否存在“代码存在但主链路不读”的假闭环？
7. 如果任务还没达到最终目标，为什么现在不能停？

---

## 十、我对这个仓库的最终评价

这是一个**有一些真东西，但被大量过早抽象、过早扩张、测试幻觉、文档乐观叙事和局部假闭环拖累的项目**。

它不是废墟，但也离“可称为成熟系统”差得很远。

当前最准确的判断是：

- **单器件能力：部分可信**
- **系统级 IR / 连接 / 导出骨架：有价值，但未闭环**
- **多模块可读布局：未解决**
- **视觉审稿闭环：当前无效**
- **图片反馈：当前未接通**
- **文档可信度：有限**
- **测试数量可信度：有限**

如果下一个 AI 再继续在这个基础上“写更多模块、更多测试、更多说明文档”，而不先收口主链路，那么项目只会继续变成更贵、更复杂、更会自我宣传的屎山。

如果它愿意下狠手：

- 冻结旧路径
- 承认假闭环
- 把系统级金路径做真
- 让布局状态真正驱动渲染
- 让 GUI/CLI/revise 真正统一

那这个项目仍然是可以救的。

---

## 附：本轮关键证据索引

- 新系统会话：`schemaforge/system/session.py:75`
- 新系统 `start()`：`schemaforge/system/session.py:104`
- 新系统 `revise()`：`schemaforge/system/session.py:282`
- visual review 在主流程被注释：`schemaforge/system/session.py:237`
- 视觉 loop 构造 layout state：`schemaforge/visual_review/loop.py:85`
- 视觉 loop 应用 patch：`schemaforge/visual_review/loop.py:145`
- 视觉 loop 重渲染未传 layout：`schemaforge/visual_review/loop.py:149`
- 系统渲染器入口：`schemaforge/system/rendering.py:841`
- 系统渲染器固定水平间距：`schemaforge/system/rendering.py:41`
- 系统渲染器固定控制支路偏移：`schemaforge/system/rendering.py:43`
- GUI 图片粘贴入口：`schemaforge/gui/pages/design_page.py:277`
- GUI 图片粘贴仅日志：`schemaforge/gui/pages/design_page.py:313`
- GUI 生成 worker：`schemaforge/gui/pages/design_page.py:376`
- GUI 保存 session：`schemaforge/gui/pages/design_page.py:380`
- GUI revise worker：`schemaforge/gui/pages/design_page.py:340`
- GUI worker 实例化 `SystemDesignSession`：`schemaforge/gui/workers/engine_worker.py:74`
- 旧单器件工作台：`schemaforge/workflows/schemaforge_session.py:165`
- 旧单器件 revise：`schemaforge/workflows/schemaforge_session.py:441`
- CLI 仍走旧路径：`main.py:217`
- 环境变量跳过 AI 解析：`schemaforge/system/ai_protocol.py:487`
- 旧设计层也读同名环境变量：`schemaforge/design/synthesis.py:41`
- 系统 bundle 声明 render metadata：`schemaforge/system/models.py:275`
- 测试默认 mock AI：`tests/conftest.py:82`
- 测试默认设置 `SCHEMAFORGE_SKIP_AI_PARSE`：`tests/conftest.py:68`

