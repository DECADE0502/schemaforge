from __future__ import annotations

from nicegui import ui

BG_BASE = "#1e1e1e"
BG_PANEL = "#252526"
BG_ELEVATED = "#2d2d30"
BG_HOVER = "#2a2d2e"
BG_ACTIVE = "#37373d"

BORDER = "#3e3e42"
BORDER_LIGHT = "#4e4e52"

ACCENT = "#0e7fd4"
ACCENT_HOVER = "#1a8fe3"
ACCENT_PRESSED = "#0b6ab5"
ACCENT_MUTED = "#094771"

SUCCESS = "#4ec9b0"
SUCCESS_BG = "#1a3a2a"
WARNING = "#dcdcaa"
WARNING_BG = "#3a3a1a"
ERROR = "#f44747"
ERROR_BG = "#3a1a1a"

TEXT_PRIMARY = "#cccccc"
TEXT_MUTED = "#858585"
TEXT_ON_ACCENT = "#ffffff"

FONT_FAMILY = '"Microsoft YaHei", "Segoe UI", "PingFang SC", sans-serif'
FONT_MONO = '"Cascadia Code", "Consolas", "JetBrains Mono", monospace'


def get_custom_css() -> str:
    return f"""
<style>
html, body {{
    background-color: {BG_BASE};
    color: {TEXT_PRIMARY};
    font-family: {FONT_FAMILY};
    margin: 0;
    padding: 0;
    height: 100%;
}}

.q-page {{
    background-color: {BG_BASE};
}}

.q-layout {{
    background-color: {BG_BASE};
}}

.q-card {{
    background-color: {BG_PANEL};
    border: 1px solid {BORDER};
    border-radius: 6px;
    color: {TEXT_PRIMARY};
}}

.q-tab-panel {{
    background-color: {BG_BASE};
    padding: 0;
}}

.q-tabs {{
    background-color: {BG_PANEL};
    color: {TEXT_MUTED};
}}

.q-tab--active {{
    color: {TEXT_PRIMARY};
    border-bottom: 2px solid {ACCENT};
}}

.q-tab:hover {{
    background-color: {BG_HOVER};
    color: {TEXT_PRIMARY};
}}

.q-btn {{
    font-family: {FONT_FAMILY};
}}

.q-input .q-field__control {{
    background-color: {BG_ELEVATED};
    border: 1px solid {BORDER};
    border-radius: 4px;
    color: {TEXT_PRIMARY};
}}

.q-input .q-field__control:focus-within {{
    border-color: {ACCENT};
}}

.q-splitter__separator {{
    background-color: {BORDER};
}}

.sf-card {{
    background-color: {BG_PANEL};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 12px;
    color: {TEXT_PRIMARY};
}}

.sf-card:hover {{
    border-color: {BORDER_LIGHT};
}}

.sf-muted {{
    color: {TEXT_MUTED};
    font-size: 12px;
}}

.sf-title {{
    font-size: 16px;
    font-weight: 700;
    color: {TEXT_PRIMARY};
}}

.sf-success-btn {{
    background-color: #2ea043 !important;
    color: {TEXT_ON_ACCENT} !important;
    border: none !important;
}}

.sf-success-btn:hover {{
    background-color: #3fb950 !important;
}}

.sf-danger-btn {{
    background-color: #d73a49 !important;
    color: {TEXT_ON_ACCENT} !important;
    border: none !important;
}}

.sf-danger-btn:hover {{
    background-color: #f85149 !important;
}}

.sf-primary-btn {{
    background-color: {ACCENT} !important;
    color: {TEXT_ON_ACCENT} !important;
    border: none !important;
}}

.sf-primary-btn:hover {{
    background-color: {ACCENT_HOVER} !important;
}}

.ag-theme-quartz-dark {{
    --ag-background-color: {BG_BASE};
    --ag-foreground-color: {TEXT_PRIMARY};
    --ag-header-background-color: {BG_PANEL};
    --ag-header-foreground-color: {TEXT_MUTED};
    --ag-odd-row-background-color: {BG_BASE};
    --ag-row-hover-color: {BG_HOVER};
    --ag-selected-row-background-color: {ACCENT_MUTED};
    --ag-border-color: {BORDER};
    --ag-cell-horizontal-border: solid {BORDER};
    --ag-font-family: {FONT_FAMILY};
    --ag-font-size: 13px;
    --ag-input-focus-border-color: {ACCENT};
    --ag-range-selection-border-color: {ACCENT};
    --ag-row-border-color: {BG_ELEVATED};
}}

.sf-bubble-user {{
    background-color: {ACCENT_MUTED};
    border: 1px solid #0a5a9e;
    border-radius: 8px;
    padding: 8px 12px;
    margin: 4px 0;
    color: {TEXT_ON_ACCENT};
    max-width: 80%;
    align-self: flex-end;
}}

.sf-bubble-assistant {{
    background-color: {BG_ELEVATED};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 8px 12px;
    margin: 4px 0;
    color: {TEXT_PRIMARY};
    max-width: 80%;
    align-self: flex-start;
}}

.sf-bubble-system {{
    background-color: {WARNING_BG};
    border: 1px solid #5a5a2a;
    border-radius: 8px;
    padding: 8px 12px;
    margin: 4px 0;
    color: {WARNING};
    max-width: 90%;
    align-self: center;
    font-size: 12px;
}}

.sf-symbol-preview {{
    background-color: #1a1a2e;
    border: 1px solid {BORDER};
    border-radius: 4px;
    display: flex;
    align-items: center;
    justify-content: center;
    min-height: 200px;
}}

::-webkit-scrollbar {{
    width: 6px;
    height: 6px;
}}

::-webkit-scrollbar-track {{
    background: {BG_BASE};
}}

::-webkit-scrollbar-thumb {{
    background: #424242;
    border-radius: 3px;
}}

::-webkit-scrollbar-thumb:hover {{
    background: #686868;
}}

* {{
    scrollbar-width: thin;
    scrollbar-color: #424242 {BG_BASE};
}}
</style>
"""


def apply_theme() -> None:
    ui.add_head_html(get_custom_css())
