"""
Prompt 模板加载器。

把 checker.py 原来硬编码的 4 个 Prompt 常量（_CHECK_CATEGORY_SYSTEM/HUMAN 等）
迁移到 skills/attachment-checker/templates/prompts/*.md 后，由本模块负责加载。

模板格式（YAML frontmatter + 两个段）：

    ---
    name: check_category
    description: ...
    input_variables: [a, b, c]
    ---

    ## System
    你是...

    ## Human
    # 类别：{category}
    ...

加载后返回 `langchain_core.prompts.ChatPromptTemplate`，可以直接被
`LLMClient.build_chain()` 消费，调用方零改动。
"""

from __future__ import annotations

import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from langchain_core.prompts import ChatPromptTemplate

logger = logging.getLogger(__name__)


# ── Frontmatter 与段落解析 ──────────────────────────────────

_FRONTMATTER_RE = re.compile(
    r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL,
)
_SECTION_RE = re.compile(
    r"^##\s+(System|Human)\s*$", re.IGNORECASE | re.MULTILINE,
)


def _parse_frontmatter(text: str) -> Tuple[Dict[str, str], str]:
    """提取 YAML frontmatter（只支持简单 key: value / 列表）。"""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    meta_text, body = m.group(1), m.group(2)

    meta: Dict[str, object] = {}
    current_key: Optional[str] = None
    for line in meta_text.splitlines():
        if not line.strip():
            continue
        if line.startswith("  - "):  # 列表项
            if current_key:
                meta.setdefault(current_key, []).append(line[4:].strip())
        elif ":" in line and not line.startswith(" "):
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip()
            if not value:
                meta[key] = []
                current_key = key
            else:
                meta[key] = value
                current_key = None
    return meta, body


def _split_sections(body: str) -> Tuple[str, str]:
    """按 '## System' / '## Human' 切分 body，返回 (system, human)。"""
    parts = _SECTION_RE.split(body)
    # parts = ['', 'System', '<system body>', 'Human', '<human body>']
    if len(parts) < 5:
        raise ValueError(
            "Prompt 模板必须同时包含 '## System' 和 '## Human' 两段"
        )
    sections: Dict[str, str] = {}
    it = iter(parts[1:])
    for name, content in zip(it, it):
        sections[name.lower()] = content.strip()
    system = sections.get("system", "")
    human = sections.get("human", "")
    if not system or not human:
        raise ValueError("Prompt 模板缺少 System 或 Human 段内容")
    return system, human


class PromptLoader:
    """从 skills/attachment-checker/templates/prompts/ 加载 prompt 模板。

    用法：
        loader = PromptLoader(Path("skills/attachment-checker"))
        prompt = loader.load("check_category")
        chain = llm.build_chain(prompt, output_json=True)
    """

    def __init__(self, skill_dir: Path):
        self.skill_dir = Path(skill_dir)
        self.prompts_dir = self.skill_dir / "templates" / "prompts"

    @lru_cache(maxsize=32)
    def load(self, name: str) -> ChatPromptTemplate:
        """加载 <name>.md 为 ChatPromptTemplate（带缓存）。"""
        path = self.prompts_dir / f"{name}.md"
        if not path.exists():
            raise FileNotFoundError(
                f"Prompt 模板不存在: {path}。"
                f"请确认 skill 目录下存在 templates/prompts/{name}.md"
            )
        text = path.read_text(encoding="utf-8")
        _meta, body = _parse_frontmatter(text)
        system, human = _split_sections(body)
        logger.debug("已加载 prompt 模板: %s (system=%d chars, human=%d chars)",
                     name, len(system), len(human))
        return ChatPromptTemplate.from_messages([
            ("system", system),
            ("human", human),
        ])

    # ── rules.md 通用规则抽取 ───────────────────────────────

    @lru_cache(maxsize=4)
    def load_common_rules(self, template_name: str = "rules") -> str:
        """抽取 rules.md 顶部的「通用金额规则」段作为跨类别通用规则。

        约定：rules.md 中第一个 `## 通用...` 二级标题到下一个 `## ` 或 `---`
        之间的内容视为通用规则。没有匹配到时返回空字符串，调用方应退回
        `check_category` 的默认判定流程。
        """
        path = self.skill_dir / "templates" / f"{template_name}.md"
        if not path.exists():
            logger.warning("rules.md 不存在: %s", path)
            return ""
        text = path.read_text(encoding="utf-8")

        # 匹配第一个 '## 通用...' 段
        m = re.search(
            r"^##\s+通用[^\n]*\n(.*?)(?=^##\s|^---\s*$)",
            text, re.DOTALL | re.MULTILINE,
        )
        if not m:
            logger.info("rules.md 中未找到「通用...」段，common_rules 为空")
            return ""
        return m.group(1).strip()
