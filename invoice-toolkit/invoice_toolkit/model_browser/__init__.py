"""
model_browser — 浏览器自动化 Agent 子模块

被 invoice_toolkit.browser_agent 引用。
"""

from .models import Action, StepResult, BrowserTaskResult

__all__ = [
    "Action",
    "StepResult",
    "BrowserTaskResult",
]
