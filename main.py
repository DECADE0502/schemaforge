#!/usr/bin/env python3
"""SchemaForge -- AI驱动的原理图生成器

CLI入口，通过 AI agent (Orchestrator + function calling) 驱动设计：
- 交互模式：多轮对话，AI 自动调用工具完成设计
- 单次模式：命令行传入需求，AI 完成后退出
"""

from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

# 修复Windows终端编码问题
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# 确保项目根目录在Python路径中
sys.path.insert(0, str(Path(__file__).parent))

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text

from schemaforge.agent.design_tools_v3 import AGENT_SYSTEM_PROMPT
from schemaforge.system.session import SystemDesignSession, SystemDesignResult

console = Console(force_terminal=True)


def _build_orchestrator(session: SystemDesignSession) -> object:
    """为 session 构建 AI Orchestrator + 原子工具集 (v3)。"""
    from schemaforge.agent.orchestrator import Orchestrator
    from schemaforge.agent.design_tools_v3 import build_atomic_design_tools
    from schemaforge.agent.tools import default_registry

    design_tools = build_atomic_design_tools(session)
    merged = default_registry.merge(design_tools)
    return Orchestrator(
        tool_registry=merged,
        system_prompt=AGENT_SYSTEM_PROMPT,
        model="kimi-k2.5",
    )


def print_banner() -> None:
    """打印欢迎横幅"""
    banner = Text()
    banner.append("SchemaForge", style="bold cyan")
    banner.append(" -- AI驱动的原理图生成器\n", style="dim")
    banner.append(
        "输入自然语言电路需求，自动生成原理图SVG + BOM清单 + SPICE网表", style="dim"
    )
    console.print(Panel(banner, border_style="cyan"))


def _display_result(result: SystemDesignResult) -> None:
    """显示设计结果到控制台。"""
    if result.bundle is None:
        console.print(f"[bold red][ERROR][/bold red] {result.message}")
        return

    bundle = result.bundle
    ir = bundle.design_ir
    module_labels = []
    for instance in ir.module_instances.values():
        if instance.device is not None:
            module_labels.append(instance.device.part_number)
        else:
            module_labels.append(instance.module_id)
    title_prefix = "[bold green][PASS][/bold green]" if result.status == "generated" else "[bold yellow][PARTIAL][/bold yellow]"
    console.print(
        Panel(
            f"{title_prefix} {result.message}\n"
            f"模块: [bold]{', '.join(module_labels)}[/bold]",
            title="设计概要",
            border_style="green" if result.status == "generated" else "yellow",
        )
    )

    if result.missing_modules:
        console.print(
            f"[bold yellow]缺失器件模块:[/bold yellow] {', '.join(result.missing_modules)}"
        )

    if bundle.svg_path:
        console.print(f"\n[bold cyan][SVG][/bold cyan] {bundle.svg_path}")

    if bundle.bom_text:
        console.print("\n[bold cyan][BOM] 清单:[/bold cyan]")
        console.print(Markdown(bundle.bom_text))

    if bundle.spice_text:
        console.print("\n[bold cyan][SPICE] 网表:[/bold cyan]")
        console.print(Panel(bundle.spice_text, title="SPICE", border_style="dim"))

    if ir.warnings:
        console.print("\n[bold yellow][Warnings][/bold yellow]")
        for warning in ir.warnings:
            console.print(f"  • {warning}")

    console.print()


def process_via_agent(
    user_input: str,
    session: SystemDesignSession,
    orch: object,
) -> None:
    """通过 AI agent 处理设计请求。"""
    console.print(
        f"\n[bold yellow]AI 正在分析需求并调用工具…[/bold yellow] 输入: {user_input}\n"
    )

    step = orch.run_turn(user_input)  # type: ignore[union-attr]

    # AI 的回复文本
    if step.message:
        console.print(f"[bold]AI:[/bold] {step.message}\n")

    # 从 session 获取设计结果并展示
    if session.bundle:
        result = SystemDesignResult(
            status="generated",
            message=step.message or "AI 设计完成",
            bundle=session.bundle,
        )
    else:
        result = SystemDesignResult(
            status="partial" if step.message else "failed",
            message=step.message or "AI 未能完成设计",
            bundle=session.bundle,
        )
    _display_result(result)


def run_interactive(session: SystemDesignSession, orch: object) -> None:
    """AI agent 交互模式，支持多轮对话。"""
    console.print(
        "[bold]进入 AI agent 交互模式[/bold] "
        "(输入 'quit' 退出, AI 会自动调用工具完成设计)\n"
    )

    while True:
        try:
            prompt = "[bold cyan]> [/bold cyan]"
            user_input = console.input(prompt).strip()
        except (KeyboardInterrupt, EOFError):
            console.print("\n[dim]再见![/dim]")
            break

        if not user_input:
            continue
        if user_input.lower() in ("quit", "exit", "q"):
            console.print("[dim]再见![/dim]")
            break

        process_via_agent(user_input, session, orch)


def main() -> None:
    """主入口"""
    parser = argparse.ArgumentParser(description="SchemaForge -- AI驱动的原理图生成器")
    parser.add_argument(
        "--visual-review",
        action="store_true",
        help="在系统主链生成后追加 visual review 布局审稿（默认关闭）",
    )
    parser.add_argument("-i", "--input", type=str, default="", help="直接传入电路需求(单次模式)")
    parser.add_argument("--store", type=str, default="", help="器件库路径(默认: schemaforge/store)")
    args = parser.parse_args()

    print_banner()
    store_dir = Path(args.store) if args.store else Path("schemaforge/store")

    if args.visual_review:
        console.print("[dim]visual review: 已启用[/dim]\n")

    console.print("[dim]后端: AI agent (kimi-k2.5 function calling)[/dim]\n")

    session = SystemDesignSession(
        store_dir=store_dir,
        skip_ai_parse=False,
        enable_visual_review=args.visual_review,
    )
    orch = _build_orchestrator(session)

    if args.input:
        process_via_agent(args.input, session, orch)
    else:
        run_interactive(session, orch)


if __name__ == "__main__":
    main()
