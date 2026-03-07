# SchemaForge 系统级改造 — 总执行清单

> GPT 审查后制定的完整执行计划。100 个任务，10 个阶段。
> 核心原则：AI 只做意图理解，所有工程决策通过本地确定性接口完成。

---

## 阶段 1：重建目标边界与系统骨架

- [x] T001 写 `docs/system-target.md`，明确"AI 只理解，本地全执行"
- [x] T002 写 `docs/system-ir.md`，明确 IR 分层
- [x] T003 冻结当前单器件路径为兼容层
- [x] T004 标记 `SchemaForgeSession` 当前单器件逻辑为 legacy path
- [x] T005 画出现状数据流图
- [x] T006 画出目标系统级数据流图
- [x] T007 定义系统设计生命周期状态
- [x] T008 列出当前不能继续扩展的函数
- [x] T009 列出必须拆分的"上帝类"
- [x] T010 把架构约束写入 `plan.md`

## 阶段 2：建立系统级模型

- [x] T011 新建 `schemaforge/system/models.py`
- [x] T012 定义 `SystemDesignRequest`
- [x] T013 定义 `ModuleIntent`
- [x] T014 定义 `ConnectionIntent`
- [x] T015 定义 `ModuleInstance`
- [x] T016 定义 `PortRef`
- [x] T017 定义 `ResolvedConnection`
- [x] T018 定义 `SystemNet`
- [x] T019 定义 `SystemDesignIR`
- [x] T020 定义 `SystemBundle`

## 阶段 3：重写 AI 输入输出协议

- [x] T021 重写需求解析 prompt
- [x] T022 让 AI 输出模块列表
- [x] T023 让 AI 输出连接意图
- [x] T024 让 AI 输出不确定项
- [x] T025 让 AI 输出特殊控制语义
- [x] T026 为 AI 输出写 Pydantic schema
- [x] T027 新建 `validate_ai_schema`
- [x] T028 新建 `normalize_ai_intents`
- [x] T029 新建 `detect_ambiguities`
- [x] T030 补 AI 输出协议测试

## 阶段 4：重做器件解析与实例化

- [x] T031 新建 `schemaforge/system/resolver.py`
- [x] T032 实现 `resolve_exact_part`
- [x] T033 实现 `resolve_alias_part`
- [x] T034 实现 `resolve_part_candidates`
- [x] T035 实现 `get_device_ports`
- [x] T036 实现 `get_power_ports`
- [x] T037 实现 `get_signal_ports`
- [x] T038 实现 `instantiate_module_from_device`
- [x] T039 实现 `validate_module_instance`
- [x] T040 补实例化测试

## 阶段 5：重做缺件导入链路

- [ ] T041 新建 `schemaforge/system/import_pipeline.py`
- [ ] T042 实现 `stage_device_import_from_pdf`
- [ ] T043 实现 `stage_device_import_from_image`
- [ ] T044 实现 `extract_pin_table`
- [ ] T045 实现 `extract_typical_application`
- [ ] T046 实现 `extract_operating_constraints`
- [ ] T047 实现 `build_symbol_preview`
- [ ] T048 实现 `validate_import_draft`
- [ ] T049 实现 `commit_import_device`
- [ ] T050 补导入安全回归测试

## 阶段 6：建立连接规则引擎

- [x] T051 新建 `schemaforge/system/connection_rules.py`
- [x] T052 定义规则数据格式
- [x] T053 实现电源链规则
- [x] T054 实现 GPIO→LED 规则
- [x] T055 实现 SPI→Flash 规则骨架
- [x] T056 实现 GND 全局归并
- [x] T057 实现 EN/BOOT/FB 特殊规则
- [x] T058 实现 unresolved 机制
- [x] T059 实现 `explain_connection_rule`
- [x] T060 补规则引擎测试

## 阶段 7：建立模块综合与依赖传播

- [x] T061 新建 `schemaforge/system/synthesis.py`
- [x] T062 接入 Buck 模块综合器
- [x] T063 接入 LDO 模块综合器
- [x] T064 新建 MCU 最小系统综合器
- [x] T065 新建 LED 指示支路综合器
- [x] T066 新建 generic placeholder 综合器
- [x] T067 实现 `propagate_supply_constraints`
- [x] T068 实现 `recompute_dependent_modules`
- [x] T069 实现参数 evidence 记录
- [x] T070 补多模块综合测试

## 阶段 8：重做全局实例、编号、BOM、SPICE

- [x] T071 新建 `schemaforge/system/instances.py`
- [x] T072 实现 `create_component_instances`
- [x] T073 实现 `allocate_global_references`
- [x] T074 实现编号稳定器
- [x] T075 新建 `schemaforge/system/export_bom.py`
- [x] T076 实现 Markdown BOM 导出
- [x] T077 实现 CSV BOM 导出
- [x] T078 新建 `schemaforge/system/export_spice.py`
- [x] T079 实现共享节点 SPICE 导出
- [x] T080 补 BOM/SPICE 回归测试

## 阶段 9：重做系统级渲染

- [x] T081 新建 `schemaforge/system/rendering.py`
- [x] T082 把模块 layout 改成"画到同一个 Drawing"
- [x] T083 实现主电源链布局器
- [x] T084 实现控制/外设支路布局器
- [x] T085 实现模块 anchor 返回
- [x] T086 实现跨模块连线
- [x] T087 实现 net label 绘制
- [x] T088 实现 GND 策略渲染
- [x] T089 输出单张系统 SVG
- [x] T090 补黄金 SVG 测试

## 阶段 10：Session、Revision、GUI 收口

- [x] T091 新建 `schemaforge/system/session.py`
- [x] T092 让 session 保存 `SystemDesignIR`
- [x] T093 实现 `start_system_design`
- [x] T094 实现 `revise_system_design`
- [x] T095 实现模块替换
- [x] T096 实现模块新增/删除
- [x] T097 GUI 增加模块树与 unresolved 面板
- [x] T098 GUI 增加系统级 BOM / SPICE / warnings 视图
- [x] T099 跑完整场景：`20V -> TPS54202 -> AMS1117 -> STM32 + PA1控LED`
- [x] T100 以这个完整场景作为里程碑验收并锁回归
