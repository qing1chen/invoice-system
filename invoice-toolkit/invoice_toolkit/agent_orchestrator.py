"""
报销代理编排引擎 (Agent Orchestrator)

位置：backend/invoice_toolkit/agent_orchestrator.py
（从 skills/reimbursement-prompt/scripts/ 移出）

本模块实现 LLM 自主编排的报销流程（仅负责生成浏览器指令）：
  LLM 接收用户意图 + 可用工具列表 → 自行决定调用顺序 → 逐步执行

职责边界：
  本编排器只负责「生成浏览器操作指令」，不负责执行。
  浏览器自动化由外部 BrowserAgent 独立执行，两者是先后关系。

Skill 调用方式：
  calculate_amounts、load_template 等涉及 Skill 资源的操作，
  通过 subprocess 调用 Skill 目录下的脚本（stdin JSON → stdout JSON），
  或直接读取 Skill 目录下的文件。

核心函数:
  run_agent_reimbursement(record_ids, target_url, settings)
    → LLM 多轮对话，每轮可调用一个工具，直到生成完整指令

支持的工具（LLM 自己选择调用哪个、什么顺序）：
  - read_records        — 从数据库读取指定记录
  - calculate_amounts   — 通过 bash 调用 Skill 脚本计算
  - load_template       — 从 Skill 目录读取模板文件
  - render_template     — 用 LLM 智能渲染模板（填充变量），正则替换备选
  - done                — 标记任务完成
"""

import json
import logging
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ─── Skill 目录定位 ───────────────────────────────────────

def _get_skill_dir() -> Path:
    """定位 reimbursement-prompt Skill 目录。

    查找顺序：
      1. 环境变量 SKILL_DIR 指定的路径
      2. 当前文件向上找 backend/skills/reimbursement-prompt/
    """
    import os
    env_dir = os.environ.get("SKILL_DIR")
    if env_dir:
        p = Path(env_dir)
        if p.exists():
            return p

    # 默认：backend/invoice_toolkit/../../skills/reimbursement-prompt
    # 即 backend/skills/reimbursement-prompt
    base = Path(__file__).resolve().parent.parent  # backend/
    skill_dir = base / "skills" / "reimbursement-prompt"
    if skill_dir.exists():
        return skill_dir

    raise FileNotFoundError(
        f"Skill 目录未找到。请设置 SKILL_DIR 环境变量或确认目录存在: {skill_dir}"
    )


# ─── 工具注册表 ─────────────────────────────────────────────

TOOL_DEFINITIONS = [
    {
        "name": "read_records",
        "description": (
            "从数据库读取指定的报销记录（含关联发票信息）。"
            "结果自动存入上下文，后续工具可直接引用，无需通过参数传递。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "record_ids": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "记录的 db_id 列表",
                },
            },
            "required": ["record_ids"],
        },
    },
    {
        "name": "calculate_amounts",
        "description": (
            "按「姓名/公司」分组计算转卡金额、去重附件路径、生成记录ID映射。"
            "自动使用 read_records 的结果，也可以传入 records 参数覆盖。"
            "结果（enriched_records）自动存入上下文。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "use_context": {
                    "type": "boolean",
                    "description": "是否使用上下文中 read_records 的结果，默认 true",
                    "default": True,
                },
            },
        },
    },
    {
        "name": "load_template",
        "description": (
            "从 Skill 模板目录加载提示词模板文件。"
            "默认加载 'default' 模板。结果自动存入上下文。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "template_name": {
                    "type": "string",
                    "description": "模板名称（不含 .md 后缀），默认 'default'",
                    "default": "default",
                },
            },
        },
    },
    {
        "name": "render_template",
        "description": (
            "将提示词模板 + 报销记录渲染为最终的浏览器操作指令。"
            "默认使用 LLM 智能渲染（理解语义、处理缺失字段、裁剪条件分支），"
            "LLM 渲染失败时自动回退到正则替换。"
            "自动使用上下文中 load_template 的模板和 calculate_amounts 的记录。"
            "渲染结果自动存入上下文。"
            "这是生成指令的最后一步，调用后应调用 done 标记完成。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "use_context": {
                    "type": "boolean",
                    "description": "是否使用上下文中的模板和记录，默认 true",
                    "default": True,
                },
                "method": {
                    "type": "string",
                    "enum": ["llm", "regex"],
                    "description": "渲染方式：'llm'（默认，智能渲染）或 'regex'（正则替换备选）",
                    "default": "llm",
                },
            },
        },
    },
    {
        "name": "done",
        "description": (
            "标记指令生成任务已完成。在 render_template 执行完毕后调用。"
            "渲染后的指令将由外部浏览器代理独立执行，本编排器不负责浏览器操作。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "任务完成摘要",
                },
            },
            "required": ["summary"],
        },
    },
]


