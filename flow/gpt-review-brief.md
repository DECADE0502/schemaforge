# SchemaForge 当前交接简报

更新时间：2026-03-07

## 1. 结论

- 当前**没有达到最终目标**。
- 项目已经有一条新的后端骨架链路，但仍处于“可演示部分流程、尚不能稳定兑现最终愿景”的阶段。
- 现阶段最关键的问题不是继续加功能，而是先修掉 4 个已经明确复现的高优先级阻塞点。

## 2. 最终愿景回顾

目标是把 SchemaForge 做成：

- 用户一句自然语言需求
- 系统精确识别器件型号
- 本地库命中则直接用
- 本地库缺失则引导上传 PDF / 图片并自动提取器件信息
- 根据 datasheet / 典型应用计算外围器件
- 输出完整原理图、SVG、BOM、SPICE
- 支持多轮修改，不推倒重来
- 不局限于 Buck / LDO，而是能从 datasheet 和参考设计推导结构

核心哲学：**AI 负责理解与决策，本地工具负责执行。**

## 3. 已完成内容

### 3.1 仓库与测试基线

- 已读过现有代码，确认当前仓库同时存在两条链路：
  - 老链路：`core/*`
  - 新链路：`design/* + workflows/*`
- 已修复本环境下 pytest 临时目录 / cache 权限问题。
- 已新增：
  - `pytest.ini`
  - `tests/conftest.py` 的临时目录兜底逻辑
  - `.gitignore` 中测试运行产生目录的忽略项

### 3.2 新后端主链路骨架

已新增或扩展以下模块：

- `schemaforge/design/synthesis.py`
- `schemaforge/workflows/schemaforge_session.py`
- `schemaforge/agent/design_tools.py`
- `schemaforge/library/models.py`

这条链路已经具备以下能力：

- 从自然语言里抽取精确型号、输入输出电压、电流、是否加 LED
- 精确命中本地器件，支持 alias 命中
- 本地缺件时进入资料导入流程
- 可基于 Buck / LDO 的规则化 recipe 生成 SVG / BOM / SPICE
- 支持简单多轮修改，例如改输出电容、加 LED

### 3.3 已有测试

已新增测试文件：

- `tests/test_schemaforge_session.py`
- `tests/test_design_tools.py`

已覆盖的行为包括：

- 精确料号提取
- alias 命中
- 缺料时返回 `needs_asset`
- 图片导入后确认再生成 Buck
- 修改输出电容与增加 LED
- 工具注册表触发设计流程

### 3.4 最近一次完整质量门结果

在上一个稳定检查点，以下命令是通过的：

- `python -m pytest -q`
- `python -m ruff check schemaforge gui.py tests main.py`

其中 pytest 结果为：

- `935 passed`

## 4. 必须保留的硬约束

`schemaforge/ai/client.py` 中以下三行**必须保留**，不能删除：

```python
DEFAULT_API_KEY = "sk-sp-396701e02c95411783e01557524e4366"
DEFAULT_BASE_URL = "https://coding.dashscope.aliyuncs.com/v1"
DEFAULT_MODEL = "kimi-k2.5"
```

其他内容可以改。

## 5. 当前明确存在的 4 个关键阻塞点

### P0-1 导入确认流程不安全

位置：`schemaforge/workflows/schemaforge_session.py`

问题：

- `confirm_import()` 在上一稳定版本中会先调用：
  - `add_device_from_draft(..., force=True, skip_validation=True)`
- 也就是先把 OCR / 图像提取出来的半成品器件写入库，再去建 symbol / 继续生成设计。
- 如果后续渲染失败，坏数据已经进入本地库。

已复现现象：

- 执行图片导入后，调用 `confirm_import({})`
- 报错：`KeyError 'inL1'`
- 但不完整的 `TPS54202` 已经被写入 store

影响：

- 会污染器件库
- 后续再命中同料号时可能持续出错
- 与“丝滑导入”目标相反

正确修复方向：

- 改成“先补全草稿 → 校验 → 生成 symbol → 预检 build_bundle / render → 成功后再落库”
- 必须引入一种“staging candidate”概念，先在内存里验证，再持久化

### P0-2 recipe 缓存导致不同请求复用旧参数

位置：`schemaforge/design/synthesis.py`

问题：

- `prepare_device()` 里对 `device.design_recipe` 做了直接复用
- 但 `design_recipe.default_parameters` 里包含了按工况算出来的参数，如：
  - `l_value`
  - `r_fb_upper`
  - `r_fb_lower`
- 这样同一芯片第一次算出来的值，会污染后续不同 `Vin/Vout/Iout` 请求

已复现现象：

- 第一次：`TPS5430 12V -> 5V`
- 第二次：`TPS5430 24V -> 3.3V`
- 结果两次都得到同样的 `l_value` 和 `r_fb_upper`

影响：

- 设计参数错误
- 直接违背“从 datasheet 和工况推导外围”的目标

正确修复方向：

- Buck / LDO 等按工况敏感的 recipe，每次请求都必须重新计算
- 不要把“器件级静态知识”和“单次设计计算结果”混存到同一个 `design_recipe.default_parameters`

### P1-3 SPICE 网表没有真实反映 topology 与器件模型

位置：`schemaforge/design/synthesis.py`

问题：

- `_render_spice()` 目前基本是硬编码模板
- 没有优先使用 `device.spice_model`
- 没有依据 `TopologyDef.connections` 真正映射器件引脚与外部元件网络

已知例子：

