# SchemaForge 痛点导向执行计划

> 日期：2026-03-06  
> 适用范围：基于当前 `schemaforge/` 仓库现状的后续演进计划  
> 目标：围绕“AI 辅助原理图设计”最核心的落地难点，给出可执行、可验收、可分阶段推进的详细方案

---

## 1. 文档目的

这份文档不是泛泛的产品愿景，而是针对当前代码基础上的**痛点拆解 + 架构演进 + 分阶段实施计划**。

它要解决三个问题：

1. 当前项目真正难的地方是什么
2. 现有代码已经做到哪里、瓶颈卡在哪里
3. 下一阶段应该按什么顺序推进，才能尽量少返工、稳定提升成功率

---

## 2. 当前项目现状判断

结合现有实现，SchemaForge 已经不是“从零开始”的概念项目，而是具备明确主干的工程原型。

### 2.1 已具备的关键基础

- 约束驱动主流程已经建立：`schemaforge/core/engine.py`
- 器件库存储与索引已经建立：`schemaforge/library/store.py`
- 器件库服务层已经建立：`schemaforge/library/service.py`
- 设计规划、检索、合理性检查、拓扑适配已经拆出：
  - `schemaforge/design/planner.py`
  - `schemaforge/design/retrieval.py`
  - `schemaforge/design/rationality.py`
  - `schemaforge/design/topology_adapter.py`
- 多轮工作流与状态管理已经具备雏形：
  - `schemaforge/workflows/design_session.py`
  - `schemaforge/workflows/state_machine.py`
- Agent 协议与工具调用框架已搭好：
  - `schemaforge/agent/protocol.py`
  - `schemaforge/agent/tool_registry.py`
  - `schemaforge/agent/orchestrator.py`
- GUI 和测试体系已具备较强的原型验证能力：
  - `gui.py`
  - `schemaforge/gui/`
  - `tests/`

### 2.2 当前路线的核心优点

- AI 没有直接掌控持久化与渲染，而是经过本地模型和约束层
- “器件库驱动设计”方向正确，比纯 prompt 生成原理图更稳
- 已经开始把系统拆成 planner / retrieval / rationality / adapter 这些关键中间层
- 有较完整测试，说明当前代码适合继续演进，不必推倒重来

### 2.3 当前真正的短板

当前系统最大问题，不是“不会生成图”，而是**还没有把需求理解、方案选择、参数闭环、局部修改这几条链真正闭合**。

换句话说，当前项目已经能“生成一些正确案例”，但距离“稳定做设计助手”还差以下几个关键层。

---

## 3. 核心痛点定义

以下痛点按重要性排序，而不是按实现难度排序。

### P1. 需求语义不完整，系统缺少强制澄清机制

#### 表现

- 用户输入常常只描述功能，不描述设计约束
- 例如“做个 12V 转 5V 电源”通常缺少：
  - 输入波动范围
  - 最大负载电流
  - 纹波要求
  - 效率偏好
  - 成本偏好
  - 封装限制
  - 温升容忍度

#### 当前代码对应位置

- `schemaforge/design/planner.py`
- `schemaforge/workflows/design_session.py`
- `schemaforge/agent/orchestrator.py`

#### 本质问题

当前系统已经能做规划，但“缺失约束识别”和“追问闭环”还不是第一等机制。

如果缺失约束没有被显式建模，后面的检索、计算、拓扑和渲染就只能依赖默认假设，成功率会随着任务复杂度迅速下降。

---

### P2. 从模块规划到真实可用拓扑之间还有一层缺口

#### 表现

- 功能模块规划并不等于完整电路
- 真实电路通常包含：
  - 必需外围件
  - 默认保护网络
  - 去耦网络
  - 测试点
  - 使能/上拉/下拉网络
  - 稳定性相关器件
- 这些内容如果只由 AI 临时决定，极易遗漏

#### 当前代码对应位置

- `schemaforge/design/topology_adapter.py`
- `schemaforge/schematic/topology.py`
- `schemaforge/core/templates.py`
- `schemaforge/render/`

#### 本质问题

当前更接近“模板渲染 + 多模块拼接”，但复杂任务真正需要的是：

- 拓扑骨架
- 必需外围规则
- 角色化外设注入
- 连接约束

也就是系统需要从“模板库”走向“拓扑规则库”。

---

### P3. 参数计算仍偏单点计算，缺少多约束候选解机制

#### 表现