# ─── 工具执行器 ─────────────────────────────────────────────

def _handle_read_records(args: dict, context: dict) -> dict:
    """从数据库读取记录，存入 context['records']。"""
    settings = context["settings"]
    record_ids = args.get("record_ids", context.get("record_ids", []))

    from invoice_toolkit.database import get_record_db, get_invoice_db
    record_db = get_record_db(settings)
    invoice_db = get_invoice_db(settings)

    all_rows = record_db.get_records_joined(invoice_db)
    if record_ids:
        selected = [r for r in all_rows if r.get("id") in record_ids]
    else:
        selected = all_rows

    # 存入上下文，后续工具直接引用
    context["records"] = selected

    # 返回摘要（不含完整数据，避免撑爆 LLM 上下文）
    summary = []
    categories = set()
    for r in selected:
        summary.append(f"{r.get('姓名/公司', '?')} ¥{r.get('金额', '?')} {r.get('物品简介', '')}")
        cat = r.get("category") or r.get("类别", "")
        if cat:
            categories.add(cat)

    return {
        "success": True,
        "record_count": len(selected),
        "categories": list(categories),
        "category_consistent": len(categories) <= 1,
        "records_summary": summary,
    }


def _handle_calculate_amounts(args: dict, context: dict) -> dict:
    """通过 bash 调用 Skill 脚本计算金额，结果存入 context['enriched_records']。"""
    records = context.get("records")
    if not records:
        return {"success": False, "message": "请先调用 read_records 读取记录"}

    skill_dir = _get_skill_dir()
    script_path = skill_dir / "scripts" / "calculate_amounts.py"

    if not script_path.exists():
        return {"success": False, "message": f"Skill 脚本未找到: {script_path}"}

    # 通过 subprocess 调用 Skill 脚本：stdin JSON → stdout JSON
    input_json = json.dumps({"records": records}, ensure_ascii=False, default=str)

    try:
        proc = subprocess.run(
            ["python", str(script_path)],
            input=input_json,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        return {"success": False, "message": "calculate_amounts 脚本执行超时"}
    except Exception as e:
        return {"success": False, "message": f"子进程调用失败: {e}"}

    if proc.returncode != 0:
        return {
            "success": False,
            "message": f"脚本执行失败 (exit {proc.returncode}): {proc.stderr[:500]}",
        }

    try:
        result = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"success": False, "message": f"脚本输出不是合法 JSON: {proc.stdout[:300]}"}

    if not result.get("success"):
        return result

    # 存入上下文
    context["enriched_records"] = result["enriched_records"]

    return {
        "success": True,
        "record_count": len(records),
        "amount_by_person": result["amount_by_person"],
        "detail_by_person": result["detail_by_person"],
        "attachment_count": len(result["attachment_summary"].split("\n")) if result["attachment_summary"] else 0,
    }


def _handle_load_template(args: dict, context: dict) -> dict:
    """从 Skill 目录读取模板文件，存入 context['template']。"""
    name = args.get("template_name", "default")
    skill_dir = _get_skill_dir()
    template_dir = skill_dir / "templates"

    md_path = template_dir / f"{name}.md"
    if md_path.exists():
        content = md_path.read_text(encoding="utf-8")
        context["template"] = content
        return {
            "success": True,
            "template_name": name,
            "template_length": len(content),
            "template_preview": content[:200] + "..." if len(content) > 200 else content,
        }

    available = [f.stem for f in template_dir.glob("*.md")] if template_dir.exists() else []
    return {
        "success": False,
        "message": f"模板 '{name}' 不存在",
        "available_templates": available,
    }