- `schemaforge/store/devices/TPS5430.json` 已带有：
  - `XU{ref} {VIN} {GND} {EN} {BST} {SW} {FB} TPS5430`
- 但当前生成逻辑没有按这个模板和 topology 来出网表

影响：

- SPICE 文本看起来像网表，但经常并不真实可仿真
- 与“输出完整原理图 + SPICE 网表”的目标仍有距离

正确修复方向：

- 优先渲染 `device.spice_model`
- 用 `TopologyDef.connections` 组装网络映射
- 用 `external_components` 生成 `CIN/COUT/L1/RFB1/RFB2/...`
- 没有模板时再退回保底逻辑

### P1-4 Buck 默认工况错误地使用 absolute max

位置：`schemaforge/design/synthesis.py`

问题：

- `_build_buck_recipe()` 里当用户没给完整工况时，会回落到：
  - `v_in_max`
  - `i_out_max`
- 这把器件额定上限错当成了默认设计点

已复现现象：

- `Use TPS54202 to build a 5V buck converter`
- 会默认得到 `v_in = 28`、`i_out_max = 2`

影响：

- 计算结果偏激，参数不合理
- 不符合工程直觉

正确修复方向：

- 默认工况优先：
  - `v_in_typ`
  - `v_in_nom`
  - `i_out_typ`
  - 典型应用值
- 实在缺失再用保守通用值，比如 `12V / 1A`
- 绝不能默认拿 absolute max 当运行点

## 6. 这轮实际工作状态

本轮并没有把上面 4 个问题修完。

### 已完成

- 再次复查了仓库现状
- 重新定位了关键函数和问题位置
- 已明确后续怎么修
- 已经把需要新增的回归测试思路整理清楚

### 未完成

- `confirm_import()` 安全改造未落地
- recipe 陈旧缓存修复未落地
- SPICE topology-aware 改造未落地
- 默认工况修复未落地

### 当前工作树唯一代码差异

当前 `git diff` 里，只有一个未收尾改动：

- `schemaforge/library/service.py`

内容是：

- `add_device_from_draft()` 新增了一个 `persist: bool = True` 参数

注意：

- 这个改动只是接口入口加了参数
- 方法体还没有完全按 `persist` 语义收尾
- 所以下一个 AI 要么把它做完，要么先回退，避免误导

## 7. 当前工作树状态

已修改：

- `schemaforge/library/service.py`

未跟踪目录：

- `.review-tmp/`
- `.review-tmp-2/`
- `.review-tmp-3/`
- `.review-tmp-4/`
- `schemaforge/web/`

说明：

- `.review-tmp-*` 是复现问题时用的临时目录
- `schemaforge/web/` 当前未处理，不应在这次 handoff 里误判为新改动重点

## 8. 下一个 AI 应该怎么接手

推荐按以下顺序推进。

### Step 1：先处理 `persist` 半成品接口

文件：`schemaforge/library/service.py`

做法二选一：

- 要么把 `persist=False` 的完整语义补完
- 要么先把这个参数回退掉，回到纯稳定状态

### Step 2：修 `confirm_import()` 的安全导入链路

文件：`schemaforge/workflows/schemaforge_session.py`

目标：

- `confirm_import()` 不允许再先落库后验证
- 对不完整 active 器件，返回 `needs_confirmation`，并给出 warnings
- 只有预检成功，才真正入库并生成设计

建议实现形态：

- `_complete_import_draft()`
- `_prepare_import_candidate()`
- `_build_import_preview()`
- `_finalize_bundle()`

### Step 3：修 `prepare_device()` 的陈旧缓存

文件：`schemaforge/design/synthesis.py`

目标：

- 对 `buck` / `ldo` 每次按请求重算 recipe
- 只对真正静态 topology / generic 结构复用已有数据

### Step 4：修 SPICE 生成

文件：`schemaforge/design/synthesis.py`

目标：

- 优先用 `device.spice_model`
- 从 `TopologyDef.connections` 推导 pin-net 映射
- 从 `external_components` 生成外围器件语句

### Step 5：修 Buck 默认工况

文件：`schemaforge/design/synthesis.py`

目标：

- 不再使用 `v_in_max / i_out_max` 作为默认运行点
- 优先典型值，其次保守通用值

### Step 6：补回归测试

建议新增测试：

- 不完整导入确认不落库
- 同一芯片不同工况会重算 `l_value` 与 `r_fb_upper`
- 默认工况不使用 absolute max
- SPICE 按模板和 topology 输出正确网络

### Step 7：重新跑质量门

必须重新跑：

- `python -m pytest -q`
- `python -m ruff check schemaforge gui.py tests main.py`

## 9. 建议新增的回归测试示例

建议加入到 `tests/test_schemaforge_session.py`：

- `test_confirm_import_rejects_incomplete_active_device_without_saving`
- `test_session_recomputes_recipe_for_different_requests`
- `test_buck_defaults_do_not_use_absolute_max_as_operating_point`
- `test_spice_uses_device_template_and_topology_nets`

## 10. 对下一个 AI 的一句话交接

请基于当前分支继续，优先修：

1. `SchemaForgeSession.confirm_import()` 的安全落库问题
2. `DesignRecipeSynthesizer.prepare_device()` 的陈旧 recipe 复用问题
3. `_render_spice()` 对 `device.spice_model + topology.connections` 的真实映射
4. `_build_buck_recipe()` 错用 absolute max 作为默认工况的问题

当前工作树里只有一个半成品代码差异：

- `schemaforge/library/service.py` 增加了 `persist` 参数，但还没有完整收尾

除此之外，请以最近绿色检查点为准继续推进。
