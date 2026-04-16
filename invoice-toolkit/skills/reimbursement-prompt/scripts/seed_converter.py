"""
种子记忆转换器：default.md → flow + fragments

将现有的静态模板转换为记忆系统的初始种子。
这是从旧模板系统向记忆驱动系统迁移的桥梁。

零外部依赖（仅 Python 标准库）。

调用方式：
  1. Python:  from seed_converter import convert_template_to_memory
  2. Bash:    echo '{"template_path":"./default.md","base_dir":"./memory"}' \
              | python seed_converter.py
"""

import json
import os
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

try:
    from .memory_store import MemoryStore
except ImportError:
    from memory_store import MemoryStore


def parse_template_sections(
    template_text: str,
) -> Tuple[List[str], List[Dict[str, Any]]]:
    """解析模板的 once/repeat 区段。

    Returns:
        (once_lines, repeat_sections)
        once_lines: 一次性区段的行列表
        repeat_sections: 重复区段列表，每个包含 title + lines
    """
    lines = template_text.splitlines()
    once_lines: List[str] = []
    repeat_sections: List[Dict[str, Any]] = []

    in_repeat = False
    current_repeat: Optional[Dict] = None

    for line in lines:
        stripped = line.strip()

        # 重复区段开始：===标题===
        match_start = re.match(r"^===(.+)===$", stripped)
        if match_start and not stripped == "======":
            in_repeat = True
            current_repeat = {
                "title": match_start.group(1).strip(),
                "lines": [],
            }
            continue

        # 重复区段结束：======
        if stripped == "======":
            if current_repeat:
                repeat_sections.append(current_repeat)
                current_repeat = None
            in_repeat = False
            continue

        if in_repeat and current_repeat is not None:
            current_repeat["lines"].append(line)
        else:
            once_lines.append(line)

    return once_lines, repeat_sections


def _extract_actions_from_text(text: str) -> List[Dict[str, Any]]:
    """从自然语言指令文本中提取结构化动作列表。"""
    actions = []

    # 模式：点击「XXX」按钮/链接
    for m in re.finditer(r"点击「([^」]+)」(按钮|链接|复选框|文本区域)?", text):
        target = m.group(1)
        elem_type = m.group(2) or "元素"
        actions.append({
            "action": "click",
            "target": f"「{target}」{elem_type}",
        })

    # 模式：在「XXX」输入框中填写 YYY
    for m in re.finditer(
        r"在「([^」]+)」输入框中填写\s*(\S+)", text
    ):
        actions.append({
            "action": "fill",
            "target": f"「{m.group(1)}」输入框",
            "value": m.group(2),
        })

    # 模式：在「XXX」下拉框中选择「YYY」
    for m in re.finditer(
        r"在「([^」]+)」下拉框中选择「([^」]+)」", text
    ):
        actions.append({
            "action": "select",
            "target": f"「{m.group(1)}」下拉框",
            "value": m.group(2),
        })

    # 模式：upload_file 动作上传文件路径「XXX」
    for m in re.finditer(
        r"upload_file\s*动作上传文件路径「([^」]+)」", text
    ):
        actions.append({
            "action": "upload_file",
            "target": "文件上传控件",
            "value": m.group(1),
        })

    # 模式：输入文件路径「XXX」
    for m in re.finditer(r"输入文件路径「([^」]+)」", text):
        actions.append({
            "action": "upload_file",
            "target": "文件上传控件",
            "value": m.group(1),
        })

    # 模式：向下滚动页面
    for m in re.finditer(r"向下滚动页面", text):
        actions.append({
            "action": "scroll",
            "value": "500",
        })

    # 模式：在「XXX」输入框中填写「YYY」
    for m in re.finditer(
        r"在「([^」]+)」输入框中填写「([^」]+)」", text
    ):
        actions.append({
            "action": "fill",
            "target": f"「{m.group(1)}」输入框",
            "value": m.group(2),
        })

    return actions