def _render_with_regex(template: str, records: list) -> str:
    """正则替换渲染（备选方案）。修复：未匹配变量替换为空串而非保留原样。"""
    import re

    aggregate_fields = {"转卡汇总", "附件汇总", "记录ID映射"}

    def render_section(content, record):
        def replace_var(m):
            key = m.group(1).strip()
            val = record.get(key)
            if val is None or val == "":
                return "" if key in aggregate_fields else m.group(0)
            return str(val)
        return re.sub(r"\{\{(.+?)\}\}", replace_var, content)

    # 解析区段
    sections = []
    remaining = template
    while remaining:
        marker = re.search(r"^(===.+?===)\s*$", remaining, re.MULTILINE)
        if not marker:
            if remaining.strip():
                sections.append({"type": "once", "content": remaining.strip()})
            break
        idx = remaining.index(marker.group(0))
        before = remaining[:idx].strip()
        if before:
            sections.append({"type": "once", "content": before})
        remaining = remaining[idx + len(marker.group(0)):]
        divider = re.search(r"^======\s*$", remaining, re.MULTILINE)
        if not divider:
            if remaining.strip():
                sections.append({"type": "repeat", "content": remaining.strip()})
            break
        div_idx = remaining.index(divider.group(0))
        repeat_content = remaining[:div_idx].strip()
        if repeat_content:
            sections.append({"type": "repeat", "content": repeat_content})
        remaining = remaining[div_idx + len(divider.group(0)):]

    # 渲染
    first = records[0]
    parts = []
    for section in sections:
        if section["type"] == "once":
            parts.append(render_section(section["content"], first))
        else:
            parts.append(
                f"\n以下对 {len(records)} 张发票依次执行，严格按顺序逐一处理，每张只操作一次：\n"
            )
            expanded = []
            for i, rec in enumerate(records):
                label = (
                    f"【第 {i+1}/{len(records)} 张: "
                    f"{rec.get('姓名/公司', '')} "
                    f"¥{rec.get('金额', '')}"
                    f"{' 票号' + rec['发票号码'] if rec.get('发票号码') else ''}】"
                )
                expanded.append(f"{label}\n{render_section(section['content'], rec)}")
            parts.append("\n\n".join(expanded))

    return "\n\n".join(parts)


def _render_with_llm(template: str, records: list, context: dict) -> str:
    """LLM 智能渲染：将模板 + 记录交给 LLM，生成连贯的浏览器操作指令。"""
    from invoice_toolkit.llm_client import LLMClient

    llm = LLMClient()

    records_json = json.dumps(records, ensure_ascii=False, default=str)

    system_prompt = """你是一个模板渲染引擎。你的任务是将提示词模板和报销记录数据合并，
生成最终的浏览器操作指令。

规则：
1. 将模板中的 {{变量名}} 替换为对应记录中的实际值
2. 如果某个变量在记录中不存在或为空，根据上下文智能处理（省略该句或用合理默认值）
3. ===每张发票重复执行=== 和 ====== 之间的内容需要为每条记录展开一次
4. 展开时为每条记录添加标签：【第 N/总数 张: 姓名 ¥金额 票号XXX】
5. 保持指令的连贯性和可执行性
6. 只输出最终的操作指令，不要输出任何解释或额外文字"""

    user_message = f"""请渲染以下模板：

## 模板
{template}

## 记录数据（共 {len(records)} 条）
{records_json}

请输出渲染后的完整浏览器操作指令。"""

    response = llm.chat(system_prompt, user_message)

    if isinstance(response, str):
        rendered = response.strip()
    else:
        rendered = (response.get("content") or "").strip()
    if not rendered:
        raise ValueError("LLM 返回空内容")

    return rendered


def _handle_render_template(args: dict, context: dict) -> dict:
    """渲染模板：默认 LLM 智能渲染，失败时回退正则替换。
    结果存入 context['rendered_instruction']。"""

    template = context.get("template")
    records = context.get("enriched_records")

    if not template:
        return {"success": False, "message": "请先调用 load_template 加载模板"}
    if not records:
        return {"success": False, "message": "请先调用 calculate_amounts 计算金额"}

    method = args.get("method", "llm")
    used_method = method

    if method == "llm":
        try:
            rendered = _render_with_llm(template, records, context)
            used_method = "llm"
            logger.info("LLM 渲染成功，指令长度: %d 字符", len(rendered))
        except Exception as e:
            logger.warning("LLM 渲染失败，回退到正则替换: %s", e)
            rendered = _render_with_regex(template, records)
            used_method = "regex_fallback"
    else:
        rendered = _render_with_regex(template, records)
        used_method = "regex"

    # 存入上下文
    context["rendered_instruction"] = rendered

    logger.info("渲染完成（方式: %s），指令长度: %d 字符", used_method, len(rendered))
    logger.info("渲染完成，完整指令:\n%s", rendered)

    return {
        "success": True,
        "method": used_method,
        "rendered_length": len(rendered),
        "rendered_preview": rendered[:300] + "..." if len(rendered) > 300 else rendered,
    }


