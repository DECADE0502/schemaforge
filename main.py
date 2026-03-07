#!/usr/bin/env python3
"""SchemaForge -- AI驱动的原理图生成器

CLI入口，支持：
- 交互模式：输入电路需求，生成原理图
- 单次模式：命令行传入需求
"""

from __future__ import annotations

import argparse
import io
import os
import sys
from pathlib import Path

# 确保 CLI 模式下始终使用真实 AI
os.environ.pop("SCHEMAFORGE_SKIP_AI_PARSE", None)

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

from schemaforge.workflows.schemaforge_session import SchemaForgeSession

console = Console(force_terminal=True)


def print_banner() -> None:
    """打印欢迎横幅"""
    banner = Text()
    banner.append("SchemaForge", style="bold cyan")
    banner.append(" -- AI驱动的原理图生成器\n", style="dim")
    banner.append(
        "输入自然语言电路需求，自动生成原理图SVG + BOM清单 + SPICE网表", style="dim"
    )
    console.print(Panel(banner, border_style="cyan"))


def process_and_display_unified(user_input: str, sf_session: SchemaForgeSession) -> None:
    """统一工作台处理并显示结果"""
    console.print(
        f"\n[bold yellow]处理中 (统一工作台)...[/bold yellow] 输入: {user_input}\n"
    )

    result = sf_session.start(user_input)

    if result.status == "needs_asset":
        console.print(
            f"[bold yellow][MISSING][/bold yellow] {result.message}"
        )
        console.print(
            f"  缺失器件: [bold]{result.missing_part_number}[/bold]"
        )
        console.print("  请使用 GUI 模式上传 datasheet 补录器件。")
        return

    if result.status != "generated" or result.bundle is None:
        console.print(f"[bold red][ERROR][/bold red] {result.message}")
        return

    bundle = result.bundle
    console.print(
        Panel(
            f"[bold green][PASS] 设计完成[/bold green]\n"
            f"[bold]{bundle.device.part_number}[/bold] — {bundle.device.description}",
            title="设计概要 (统一工作台)",
            border_style="green",
        )
    )

    if bundle.svg_path:
        console.print(f"\n[bold cyan][SVG][/bold cyan] {bundle.svg_path}")

    if bundle.bom_text:
        console.print("\n[bold cyan][BOM] 清单:[/bold cyan]")
        console.print(Markdown(bundle.bom_text))

    if bundle.spice_text:
        console.print("\n[bold cyan][SPICE] 网表:[/bold cyan]")
        console.print(Panel(bundle.spice_text, title="SPICE", border_style="dim"))

    if bundle.rationale:
        console.print("\n[bold cyan][设计依据][/bold cyan]")
        for r in bundle.rationale:
            console.print(f"  • {r}")

    console.print()


def process_and_display_orchestrated(user_input: str, sf_session: SchemaForgeSession) -> None:
    """AI 编排模式处理并显示结果"""
    console.print(
        f"\n[bold yellow]AI 编排中...[/bold yellow] 输入: {user_input}\n"
    )

    step = sf_session.run_orchestrated(user_input)

    action_name = step.action.value if step.action is not None else "unknown"

    if action_name == "finalize":
        console.print(
            Panel(
                f"[bold green][PASS] AI 编排完成[/bold green]\n{step.message}",
                title="AI 编排结果",
                border_style="green",
            )
        )
        bundle = sf_session.bundle
        if bundle is not None:
            if bundle.svg_path:
                console.print(f"\n[bold cyan][SVG][/bold cyan] {bundle.svg_path}")
            if bundle.bom_text:
                console.print("\n[bold cyan][BOM] 清单:[/bold cyan]")
                console.print(Markdown(bundle.bom_text))

    elif action_name == "ask_user":
        console.print(f"[bold yellow][AI 提问][/bold yellow] {step.message}")
        questions = getattr(step, "questions", [])
        for q in questions:
            q_text = getattr(q, "text", str(q))
            console.print(f"  ? {q_text}")

    elif action_name == "fail":
        console.print(f"[bold red][FAIL][/bold red] {step.message}")

    else:
        console.print(f"[dim][AI {action_name}] {step.message}[/dim]")

    console.print()


def run_interactive_unified(sf_session: SchemaForgeSession) -> None:
    """统一工作台交互模式，支持多轮修改。"""
    console.print(
        "[bold]进入统一工作台交互模式[/bold] "
        "(输入 'quit' 退出, 首条消息发起设计, 后续消息修改设计)\n"
    )

    has_design = False
    while True:
        try:
            prompt = "[bold cyan]修改指令 > [/bold cyan]" if has_design else "[bold cyan]电路需求 > [/bold cyan]"
            user_input = console.input(prompt).strip()
        except (KeyboardInterrupt, EOFError):
            console.print("\n[dim]再见![/dim]")
            break

        if not user_input:
            continue
        if user_input.lower() in ("quit", "exit", "q"):
            console.print("[dim]再见![/dim]")
            break

        if has_design:
            # 多轮修改
            console.print(f"\n[bold yellow]修改中...[/bold yellow] {user_input}\n")
            result = sf_session.revise(user_input)
            if result.status == "generated" and result.bundle is not None:
                bundle = result.bundle
                console.print(f"[bold green][PASS][/bold green] {result.message}")
                if bundle.svg_path:
                    console.print(f"  [cyan]SVG:[/cyan] {bundle.svg_path}")
            else:
                console.print(f"[bold red][ERROR][/bold red] {result.message}")
            console.print()
        else:
            # 首次设计
            process_and_display_unified(user_input, sf_session)
            has_design = sf_session.bundle is not None


def run_interactive_orchestrated(sf_session: SchemaForgeSession) -> None:
    """AI 编排交互模式。"""
    console.print(
        "[bold]进入 AI 编排交互模式[/bold] "
        "(输入 'quit' 退出, AI 自动执行工具循环)\n"
    )

    while True:
        try:
            user_input = console.input(
                "[bold cyan]对话 > [/bold cyan]"
            ).strip()
        except (KeyboardInterrupt, EOFError):
            console.print("\n[dim]再见![/dim]")
            break

        if not user_input:
            continue
        if user_input.lower() in ("quit", "exit", "q"):
            console.print("[dim]再见![/dim]")
            break

        process_and_display_orchestrated(user_input, sf_session)


def main() -> None:
    """主入口"""
    parser = argparse.ArgumentParser(description="SchemaForge -- AI驱动的原理图生成器")
    parser.add_argument("--orchestrated", action="store_true", help="使用AI编排模式")
    parser.add_argument("-i", "--input", type=str, default="", help="直接传入电路需求(单次模式)")
    parser.add_argument("--store", type=str, default="", help="器件库路径(默认: schemaforge/store)")
    args = parser.parse_args()

    print_banner()
    store_dir = Path(args.store) if args.store else Path("schemaforge/store")
    sf_session = SchemaForgeSession(store_dir=store_dir, skip_ai_parse=False)

    chain_label = "AI 编排" if args.orchestrated else "统一工作台"
    console.print(f"[dim]后端: {chain_label}[/dim]\n")

    if args.input:
        if args.orchestrated:
            process_and_display_orchestrated(args.input, sf_session)
        else:
            process_and_display_unified(args.input, sf_session)
    else:
        if args.orchestrated:
            run_interactive_orchestrated(sf_session)
        else:
            run_interactive_unified(sf_session)


if __name__ == "__main__":
    main()