- 简单模块可以直接公式计算
- 一旦进入真实设计，参数不再是唯一答案
- 同一个设计目标，往往存在：
  - 低成本方案
  - 低功耗方案
  - 高鲁棒方案
  - 库存优先方案

#### 当前代码对应位置

- `schemaforge/core/calculator.py`
- `schemaforge/design/rationality.py`
- `schemaforge/design/retrieval.py`

#### 本质问题

目前系统更偏“算出一个值”，而不是“生成候选设计并排序”。

这会导致：

- 可解释性弱
- 修改难度大
- 很难支持用户说“换一个更省电/更便宜/更稳的方案”

---

### P4. 器件库仍以描述数据为主，缺少设计知识层

#### 表现

- 当前器件模型已能支持 symbol / pin / package / 参数
- 但真正用于自动设计，还需要更高层信息：
  - 这个器件适合扮演什么角色
  - 有什么禁用场景
  - 必需外围件是什么
  - 布局注意事项是什么
  - 容易犯错的地方是什么

#### 当前代码对应位置

- `schemaforge/library/models.py`
- `schemaforge/library/validator.py`
- `schemaforge/library/store.py`
- `schemaforge/store/devices/`

#### 本质问题

现在的器件库更像“元数据仓库”，而未来要支撑 AI 辅助设计，必须升级成“设计知识库”。

---

### P5. 校验层偏基础 ERC，缺少工程审查层

#### 表现

- ERC 可以发现基础连线问题
- 但很多真实风险并不属于 ERC：
  - LDO 压差不够
  - LED 电阻功耗不足
  - 运放输入共模越界
  - 反馈网络参数不合理
  - 电源输出电容类型不满足稳定性要求
  - 去耦电容值或位置建议缺失

#### 当前代码对应位置

- `schemaforge/core/erc.py`
- `schemaforge/design/rationality.py`
- `schemaforge/core/engine.py`

#### 本质问题

项目现在已有“合理性检查”雏形，但还没有形成独立的“设计审查层”。

真正面向实用场景时，系统不能只回答“连得对不对”，还要回答“这样设计稳不稳、常不常见、风险在哪”。

---

### P6. 多轮修改能力会成为未来最大瓶颈

#### 表现

- 首次生成通常还能接受重跑式生成
- 但真实用户更常见的行为是局部修改：
  - 改输入电压
  - 改输出电流
  - 换芯片
  - 去掉某个模块
  - 增加采样点
  - 改 LED 颜色
- 如果每次都全量重做，设计状态很容易漂移

#### 当前代码对应位置

- `schemaforge/workflows/design_session.py`
- `schemaforge/common/session_store.py`
- `schemaforge/agent/orchestrator.py`

#### 本质问题

项目后续必须引入**可 patch 的设计中间表示**，否则多轮迭代会越来越不稳定。

---

## 4. 总体演进原则

后续所有开发必须坚持以下原则，否则系统复杂度会快速失控。

### G1. AI 负责理解与决策，不直接负责最终结构落地

AI 输出应偏向：

- 需求解析
- 缺失信息识别
- 方案候选
- 角色选择
- 解释文本

不应直接成为：

- 原理图连接真值
- 器件持久化真值
- 最终参数真值

### G2. 本地规则必须成为主裁决层

最终真值应由以下层决定：

- 设计 IR
- 检索排序器
- 参数求解器
- 拓扑规则
- 设计审查器
- 渲染器

### G3. 先做中间表示，再做复杂能力

没有稳定 IR，以下功能都无法长久稳定：

- 多轮修改
- 差异对比
- 局部重算
- 可解释输出
- 设计回放

### G4. 先提升成功率，再扩展题材范围

建议先把以下题材做到稳定：

- LDO 电源
- LED 指示
- 分压采样
- RC 滤波
- 简单多模块拼接

再逐步扩展到：

- 运放前端
- USB-C 供电前端
- Buck 电源
- MCU 最小系统

---

## 5. 目标架构演进

建议把现有系统逐步演进成以下主链路：

`用户需求 -> 需求澄清 -> 设计意图 IR -> 候选方案生成 -> 器件/拓扑选择 -> 参数求解 -> 设计审查 -> 渲染/导出 -> 局部修改`

### 5.1 推荐新增的核心层

#### A. Requirement Clarifier

职责：

- 检测缺失约束
- 区分必须澄清和可默认假设项
- 形成结构化 assumptions
- 输出风险等级

建议位置：

- 新增 `schemaforge/design/clarifier.py`

#### B. Design IR

职责：

- 作为整个系统唯一的中间真值
- 承接规划、选择、参数、拓扑、审查结果
- 支持 patch / diff / 回放