def _handle_done(args: dict, context: dict) -> dict:
    """标记完成。"""
    return {
        "success": True,
        "done": True,
        "summary": args.get("summary", "指令生成完成"),
    }


# 工具名 → 处理函数映射
TOOL_HANDLERS = {
    "read_records": _handle_read_records,
    "calculate_amounts": _handle_calculate_amounts,
    "load_template": _handle_load_template,
    "render_template": _handle_render_template,
    "done": _handle_done,
}


# ─── Agent 主循环 ───────────────────────────────────────────

_MAX_RESULT_CHARS = 8000


def _truncate_tool_result(result: dict) -> dict:
    """截断大型工具结果，防止撑爆 LLM 上下文窗口。"""
    truncated = dict(result)

    for key in ("enriched_records", "records"):
        if key in truncated and isinstance(truncated[key], list):
            full = truncated[key]
            if len(full) > 3:
                truncated[key] = full[:3]
                truncated[f"_{key}_truncated"] = True
                truncated[f"_{key}_total"] = len(full)

    if "rendered" in truncated and isinstance(truncated["rendered"], str):
        if len(truncated["rendered"]) > _MAX_RESULT_CHARS:
            truncated["rendered"] = truncated["rendered"][:_MAX_RESULT_CHARS] + "\n...(已截断)"
            truncated["_rendered_truncated"] = True

    return truncated


def build_system_prompt(target_url: str, memory_context: str = "") -> str:
    """构建 Agent 的系统提示词。"""

    skill_md_path = _get_skill_dir() / "SKILL.md"
    skill_knowledge = ""
    if skill_md_path.exists():
        skill_knowledge = skill_md_path.read_text(encoding="utf-8")

    tools_desc = "\n".join(
        f"  - {t['name']}: {t['description']}"
        for t in TOOL_DEFINITIONS
    )

    base = f"""你是一个报销系统指令生成代理。你的任务是根据用户选择的报销记录，
自主完成从数据读取、金额计算、模板渲染的完整流程，最终生成浏览器操作指令。

重要：你只负责生成浏览器操作指令，不负责执行浏览器操作。
浏览器自动化由外部的 BrowserAgent 独立执行，与你的编排是先后关系。

目标报销系统网址: {target_url}

## 你的工具

你可以通过 function call 调用以下工具，每次只调用一个，根据返回结果决定下一步：

{tools_desc}

## 推荐的工作流程

1. 调用 read_records 获取完整的记录数据（含发票关联信息）
2. 调用 calculate_amounts 计算转卡金额、去重附件
3. 调用 load_template 加载提示词模板
4. 调用 render_template 将模板 + 记录渲染为浏览器指令
5. 调用 done 标记完成（渲染结果将由外部浏览器代理执行）

但你可以根据实际情况调整顺序。例如：
- 如果记录数据已经在上下文中，可以跳过 read_records
- 如果发现记录类别不一致，可以先提醒用户再继续
- 如果渲染结果有问题，可以重新渲染

## 背景知识

{skill_knowledge}

## 重要规则

1. 每次只调用一个工具
2. 仔细检查每个工具的返回结果，确认成功后再进行下一步
3. 如果某步失败，尝试诊断原因并决定是重试还是中止
4. 附件必须按路径去重——这由 calculate_amounts 自动处理
5. 同一报销单中的记录应属于同一类别
6. 你不负责执行浏览器操作，render_template 完成后调用 done 即可
"""

    if memory_context:
        return base + f"\n\n## 历史记忆（来自记忆系统）\n\n{memory_context}\n"
    return base


