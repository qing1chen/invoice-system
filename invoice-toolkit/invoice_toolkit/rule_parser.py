"""
规则解析模块

从 Skill 模板（Markdown 格式）解析自然语言检查规则，
转换为 AttachmentChecker 可直接使用的 ATTACHMENT_RULES 字典格式。

支持：
    - 从 templates/rules.md 读取自然语言规则
    - 用 LLM 解析自然语言规则为结构化配置（可选）
    - 直接解析 Markdown 格式的标准规则字段
    - 与现有 checker.py 的 ATTACHMENT_RULES 格式兼容
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# =========================================================================
# 模板目录探测
# =========================================================================

_CHECKER_SKILL_DIRS: List[Path] = []


def _find_checker_skill_dirs(settings=None) -> List[Path]:
    """惰性查找 attachment-checker Skill 的 templates 目录。"""
    global _CHECKER_SKILL_DIRS
    if _CHECKER_SKILL_DIRS:
        return _CHECKER_SKILL_DIRS

    candidates = [
        Path(__file__).resolve().parent / "skills" / "attachment-checker" / "templates",
        Path(__file__).resolve().parent.parent / "skills" / "attachment-checker" / "templates",
    ]
    if settings:
        candidates.insert(
            0,
            Path(settings.paths.project_root) / "skills" / "attachment-checker" / "templates",
        )

    _CHECKER_SKILL_DIRS = [d for d in candidates if d.is_dir()]
    return _CHECKER_SKILL_DIRS


def load_rules_template(template_name: str = "rules", settings=None) -> Optional[str]:
    """
    加载指定名称的规则模板内容。

    Args:
        template_name: 模板文件名（不含 .md 后缀），默认 "rules"
        settings: Settings 实例（可选，用于定位项目根目录）

    Returns:
        模板文本内容，未找到返回 None
    """
    dirs = _find_checker_skill_dirs(settings)
    for d in dirs:
        md_path = d / f"{template_name}.md"
        if md_path.exists():
            return md_path.read_text(encoding="utf-8")
    return None


def list_available_templates(settings=None) -> List[str]:
    """列出所有可用的规则模板名称。"""
    dirs = _find_checker_skill_dirs(settings)
    names = []
    for d in dirs:
        names.extend(f.stem for f in d.glob("*.md"))
    return sorted(set(names))


# =========================================================================
# Markdown 规则解析器
# =========================================================================

def parse_rules_from_markdown(content: str) -> Dict[str, Dict[str, Any]]:
    """
    从 Markdown 格式的规则模板解析出结构化的检查规则。

    解析 `## 类别名` 下的各字段，转换为与 ATTACHMENT_RULES 兼容的字典。

    Args:
        content: Markdown 文本

    Returns:
        {类别名: {required_attachment, invoice_keywords, attachment_keywords,
                   description, auto_generate, conditional, check_rule_text}}
    """
    rules: Dict[str, Dict[str, Any]] = {}

    # 按 ## 标题分割
    sections = re.split(r'\n## ', content)

    for section in sections:
        if not section.strip():
            continue

        # 取标题行
        lines = section.strip().split('\n')
        title = lines[0].strip().lstrip('#').strip()

        # 跳过非类别的标题（如「通用匹配策略」「异常标记格式」等说明段落）
        skip_titles = {
            "通用匹配策略", "异常标记格式", "文件名规范",
            "发票附件检查规则",  # 顶部 H1
        }
        if title in skip_titles or not title:
            continue

        body = '\n'.join(lines[1:])
        rule = _parse_single_category(title, body)
        if rule:
            rules[title] = rule

    return rules


def _parse_single_category(title: str, body: str) -> Optional[Dict[str, Any]]:
    """解析单个类别的规则字段。"""
    rule: Dict[str, Any] = {
        "required_attachment": "",
        "invoice_keywords": [],
        "attachment_keywords": [],
        "description": "",
        "auto_generate": False,
        "conditional": False,
        "check_rule_text": "",  # 保留原始自然语言规则，供 LLM 直接使用
    }

    # 提取各字段
    # 必需附件
    m = re.search(r'\*\*必需附件\*\*\s*[:：]\s*(.+)', body)
    if m:
        rule["required_attachment"] = m.group(1).strip()

    # 发票特征
    m = re.search(r'\*\*发票特征\*\*\s*[:：]\s*(.+)', body)
    if m:
        raw = m.group(1).strip()
        # 括号内的备注不作为关键词
        if raw.startswith("（") or raw.startswith("("):
            rule["invoice_keywords"] = []
        else:
            rule["invoice_keywords"] = [
                k.strip() for k in re.split(r'[,，、]', raw)
                if k.strip()
            ]

    # 附件特征
    m = re.search(r'\*\*附件特征\*\*\s*[:：]\s*(.+)', body)
    if m:
        rule["attachment_keywords"] = [
            k.strip() for k in re.split(r'[,，、]', m.group(1).strip())
            if k.strip()
        ]

    # 自动生成
    m = re.search(r'\*\*自动生成\*\*\s*[:：]\s*(.+)', body)
    if m:
        val = m.group(1).strip().lower()
        rule["auto_generate"] = val in ("是", "yes", "true", "1")

    # 条件性（材料类）
    if "条件" in rule.get("required_attachment", "") or "分级" in body:
        rule["conditional"] = True

    # 检查规则（自然语言）—— 提取完整的检查规则文本
    # 匹配 **检查规则** 或 **检查规则（分级）** 后的内容
    m = re.search(
        r'\*\*检查规则[^*]*\*\*\s*[:：]\s*(.+?)(?=\n---|\n## |\Z)',
        body,
        re.DOTALL,
    )
    if m:
        rule["check_rule_text"] = m.group(1).strip()

    # description：用必需附件和类别名生成
    rule["description"] = f"{title}发票需要对应的{rule['required_attachment']}"

    return rule if rule["required_attachment"] else None


# =========================================================================
# 规则合并（模板规则 + 代码硬编码规则）
# =========================================================================

def merge_with_builtin_rules(
    template_rules: Dict[str, Dict[str, Any]],
    builtin_rules: Dict[str, Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    """
    将模板解析的规则与内置硬编码规则合并。

    模板规则优先，模板中未定义的类别回退到内置规则。
    模板中的 check_rule_text 字段保留（内置规则没有此字段）。

    Args:
        template_rules: 从模板解析的规则
        builtin_rules: checker.py 中的 ATTACHMENT_RULES

    Returns:
        合并后的完整规则字典
    """
    merged = {}

    # 先加入内置规则
    for cat, rule in builtin_rules.items():
        merged[cat] = dict(rule)

    # 模板规则覆盖/新增
    for cat, rule in template_rules.items():
        if cat in merged:
            # 保留模板的自然语言规则，其他字段如果模板有值则覆盖
            existing = merged[cat]
            for key, val in rule.items():
                if key == "check_rule_text":
                    existing[key] = val  # 总是覆盖
                elif val:  # 模板有值才覆盖
                    if isinstance(val, list) and not val:
                        continue
                    existing[key] = val
        else:
            # 模板新增的类别
            merged[cat] = rule

    return merged


# =========================================================================
# LLM 辅助解析（高级用法）
# =========================================================================

def parse_rules_with_llm(content: str, llm_client=None) -> Optional[Dict[str, Dict[str, Any]]]:
    """
    使用 LLM 解析自由格式的自然语言规则。

    当模板不遵循标准 Markdown 格式时，回退到 LLM 解析。

    Args:
        content: 自然语言规则文本
        llm_client: LLMClient 实例

    Returns:
        解析后的规则字典，失败返回 None
    """
    if not llm_client:
        return None

    sys_prompt = (
        "你是财务附件检查规则解析专家。将自然语言检查规则转换为 JSON 格式。\n"
        "每个类别包含：required_attachment, invoice_keywords(列表), "
        "attachment_keywords(列表), description, auto_generate(布尔), "
        "conditional(布尔), check_rule_text(原始规则文本)。\n"
        "只返回 JSON，格式为 {\"类别名\": {...}, ...}。"
    )
    user_prompt = f"请解析以下检查规则：\n\n{content}"

    try:
        import json
        result = llm_client.chat(sys_prompt, user_prompt)
        # 清理 JSON 代码块标记
        result = re.sub(r'```json\s*', '', result)
        result = re.sub(r'```\s*', '', result)
        return json.loads(result.strip())
    except Exception as exc:
        logger.warning("LLM 解析规则失败: %s", exc)
        return None
