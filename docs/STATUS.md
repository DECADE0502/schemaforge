# SchemaForge 状态报告（2026-03-08）

## 当前结论

SchemaForge 现在已经有一条**可运行的系统级主链**，但仍不是“完全成熟的自动电路设计系统”。

最准确的说法是：

- 系统级默认后端：`schemaforge/system/session.py` 中的 `SystemDesignSession`
- CLI 默认主链：`main.py`
- GUI 默认主链：`schemaforge/gui/workers/engine_worker.py` -> `SystemDesignSession`
- 旧兼容路径已清除（workflows/ 目录已删除）

## 本轮已验证

- `python -m pytest -q` -> `1332 passed`
- `python -m ruff check schemaforge gui.py tests main.py` -> 通过

## 已真实打通的样例

输入：

```text
20V 输入，用 TPS54202 降压到 5V，再用 AMS1117 降到 3.3V，给 STM32F103C8T6 供电，并且用 MCU 的 PA1 控制一颗 LED
```

当前可真实得到：

- 单张系统 SVG
- `TPS54202 -> AMS1117-3.3 -> STM32F103C8T6` 电源链
- `STM32F103C8T6.PA1 -> LED_INDICATOR.ANODE`
- 全局 BOM
- 含共享网络的 SPICE（如 `NET_5V`、`NET_3.3V`、`NET_PA1_led1`）

## 当前可信能力

- 系统级文本解析、器件解析、连接规则、参数综合、BOM/SPICE 导出
- GUI 文本 revise
- GUI 图片 revise：图片先经 vision 提取成修订文本，再落到同一个 `SystemDesignSession.revise()` 管线
- 系统级文字 revise 已覆盖一组真金路径修改：替换 buck 料号、改 LED 颜色、改唯一 `v_out`
- 系统级最小增删模块 revise 已接通：可新增电源 LED、删除唯一 LED、删除显式 `led2`
- 系统级 GPIO-LED revise 已接通：可新增 `PA2` 控制的 LED，也可把 `led1` 改到新 GPIO
- 系统级最小下游电源模块追加 revise 已接通：可把新的 `AMS1117-3.3` 自动接到现有 `5V` 电源链
- 系统级显式模块定向 revise 已接通：可精确修改 `led2`、`ldo2` 等显式目标模块
- renderer 真实产出 `render_metadata`
- visual review 支持显式启用，并且布局 patch 会真正影响重渲染
- CLI 与 GUI 现在都提供显式 visual review 入口
- 旧 `SchemaForgeSession` 已删除，遗留代码已全面清理

## 当前仍未完成

- visual review 默认仍关闭，不是主流程默认行为
- 图片 revise 还不是直接的图像 patch 闭环，而是“图片 -> 修订文本 -> revise()”
- 复杂多轮增量修改仍可能不稳；当前只对一小组高价值 revise 场景、最小增删模块、GPIO-LED 改线、最小下游电源追加、显式模块定向修改做了真闭环
## 推荐阅读顺序

1. `docs/HANDOVER.md`
2. `docs/archive/schemaforge-implementation.md`
3. `docs/archive/schemaforge-tasks.md`
