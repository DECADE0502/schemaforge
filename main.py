#!/usr/bin/env python3
"""SchemaForge -- 约束驱动的AI原理图生成器

CLI入口，支持：
- 交互模式：输入电路需求，生成原理图
- Demo模式：运行预设示例
- 单次模式：命令行传入需求
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
from rich.table import Table
from rich.text import Text

from schemaforge.core.engine import SchemaForgeEngine
from schemaforge.core.templates import list_templates, get_template
from schemaforge.render.composite import render_composite

console = Console(force_terminal=True)


def print_banner() -> None:
    """打印欢迎横幅"""
    banner = Text()
    banner.append("SchemaForge", style="bold cyan")
    banner.append(" -- 约束驱动的AI原理图生成器\n", style="dim")
    banner.append("输入自然语言电路需求，自动生成原理图SVG + BOM清单 + SPICE网表", style="dim")
    console.print(Panel(banner, border_style="cyan"))


def print_templates() -> None:
    """打印可用模板列表"""
    table = Table(title="可用电路模板", border_style="blue")
    table.add_column("模板名", style="cyan")
    table.add_column("显示名", style="green")
    table.add_column("分类", style="yellow")
    table.add_column("说明")

    for name in list_templates():
        t = get_template(name)
        if t:
            table.add_row(t.name, t.display_name, t.category, t.description)

    console.print(table)
    console.print()


def process_and_display(user_input: str, engine: SchemaForgeEngine) -> None:
    """处理用户输入并显示结果"""
    console.print(f"\n[bold yellow]处理中...[/bold yellow] 输入: {user_input}\n")

    result = engine.process(user_input)

    if not result.success:
        console.print(f"[bold red][ERROR][/bold red] 阶段: {result.stage}")
        console.print(f"  {result.error}")
        return

    # 成功 -- 显示结果
    console.print(Panel(
        f"[bold green][PASS] 设计完成[/bold green]\n"
        f"[bold]{result.design_name}[/bold]\n"
        f"{result.description}",
        title="设计概要",
        border_style="green",
    ))

    # SVG路径
    if result.svg_paths:
        console.print("\n[bold cyan][SVG] 原理图:[/bold cyan]")
        for p in result.svg_paths:
            console.print(f"  -> {p}")

    # ERC结果
    if result.erc_errors:
        erc_errors = [e for e in result.erc_errors if e.severity.value == "error"]
        erc_warnings = [e for e in result.erc_errors if e.severity.value != "error"]
        if erc_errors:
            console.print(f"\n[bold red][ERC] 错误: {len(erc_errors)}[/bold red]")
            for e in erc_errors[:5]:
                console.print(f"  [X] {e.message}")
        if erc_warnings:
            console.print(f"\n[yellow][ERC] 警告: {len(erc_warnings)}[/yellow]")
    else:
        console.print("\n[green][ERC] 检查通过[/green]")

    # BOM
    if result.bom_text:
        console.print("\n[bold cyan][BOM] 清单:[/bold cyan]")
        console.print(Markdown(result.bom_text))

    # SPICE
    if result.spice_text:
        console.print("\n[bold cyan][SPICE] 网表:[/bold cyan]")
        console.print(Panel(result.spice_text, title="SPICE", border_style="dim"))

    # 设计备注
    if result.notes:
        console.print("\n[bold cyan][NOTE] 设计备注:[/bold cyan]")
        console.print(f"  {result.notes}")

    console.print()


def run_demo(engine: SchemaForgeEngine) -> None:
    """运行预设Demo"""
    console.print("[bold magenta][DEMO] 运行Demo模式[/bold magenta]\n")

    demos = [
        ("5V转3.3V稳压电路，带绿色LED电源指示灯", "LDO+LED组合"),
        ("12V到3.3V的分压采样电路", "电压分压器"),
        ("1kHz低通滤波器", "RC低通滤波器"),
    ]

    for user_input, desc in demos:
        console.rule(f"[bold]{desc}[/bold]")
        process_and_display(user_input, engine)

    # 额外渲染组合电路
    console.rule("[bold]组合电路(LDO+LED一体化渲染)[/bold]")
    path = render_composite()
    console.print(f"[green][PASS] 组合电路SVG: {path}[/green]\n")

    console.print("[bold green][DONE] Demo完成! 所有SVG文件在 schemaforge/output/ 目录下。[/bold green]")


def run_interactive(engine: SchemaForgeEngine) -> None:
    """交互模式"""
    console.print("[bold]进入交互模式[/bold] (输入 'quit' 退出, 'help' 查看帮助)\n")

    while True:
        try:
            user_input = console.input("[bold cyan]请输入电路需求 > [/bold cyan]").strip()
        except (KeyboardInterrupt, EOFError):
            console.print("\n[dim]再见![/dim]")
            break

        if not user_input:
            continue
        if user_input.lower() in ("quit", "exit", "q"):
            console.print("[dim]再见![/dim]")
            break
        if user_input.lower() == "help":
            print_templates()
            console.print("示例输入:")
            console.print("  * 设计一个5V转3.3V的稳压电路")
            console.print("  * LED电源指示灯")
            console.print("  * 12V到3.3V分压采样")
            console.print("  * 1kHz低通滤波器\n")
            continue
        if user_input.lower() == "demo":
            run_demo(engine)
            continue

        process_and_display(user_input, engine)


def main() -> None:
    """主入口"""
    parser = argparse.ArgumentParser(
        description="SchemaForge -- 约束驱动的AI原理图生成器",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="运行预设Demo(离线模式)",
    )
    parser.add_argument(
        "--online",
        action="store_true",
        help="使用真实LLM(需要API Key)",
    )
    parser.add_argument(
        "--input", "-i",
        type=str,
        default="",
        help="直接传入电路需求(单次模式)",
    )
    parser.add_argument(
        "--templates",
        action="store_true",
        help="列出所有可用模板",
    )

    args = parser.parse_args()

    print_banner()

    if args.templates:
        print_templates()
        return

    use_mock = not args.online
    engine = SchemaForgeEngine(use_mock=use_mock)

    mode = "离线Mock" if use_mock else "在线LLM"
    console.print(f"[dim]模式: {mode}[/dim]\n")

    if args.demo:
        run_demo(engine)
    elif args.input:
        process_and_display(args.input, engine)
    else:
        run_interactive(engine)


if __name__ == "__main__":
    main()
