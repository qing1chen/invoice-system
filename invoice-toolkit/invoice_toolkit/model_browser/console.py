"""控制台输出 — ANSI 颜色 & 调试打印"""

from __future__ import annotations

import sys
from datetime import datetime

from .models import Action

# ANSI 颜色
C = "\033[96m"   # cyan
G = "\033[92m"   # green
Y = "\033[93m"   # yellow
R = "\033[91m"   # red
B = "\033[1m"    # bold
D = "\033[2m"    # dim
RST = "\033[0m"  # reset


def print_step_header(step_num: int, max_steps: int) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"\n{B}{C}{'─' * 50}{RST}")
    print(f"  {B}步骤 {step_num}/{max_steps}{RST}  {D}{ts}{RST}")
    print(f"{C}{'─' * 50}{RST}")
    sys.stdout.flush()


def print_dom_summary(dom_text: str, page_url: str) -> None:
    lines = dom_text.split("\n")
    print(f"  {D}🌐 {page_url}{RST}")
    for line in lines[2:5]:
        print(f"  {D}{line}{RST}")
    for line in lines[5:10]:
        print(f"  {D}{line}{RST}")
    if len(lines) > 10:
        print(f"  {D}  ... (更多元素省略){RST}")
    sys.stdout.flush()


def print_thinking(thinking: str) -> None:
    if thinking:
        display = thinking[:200] + "..." if len(thinking) > 200 else thinking
        print(f"  {D}💭 思考: {display}{RST}")
    sys.stdout.flush()


def print_action(action: Action) -> None:
    desc = action.description or f"{action.type}"
    if action.index >= 0:
        desc += f" [索引={action.index}]"
    if action.value:
        desc += f" 值='{action.value[:50]}'"
    print(f"  {Y}🎯 动作: {desc}{RST}")
    sys.stdout.flush()


def print_result(success: bool, message: str, elapsed_ms: int) -> None:
    icon = f"{G}✅" if success else f"{R}❌"
    print(f"  {icon} {message}{RST}  {D}({elapsed_ms}ms){RST}")
    sys.stdout.flush()


def print_llm_raw(raw: str) -> None:
    if raw:
        display = raw[:300] + "..." if len(raw) > 300 else raw
        print(f"  {D}📎 LLM原始返回:{RST}")
        for line in display.split("\n"):
            print(f"  {D}   {line}{RST}")
    sys.stdout.flush()