建议位置：

- 新增 `schemaforge/design/ir.py`

#### C. Candidate Solver

职责：

- 不只给单解，而是生成多组候选
- 带 tradeoff 与评分

建议位置：

- 新增 `schemaforge/design/candidate_solver.py`

#### D. Design Review Engine

职责：

- 补足 ERC 之外的工程审查规则
- 输出 blocking / warning / recommendation / layout_note

建议位置：

- 新增 `schemaforge/design/review.py`

#### E. Patch Engine

职责：

- 处理局部修改请求
- 识别受影响模块
- 只重算必要部分

建议位置：

- 新增 `schemaforge/workflows/patch_engine.py`

---

## 6. 设计 IR 详细建议

这是整个后续路线中最优先、最关键的基础设施。

### 6.1 设计目标

IR 必须满足：

- 人类可读
- 程序可校验
- 可持久化
- 可差异比较
- 可局部修改
- 可回放生成过程

### 6.2 推荐结构

建议至少包含以下对象：

#### DesignIntent

- 原始用户需求
- 规范化需求摘要
- 必需约束
- 可选偏好
- assumptions
- unresolved_questions
- confidence

#### ModuleIntent

- 角色，如 `main_regulator`、`status_led`、`adc_divider`
- 功能类别
- 输入/输出目标
- 关键参数约束
- 依赖模块

#### DeviceSelection

- 候选器件列表
- 当前选中器件
- 打分明细
- 选择理由
- 替代项

#### TopologyDraft

- 模块间连接
- 必需外围件
- 自动注入附件
- 网络命名
- 连接来源说明

#### DerivedParameters

- 计算结果
- 使用公式
- 假设条件
- 单位与来源

#### ReviewReport

- blocking
- warning
- recommendation
- layout_notes
- bringup_notes

#### PatchHistory

- 每次用户修改的 patch 操作
- 影响范围
- 回滚依据

### 6.3 落地方式

建议用 Pydantic 模型实现，先不追求极度复杂，先保证稳定。

建议新增：

- `schemaforge/design/ir.py`
- `tests/test_design_ir.py`

---

## 7. 器件库升级计划

### 7.1 当前问题

当前器件库可支撑“查找到器件”，但未必足以支撑“自动做设计决策”。

### 7.2 推荐为器件增加的字段

建议在 `schemaforge/library/models.py` 中逐步增加以下能力字段：

- `design_roles`: 可扮演角色列表
- `selection_hints`: 适用场景描述
- `anti_patterns`: 不适用场景
- `required_companions`: 必需外围件模板
- `operating_constraints`: 关键工作约束
- `layout_hints`: 布局注意事项
- `failure_modes`: 常见误用模式
- `review_rules`: 针对该器件的审查规则引用

### 7.3 数据来源策略

不建议一开始完全依赖 AI 自动抽取这些高层字段。

建议采用三层来源：

1. 人工维护的高价值器件模板
2. EasyEDA / datasheet 抽取的原始数据
3. AI 生成的建议字段，进入人工确认或低信任态存储

### 7.4 第一批推荐升级器件

- `AMS1117-3.3`
- `VOLTAGE_DIVIDER`
- `RC_LOWPASS`
- `LED_INDICATOR`

理由：这些器件或模板已经在现有样例中使用，改造成本低、收益高。

---

## 8. 需求澄清机制计划

### 8.1 目标

把“AI 自动脑补”改造成“系统化澄清缺失条件”。

### 8.2 输出结构建议

`clarifier` 应输出：

- `known_constraints`
- `missing_required_constraints`
- `optional_preferences`
- `assumptions`
- `confidence`
- `must_ask_before_continue`

### 8.3 继续执行策略

建议引入明确策略：

- 若存在 `missing_required_constraints`，禁止进入最终设计生成
- 若只有 `optional_preferences` 未给出，可按默认策略继续
- 所有默认值都必须写入 `assumptions`

### 8.4 第一批必须澄清的设计类型字段

#### 电源类

- 输入电压范围
- 输出电压
- 最大负载电流
- 线性稳压/开关稳压偏好
- 是否关心效率

#### 采样类

- 被测输入范围
- ADC 满量程
- 采样阻抗偏好
- 精度需求

#### 指示灯类

- 供电电压
- LED 颜色/正向压降
- 目标电流或亮度偏好

---

## 9. 候选方案与评分机制计划

### 9.1 目标

从“生成一个答案”升级为“生成多个候选，并给出排序依据”。

