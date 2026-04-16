"""数据模型 — Action / StepResult / BrowserTaskResult"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Action:
    """LLM 返回的单个动作"""
    type: str           # click | fill | select | scroll | wait | done | go_to_url ...
    selector: str = ""
    value: str = ""
    index: int = -1
    description: str = ""

    def to_dict(self) -> dict:
        d = {"type": self.type}
        if self.selector:
            d["selector"] = self.selector
        if self.value:
            d["value"] = self.value
        if self.index >= 0:
            d["index"] = self.index
        if self.description:
            d["description"] = self.description
        return d


@dataclass
class StepResult:
    """单步执行结果"""
    step_number: int
    action: Action
    success: bool
    message: str = ""
    error: str = ""
    screenshot_b64: str = ""
    page_url: str = ""
    timestamp: str = ""
    llm_raw: str = ""
    dom_summary: str = ""
    elapsed_ms: int = 0

    def to_dict(self) -> dict:
        return {
            "step": self.step_number,
            "action": self.action.to_dict(),
            "success": self.success,
            "message": self.message,
            "error": self.error,
            "url": self.page_url,
            "timestamp": self.timestamp,
            "elapsed_ms": self.elapsed_ms,
        }


@dataclass
class BrowserTaskResult:
    """最终任务结果"""
    success: bool
    message: str
    steps: List[str] = field(default_factory=list)
    step_details: List[dict] = field(default_factory=list)
    screenshot_base64: Optional[str] = None
    error: Optional[str] = None
    total_steps: int = 0
    elapsed_seconds: float = 0.0
    llm_call_count: int = 0
    browser_errors: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = {
            "success": self.success,
            "message": self.message,
            "steps": self.steps,
            "total_steps": self.total_steps,
            "elapsed_seconds": round(self.elapsed_seconds, 1),
            "llm_call_count": self.llm_call_count,
        }
        if self.error:
            d["error"] = self.error
        if self.screenshot_base64:
            d["screenshot"] = self.screenshot_base64
        if self.step_details:
            d["step_details"] = self.step_details
        if self.browser_errors:
            d["browser_errors"] = self.browser_errors
        return d
