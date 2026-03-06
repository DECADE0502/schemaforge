# SchemaForge — AI 交互记录

> 记录关键轮次的AI协作过程：输入→输出→决策→后续动作

---

## Round 0 — 项目启动与需求澄清

### 输入（Prompt/指令）
用户提供了完整的工程Prompt（PROMPT.md），要求从零实现SchemaForge项目。同时附加了严格的日志规范要求（devlog.md、ai_interaction.md、prompts/版本化等）。

### 输出摘要
- 发现PROMPT.md描述的是SchemaForge（原理图生成器），而消息正文描述的是迭代框架，两者不同
- 向用户确认：应实现SchemaForge
- 用户确认后，创建了项目骨架

### 决策理由
1. **Web→CLI转换**: 用户明确要求"不要web"，将Gradio界面替换为rich TUI CLI
2. **日志规范适配**: 消息中的日志规范（devlog、ai_interaction、prompts/）完全适用于SchemaForge开发
3. **分阶段实施**: 按PROMPT.md的Phase 1-5规划，结合日志要求

### 后续动作
进入Phase 1：实现核心数据模型 + 分压器模板 + schemdraw渲染

---

## Round 1 — 核心模型与渲染设计

### 输入（Prompt/指令）
"一直迭代，自己review自己检查等等"（自动迭代模式开始）

### 输出摘要
- 完成全部 Pydantic 数据模型（12个类/枚举）
- 分压器渲染函数 + 工具函数（format_value, find_nearest_e24, output_path）
- 10个模型单元测试全PASS

### 决策理由
1. **模型设计**: 采用 ComponentDef（模板定义层）+ ComponentInstance（实例层）分离，模板只定义结构，实例持有具体参数值
2. **E24匹配**: 在 render/base.py 中实现而非 calculator.py，因为渲染时需要格式化值
3. **Net模板化**: Net包含 NetConnection 列表作为模板，实例化时 model_copy() 复制

### 后续动作
实现全部4个模板 + 计算器

---

## Round 2 — 模板系统与计算引擎

### 输入（Prompt/指令）
自动迭代：Phase 2 模板系统

### 输出摘要
- 4个电路模板（voltage_divider, ldo_regulator, led_indicator, rc_lowpass）
- 参数计算器：分压计算 + LED限流电阻 + RC滤波器
- 4个独立SVG渲染函数
- 16个计算器测试全PASS

### 决策理由
1. **AMS1117渲染**: 使用 schemdraw 的 elm.Ic（集成电路通用元件）而非自定义图形，输入/输出/地引脚位置明确
2. **模板注册表**: 用全局字典 TEMPLATE_REGISTRY 注册模板，get_template() 查询，简洁直接
3. **LED颜色**: 支持 red/green/blue/white，正向压降查表（2.0~3.0V），限流电阻自动匹配E24

### 后续动作
AI集成层

---

## Round 3 — AI集成与引擎串联

### 输入（Prompt/指令）
自动迭代：Phase 3 AI集成。用户额外要求：API key硬编码 sk-sp-396701e02c95411783e01557524e4366，base URL https://coding.dashscope.aliyuncs.com/v1，模型 kimi-k2.5

### 输出摘要
- LLM客户端（OpenAI兼容接口，硬编码配置）
- Mock/Online双模式
- JSON提取（regex从LLM输出中提取JSON块）
- 设计规格验证器（模板存在性、参数类型/范围、连接有效性、重复检测）
- 6阶段核心引擎流水线

### 决策理由
1. **硬编码API**: 用户明确要求"AI配置默认写死用kimi-k2.5，不需要手动配置"
2. **Mock优先**: DEMO_RESPONSES 按关键词匹配，支持离线完整演示
3. **JSON提取策略**: LLM可能返回markdown包裹的JSON（```json ... ```），用regex提取纯JSON部分
4. **验证层分离**: validator.py 独立于 engine.py，可单独测试

### 后续动作
ERC + 导出

---

## Round 4 — ERC检查与导出

### 输入（Prompt/指令）
自动迭代：Phase 4 ERC + BOM + SPICE

### 输出摘要
- ERCChecker 6条规则全实现
- BOM Markdown表格（含LCSC编号映射）
- SPICE网表（按模板类型分别生成）
- 17个测试全PASS

### 决策理由
1. **ERC不阻断**: ERC错误记为warning而非fatal error，因为模板保证的连接是正确的，ERC主要捕获参数问题
2. **LCSC映射**: 硬编码常用器件的LCSC编号（AMS1117=C347222, 0805电容=C14663等），实际项目应从数据库查询
3. **SPICE按模板**: 每种模板有固定拓扑，不需要通用SPICE生成器

### 后续动作
CLI + 组合模板

---

## Round 5 — CLI界面与系统集成

### 输入（Prompt/指令）
自动迭代：Phase 5 CLI + 组合模板 + 端到端

### 输出摘要
- rich TUI 中文CLI（--demo/--online/--input/--templates 四种模式）
- LDO+LED组合渲染
- Windows GBK编码兼容修复
- 58个测试全PASS

### 决策理由
1. **CLI而非Web**: 用户明确要求"最好不要web"，使用rich做TUI
2. **编码修复**: Windows默认GBK编码，emoji字符（✅❌等）导致UnicodeEncodeError，改用ASCII替代（[PASS]/[FAIL]）+ force_terminal=True + UTF-8 stdout wrapper
3. **组合渲染**: composite.py 分别调用 ldo 和 led 渲染函数而非创建新的合成图，因为schemdraw不支持多Drawing合并

### 后续动作
文档补全、SPICE修复、质量检查