### 9.2 候选方案至少应包含

- 方案名称
- 选用器件
- 关键参数
- 估计功耗/成本/复杂度
- 风险摘要
- 适用场景
- 综合评分

### 9.3 评分维度建议

- 约束满足度
- 器件匹配度
- 库存可得性
- 电气合理性
- BOM 复杂度
- 预计热风险
- 用户偏好匹配度

### 9.4 最小落地范围

第一阶段只对以下场景启用候选解：

- LDO 方案选择
- 电阻取值方案选择
- LED 电流方案选择

---

## 10. 设计审查层计划

### 10.1 目标

把 `rationality` 从“参数合理性检查”升级为“工程审查引擎”。

### 10.2 审查输出建议

- `blocking_issues`
- `warnings`
- `recommendations`
- `layout_notes`
- `bringup_checks`

### 10.3 第一批高价值规则

#### 电源类

- LDO 压差是否满足
- 估算功耗与温升风险
- 输入输出电容是否齐全
- 输出电容值是否满足稳定性经验要求

#### LED 类

- 限流电阻功耗是否足够
- LED 电流是否超出常规指示范围

#### 分压类

- 分压电流是否过大
- 下一级输入阻抗影响是否可能显著

#### 通用类

- 上电默认状态是否明确
- 悬空控制引脚是否存在
- 去耦是否缺失

### 10.4 代码落点

- 扩展 `schemaforge/design/rationality.py`
- 或新增 `schemaforge/design/review.py`

建议采用“新增 review 层”的方式，避免 `rationality.py` 承担过多职责。

---

## 11. 多轮修改与 Patch 机制计划

### 11.1 目标

把“重新生成”升级为“在稳定设计状态上做局部修改”。

### 11.2 推荐 PatchOp 类型

- `update_constraint`
- `replace_device`
- `add_module`
- `remove_module`
- `update_parameter`
- `rename_net`
- `change_preference`

### 11.3 Patch 流程

1. 用户提出修改请求
2. AI/规则层将请求解析为 PatchOp
3. Patch Engine 识别影响范围
4. 对受影响模块执行局部重检索/重计算/重审查
5. 更新 IR
6. 重新导出相关输出

### 11.4 第一阶段不做的事

为了控制复杂度，第一阶段 Patch 不处理：

- 任意网络手工重布线
- 跨多个复杂子系统的自动重拓扑
- 大范围自动布局优化

第一阶段只做“参数级修改 + 模块级替换”。

---

## 12. 参考设计检索计划

### 12.1 目标

把系统从“纯器件驱动”升级为“器件 + 参考拓扑双驱动”。

### 12.2 原因

原理图设计的稳定性，很大程度不来自自由生成，而来自复用成熟参考设计骨架。

### 12.3 推荐新增的库

- `schemaforge/store/reference_designs/`

每个参考设计包含：

- 适用场景
- 关键约束
- 拓扑骨架
- 必需外围件
- 可替换器件角色
- 经验注释

### 12.4 第一批参考设计建议

- 线性稳压输出
- LED 电源指示
- ADC 分压采样
- RC 输入滤波
- LDO + LED 组合前端

---

## 13. 分阶段执行计划

以下阶段以“低返工、可持续集成”为原则排列。

### Phase A：设计 IR 落地

#### 目标

建立未来所有能力共享的中间真值层。

#### 任务

- 新增 `schemaforge/design/ir.py`
- 定义核心 Pydantic 模型
- 在 `design_session` 中引入 IR 持久化
- 在 `session_store` 中保存 IR 快照
- 增加 `tests/test_design_ir.py`

#### 验收

- 单个设计流程能输出完整 IR
- IR 可序列化/反序列化
- IR 可保存到 session

#### 优先级

最高

---

### Phase B：需求澄清层落地

#### 目标

建立必须约束的强制补全机制。

#### 任务

- 新增 `schemaforge/design/clarifier.py`
- planner 先调用 clarifier 再生成模块计划
- Agent/GUI 支持问答式补全约束
- 把 assumptions 写入 IR
- 增加 `tests/test_design_clarifier.py`

#### 验收

- 对缺少关键参数的需求，系统能阻止直接出图
- 对补全后的需求，系统能恢复主流程

#### 优先级

最高

---

### Phase C：器件库升级为设计知识库

#### 目标

让器件模型能够支撑角色化选择和审查。

#### 任务

- 扩展 `schemaforge/library/models.py`
- 更新 `validator.py` / `store.py` / `service.py`
- 为现有 4 个示例器件补充高层字段
- 增加相关迁移脚本与测试