async def run_agent_reimbursement(
    record_ids: List[int],
    target_url: str,
    settings: Any,
    *,
    max_steps: int = 10,
    on_step: Optional[callable] = None,
    memory_context: str = "",
) -> Dict[str, Any]:
    """LLM 自主编排的报销指令生成流程。

    注意：本函数只负责生成浏览器操作指令（rendered_instruction），
    不负责执行浏览器操作。浏览器执行由调用方在拿到结果后单独发起。

    Args:
        record_ids: 选中记录的 db_id 列表
        target_url: 目标报销系统网址
        settings: 应用配置
        max_steps: 最大工具调用轮数（防止无限循环）
        on_step: 每步回调 (step_num, tool_name, tool_result) → None
        memory_context: 记忆系统提供的上下文文本（注入 system prompt）

    Returns:
        dict: {
            "success": bool,
            "steps": [{"tool": str, "args": dict, "result": dict}, ...],
            "summary": str,
            "rendered_instruction": str  # 渲染后的浏览器操作指令
        }
    """
    from invoice_toolkit.llm_client import LLMClient

    llm = LLMClient()
    context = {
        "settings": settings,
        "record_ids": record_ids,
        "target_url": target_url,
    }

    system_prompt = build_system_prompt(target_url, memory_context=memory_context)

    user_message = (
        f"请为以下报销记录生成浏览器操作指令。\n"
        f"选中的记录 ID: {record_ids}\n"
        f"目标网址: {target_url}\n"
        f"请开始。"
    )

    llm_tools = [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["parameters"],
            },
        }
        for t in TOOL_DEFINITIONS
    ]

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]

    steps = []

    for step_num in range(max_steps):
        # 调用 LLM
        try:
            response = llm.chat_with_tools(
                messages=messages,
                tools=llm_tools,
            )
        except Exception as e:
            logger.error("LLM 调用失败 (step %d): %s", step_num, e)
            return {
                "success": False,
                "steps": steps,
                "summary": f"LLM 调用失败: {e}",
                "rendered_instruction": context.get("rendered_instruction", ""),
            }

        # 如果 LLM 没有调用工具（纯文本回复）
        if not response.get("tool_calls"):
            text = response.get("content", "")
            logger.info("Agent step %d: LLM 纯文本回复: %s", step_num, text[:200])
            steps.append({"tool": "_text", "args": {}, "result": {"text": text}})
            has_done = any(s["tool"] == "done" for s in steps)
            if has_done:
                return {
                    "success": True,
                    "steps": steps,
                    "summary": text or "指令生成完成",
                    "rendered_instruction": context.get("rendered_instruction", ""),
                }
            messages.append({"role": "assistant", "content": text})
            continue

        # 处理工具调用
        tool_call = response["tool_calls"][0]
        tool_name = tool_call["function"]["name"]
        try:
            tool_args = json.loads(tool_call["function"]["arguments"])
        except json.JSONDecodeError:
            tool_args = {}

        logger.info("Agent step %d: 调用工具 %s(%s)", step_num, tool_name,
                     json.dumps(tool_args, ensure_ascii=False)[:200])

        # 执行工具
        handler = TOOL_HANDLERS.get(tool_name)
        if not handler:
            tool_result = {"success": False, "message": f"未知工具: {tool_name}"}
        else:
            try:
                import asyncio
                if asyncio.iscoroutinefunction(handler):
                    tool_result = await handler(tool_args, context)
                else:
                    tool_result = handler(tool_args, context)
            except Exception as e:
                logger.error("工具 %s 执行失败: %s", tool_name, e, exc_info=True)
                tool_result = {"success": False, "message": f"工具执行失败: {e}"}

        step_info = {
            "tool": tool_name,
            "args": tool_args,
            "result": tool_result,
        }
        steps.append(step_info)

        if on_step:
            try:
                on_step(step_num, tool_name, tool_result)
            except Exception:
                pass

        # 将工具调用和结果加入对话历史
        messages.append({
            "role": "assistant",
            "content": None,
            "tool_calls": [tool_call],
        })
        messages.append({
            "role": "tool",
            "tool_call_id": tool_call.get("id", f"call_{step_num}"),
            "content": json.dumps(tool_result, ensure_ascii=False, default=str),
        })

        # 如果是 done 工具，结束循环
        if tool_name == "done":
            return {
                "success": True,
                "steps": steps,
                "summary": tool_result.get("summary", "指令生成完成"),
                "rendered_instruction": context.get("rendered_instruction", ""),
            }

    # 超出最大步数
    return {
        "success": False,
        "steps": steps,
        "summary": f"超出最大步数限制 ({max_steps})",
        "rendered_instruction": context.get("rendered_instruction", ""),
    }
