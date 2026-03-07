# 视觉审稿闭环 Task 清单

> 渲染 → 截图 → AI 审稿 → 本地 patch → 重渲染 → 循环 N 次
> AI 只做视觉质量评判，不能改电气连接/参数/器件。

---

## 阶段 1：定义边界与规则 (V001-V010)

- [x] V001 写 `docs/visual-review-loop.md`
- [x] V002 明确这层只负责"视觉质量"，不负责"电气正确性"
- [x] V003 写出允许动作白名单
- [x] V004 写出禁止动作黑名单
- [x] V005 定义最大迭代次数策略
- [x] V006 定义停止条件
- [x] V007 定义失败退出策略
- [x] V008 定义审稿 trace 格式
- [x] V009 定义 AI 审稿分数含义
- [x] V010 定义本地硬指标与 AI 软指标的优先级

## 阶段 2：建立数据模型 (V011-V020)

- [x] V011 新建 `schemaforge/visual_review/models.py`
- [x] V012 定义 `ReviewImageSet`
- [x] V013 定义 `ReviewManifest`
- [x] V014 定义 `VisualIssue`
- [x] V015 定义 `VisualReviewReport`
- [x] V016 定义 `LayoutPatchAction`
- [x] V017 定义 `LayoutPatchPlan`
- [x] V018 定义 `RenderScore`
- [x] V019 定义 `VisualReviewTrace`
- [x] V020 为这些模型写序列化测试

## 阶段 3：截图与审稿素材生成 (V021-V030)

- [ ] V021 实现 `render_review_images()`
- [ ] V022 输出整图 PNG
- [ ] V023 输出高 DPI 整图 PNG
- [ ] V024 输出每个模块的局部裁剪图
- [ ] V025 输出模块间连接区域裁剪图
- [ ] V026 输出文字密集区域裁剪图
- [ ] V027 实现 `build_review_manifest()`
- [ ] V028 把模块列表写入 manifest
- [ ] V029 把关键连接写入 manifest
- [ ] V030 把 unresolved 项写入 manifest

## 阶段 4：AI 审稿接口 (V031-V040)

- [ ] V031 新建 `schemaforge/visual_review/critic.py`
- [ ] V032 定义 AI 审稿 prompt
- [ ] V033 实现 `review_rendered_schematic()`
- [ ] V034 要求 AI 输出结构化 JSON
- [ ] V035 支持多图输入（全图 + 局部图）
- [ ] V036 把 manifest 一并提供给 AI
- [ ] V037 实现 `validate_visual_review_report()`
- [ ] V038 对非法建议做拒收
- [ ] V039 对缺字段做兜底
- [ ] V040 为 AI 审稿接口写 mock 测试

## 阶段 5：本地硬指标评分器 (V041-V050)

- [ ] V041 新建 `schemaforge/visual_review/scoring.py`
- [ ] V042 实现模块是否全部可见检查
- [ ] V043 实现标签重叠检查
- [ ] V044 实现标签越界检查
- [ ] V045 实现模块边界重叠检查
- [ ] V046 实现线交叉数统计
- [ ] V047 实现最小间距检查
- [ ] V048 实现关键连接可视性检查
- [ ] V049 实现 `score_render_quality()`
- [ ] V050 写评分器测试

## 阶段 6：Patch 规划器 (V051-V060)

- [ ] V051 新建 `schemaforge/visual_review/patch_planner.py`
- [ ] V052 实现 `plan_visual_patches()`
- [ ] V053 支持 `increase_module_spacing`
- [ ] V054 支持 `move_module`
- [ ] V055 支持 `move_label`
- [ ] V056 支持 `reroute_connection`
- [ ] V057 支持 `expand_canvas`
- [ ] V058 支持 `add_net_label`
- [ ] V059 拒绝越权 patch
- [ ] V060 写 patch 规划测试

## 阶段 7：Patch 执行器 (V061-V070)

- [ ] V061 新建 `schemaforge/visual_review/patch_executor.py`
- [ ] V062 实现 `apply_visual_patches()`
- [ ] V063 实现模块位置调整
- [ ] V064 实现标签位置调整
- [ ] V065 实现画布尺寸调整
- [ ] V066 实现正交连线风格切换
- [ ] V067 实现局部重新布局
- [ ] V068 保证 patch 不修改系统 IR 电气语义
- [ ] V069 实现 patch 前后 diff 记录
- [ ] V070 写 patch 执行测试

## 阶段 8：重渲染闭环 (V071-V080)

- [ ] V071 新建 `schemaforge/visual_review/loop.py`
- [ ] V072 实现 `run_visual_review_loop()`
- [ ] V073 支持首轮渲染
- [ ] V074 支持 AI 审稿
- [ ] V075 支持 patch 规划
- [ ] V076 支持 patch 执行
- [ ] V077 支持重渲染
- [ ] V078 支持多轮 trace 记录
- [ ] V079 支持提前停止
- [ ] V080 写闭环集成测试

## 阶段 9：GUI 集成 (V081-V090)

- [ ] V081 在 GUI 中增加"视觉优化中"状态显示
- [ ] V082 显示当前第几轮审稿
- [ ] V083 显示本轮发现的问题数量
- [ ] V084 显示 AI 审稿摘要
- [ ] V085 显示本地硬指标评分
- [ ] V086 支持查看每轮截图
- [ ] V087 支持查看每轮 patch
- [ ] V088 支持用户提前停止闭环
- [ ] V089 支持用户切换"快速/标准/精修"模式
- [ ] V090 写 GUI 交互测试

## 阶段 10：验收与回归 (V091-V100)

- [ ] V091 准备一张故意布局很差的系统图样例
- [ ] V092 跑 1 轮闭环，看是否能改善
- [ ] V093 跑 3 轮闭环，看是否收敛
- [ ] V094 验证闭环不改变 BOM
- [ ] V095 验证闭环不改变 SPICE 网表
- [ ] V096 验证闭环不改变系统 IR 的连接关系
- [ ] V097 验证 patch 全部在白名单内
- [ ] V098 验证停止条件正确触发
- [ ] V099 验证 trace 可回放
- [ ] V100 用 "TPS54202 + AMS1117 + STM32 + LED" 场景做最终回归