#### 验收

- 检索时可按 design role 过滤
- 设计审查可读取器件级规则元数据

#### 优先级

高

---

### Phase D：候选方案与评分机制

#### 目标

把“单解系统”升级为“候选解系统”。

#### 任务

- 新增 `schemaforge/design/candidate_solver.py`
- retrieval 输出候选列表而不仅是最佳匹配
- calculator/rationality 接入评分维度
- GUI 支持显示候选方案摘要
- 增加 `tests/test_candidate_solver.py`

#### 验收

- 至少在 LDO 和 LED 场景中生成 2~3 个候选解
- 用户可查看每个候选的 tradeoff

#### 优先级

高

---

### Phase E：设计审查层

#### 目标

在 ERC 之外增加工程经验型校验。

#### 任务

- 新增 `schemaforge/design/review.py`
- 将 review 接入 `design_session` 和 `engine`
- 输出 recommendation / layout note / bring-up note
- 增加 `tests/test_design_review.py`

#### 验收

- 对明显高风险设计给出可解释告警
- review 结果能被 GUI/CLI 展示

#### 优先级

高

---

### Phase F：Patch 与多轮修改

#### 目标

支持在稳定设计状态上做增量修改。

#### 任务

- 新增 `schemaforge/workflows/patch_engine.py`
- 定义 PatchOp 模型
- 设计 session 记录 patch history
- 支持局部重算
- 增加 `tests/test_patch_engine.py`

#### 验收

- 支持参数更新、模块替换两类 patch
- patch 后只重算受影响模块

#### 优先级

中高

---

### Phase G：参考设计库接入

#### 目标

提升复杂场景下的拓扑稳定性。

#### 任务

- 新增 `schemaforge/store/reference_designs/`
- 新增参考设计模型与检索器
- topology adapter 优先从参考骨架出发构建
- 增加 `tests/test_reference_designs.py`

#### 验收

- 在 LDO + LED 组合设计中优先复用参考骨架
- 成功率与审查通过率明显高于纯模板拼接

#### 优先级

中

---

## 14. 各阶段建议工期

如果按照“保持现有代码连续可运行”的方式推进，建议节奏如下：

- Phase A：3~5 天
- Phase B：3~5 天
- Phase C：4~7 天
- Phase D：4~6 天
- Phase E：4~6 天
- Phase F：5~8 天
- Phase G：4~7 天

总计建议：`4~6 周` 做出一个明显更稳定的 vNext 原型。

---

## 15. 里程碑定义

### M1：可澄清的设计系统

达成条件：

- 缺失约束可识别
- 系统可追问
- assumptions 可记录

### M2：可解释的候选设计系统

达成条件：

- 同一需求可给出多个候选方案
- 每个候选有评分与理由

### M3：可审查的工程设计系统

达成条件：

- review 层可以输出工程告警
- 输出不再只有 schematic/BOM/SPICE

### M4：可修改的设计系统

达成条件：

- 参数修改与器件替换可局部重算
- patch history 可追溯

---

## 16. 建议暂缓的事项

为了避免分散精力，以下事项建议先不作为主线：

- 全自动复杂布局优化
- 大规模开放式任意拓扑生成
- 一次性支持太多器件品类
- 过早接入大型外部EDA生态导出
- 先做花哨 GUI，而不先补 IR/clarifier/review

这些不是不重要，而是排在稳定主链路之后。

---

## 17. 推荐的近期实施顺序

如果只做最关键、最能提升成功率的三步，推荐顺序如下：

1. 先做 `Design IR`
2. 再做 `Requirement Clarifier`
3. 然后做 `Design Review Engine`

原因：

- 没有 IR，多轮修改和解释能力无从谈起
- 没有澄清层，复杂需求成功率起不来
- 没有审查层，结果难以建立用户信任

---

## 18. 最终判断

SchemaForge 最难的部分，不是“让 AI 画出图”，而是让系统像一个谨慎的硬件工程师那样工作：

- 先识别信息缺失
- 再明确假设
- 再选方案
- 再补外围
- 再做参数闭环
- 再做工程审查
- 最后支持增量修改

当前仓库已经完成了最可贵的一步：**主干方向是对的，而且代码结构已经具备承载下一阶段演进的条件**。

后续不要把重点放在“让 AI 更自由地生成”，而要放在“让系统更稳地约束、审查和迭代设计”。

这会决定 SchemaForge 是一个演示项目，还是一个真正可持续升级的 AI 原理图设计助手。