def convert_template_to_memory(
    template_text: str,
    base_dir: str,
    category: str = "日常报销",
) -> Dict[str, Any]:
    """将模板文本转换为记忆系统的种子数据。

    Args:
        template_text: default.md 的完整内容
        base_dir:      记忆目录路径
        category:      报销类别

    Returns:
        转换结果
    """
    store = MemoryStore(base_dir)

    # 确保目录已初始化
    store.init()

    once_lines, repeat_sections = parse_template_sections(template_text)
    results: Dict[str, Any] = {"fragments": [], "flow": None}

    # ── 1. 从 repeat 区段提取 fragment ──────────────

    fragment_refs = []  # flow 中引用的 fragment 列表

    for section in repeat_sections:
        title = section["title"]
        text = "\n".join(section["lines"])
        actions = _extract_actions_from_text(text)

        if not actions:
            # 没提取到结构化动作，存为原始文本片段
            frag_steps = [{
                "action": "raw_instruction",
                "target": title,
                "value": text.strip(),
            }]
        else:
            frag_steps = actions

        # 根据标题判断 fragment_id
        if "发票" in title and "重复" in title:
            frag_id = "process-invoice"
            frag_title = "处理单张发票"
        elif "附件" in title:
            frag_id = "upload-attachments"
            frag_title = "上传附件"
        else:
            safe = re.sub(r"[^\w\u4e00-\u9fff]", "-", title)
            frag_id = f"repeat-{safe.strip('-')[:20]}"
            frag_title = title

        frag_result = store.write_fragment(
            fragment_id=frag_id,
            title=frag_title,
            steps=frag_steps,
            known_issues=[],
        )
        results["fragments"].append(frag_result)
        fragment_refs.append({
            "fragment_id": frag_id,
            "title": frag_title,
            "original_title": title,
        })

    # ── 2. 从 once 区段 + fragment 引用组装 flow ────

    once_text = "\n".join(once_lines)
    # 按段落拆分（空行分隔）
    paragraphs = [
        p.strip() for p in re.split(r"\n\s*\n", once_text) if p.strip()
    ]

    flow_steps: List[Dict[str, Any]] = []
    frag_idx = 0

    for para in paragraphs:
        # 跳过变量占位符行（如 {{记录ID映射}}）
        if re.match(r"^\{\{[^}]+\}\}$", para.strip()):
            flow_steps.append({
                "title": f"注入变量 {para.strip()}",
                "action": "inject_variable",
                "value": para.strip(),
                "notes": "运行时由 calculate_amounts 自动注入",
            })
            continue

        actions = _extract_actions_from_text(para)

        if actions:
            # 合并为一个步骤组
            if len(actions) == 1:
                step = actions[0]
                step["title"] = step.get("target", "操作")[:40]
                flow_steps.append(step)
            else:
                # 多动作段落 → 推断一个逻辑步骤名
                step_title = _infer_step_title(para, actions)
                for a in actions:
                    a["title"] = a.get("target", "操作")[:40]
                    if step_title and not a.get("notes"):
                        a["notes"] = f"逻辑步骤: {step_title}"
                    flow_steps.append(a)
        else:
            # 无法提取动作 → 存为原始指令
            flow_steps.append({
                "title": para[:40].replace("\n", " "),
                "action": "raw_instruction",
                "notes": para,
            })

        # 在合适的位置插入 fragment 引用
        # 启发式：当 once 文本中出现了和 repeat 区段相关的关键词时
        for fref in fragment_refs:
            if fref["original_title"] in para or fref["title"] in para:
                flow_steps.append({
                    "title": f"执行片段: {fref['title']}",
                    "action": "call_fragment",
                    "fragment": f"{fref['fragment_id']}.frag.md",
                    "notes": f"重复执行: {fref['original_title']}",
                })

    # 没有在 once 中引用的 fragment → 按顺序插入到合适位置
    referenced = {
        s.get("fragment", "").replace(".frag.md", "")
        for s in flow_steps if s.get("fragment")
    }
    for fref in fragment_refs:
        if fref["fragment_id"] not in referenced:
            # 找到 flow 中"所有发票处理完毕"之前的位置
            insert_pos = len(flow_steps)
            for idx, s in enumerate(flow_steps):
                notes = s.get("notes", "")
                title = s.get("title", "")
                if "处理完毕" in notes or "处理完毕" in title \
                   or "前往报销" in notes or "前往报销" in title:
                    insert_pos = idx
                    break
            flow_steps.insert(insert_pos, {
                "title": f"执行片段: {fref['title']}",
                "action": "call_fragment",
                "fragment": f"{fref['fragment_id']}.frag.md",
                "notes": f"重复执行: {fref['original_title']}",
            })

    # 写入 flow
    category_flow_ids = {
        "日常报销": "daily-reimbursement",
        "材料、快递": "daily-reimbursement",
        "国内差旅": "travel-reimbursement",
        "出差": "travel-reimbursement",
        "手机通讯费": "phone-reimbursement",
        "加班餐": "overtime-meal-reimbursement",
        "试剂耗材": "reagent-reimbursement",
    }
    flow_id = category_flow_ids.get(category, "default-reimbursement")

    flow_result = store.write_flow(
        flow_id=flow_id,
        category=category,
        steps=flow_steps,
        source="manual",
        preconditions=["已登录报销系统", "至少有一条待报销记录"],
    )
    results["flow"] = flow_result

    # ── 3. 写入初始业务规则（语义记忆） ──────────────

    store.update_semantic("rules.md", [{
        "section": "模板来源",
        "content": f"初始流程和片段从 default.md 模板转换而来（{category}）。",
    }])

    # ── 4. 重建索引 ─────────────────────────────────

    store.rebuild_index()

    results["success"] = True
    return results


def _infer_step_title(paragraph: str, actions: List[Dict]) -> str:
    """从段落内容推断步骤标题。"""
    if "修改信息" in paragraph:
        return "填写联系人信息"
    if "经费项目" in paragraph:
        return "选择经费项目"
    if "附件" in paragraph or "上传" in paragraph:
        return "上传附件"
    if "报销" in paragraph and "入口" in paragraph:
        return "选择报销入口"
    if "发票" in paragraph:
        return "处理发票"
    if len(actions) > 0:
        return actions[0].get("target", "操作")[:30]
    return paragraph[:30]


# ─── CLI 入口 ────────────────────────────────────────────

if __name__ == "__main__":
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        json.dump(
            {"success": False, "error": f"JSON 解析失败: {e}"},
            sys.stdout, ensure_ascii=False,
        )
        sys.exit(1)

    template_path = payload.get("template_path", "")
    base_dir = payload.get("base_dir", "./memory")
    category = payload.get("category", "日常报销")

    if template_path:
        with open(template_path, "r", encoding="utf-8") as f:
            template_text = f.read()
    elif "template_text" in payload:
        template_text = payload["template_text"]
    else:
        json.dump(
            {"success": False, "error": "需要 template_path 或 template_text"},
            sys.stdout, ensure_ascii=False,
        )
        sys.exit(1)

    result = convert_template_to_memory(template_text, base_dir, category)
    json.dump(result, sys.stdout, ensure_ascii=False, default=str)
