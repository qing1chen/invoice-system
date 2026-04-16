"""
附件完整性检查 — Python 调用桥接

架构说明：
    真正的编排逻辑在 SKILL.md 中，由 Claude（宿主 LLM）直接执行。
    本文件只在"调用方是 Python 程序而非 Claude 对话"时才需要。

    它做的事情极其简单：
    1. 把 SKILL.md + rules.md + tools.md 拼成 system prompt
    2. 发一条 user message 给 LLM API
    3. LLM 回复要调工具 → 转发给 tools.py → 结果喂回
    4. LLM 回复 finish_check → 返回结果

    这不是"编排代码"，这是"传话筒"。所有决策都在 SKILL.md 指导下
    由 LLM 完成，本文件不含任何业务逻辑。

公共接口（与 v4 兼容）：
    - AttachmentChecker(settings).check_all()       → Dict[str, List[Dict]]
    - AttachmentChecker(settings).check_category(cat) → List[Dict]
    - AttachmentChecker(settings).save_report(report)
"""

from __future__ import annotations

import importlib.util
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ── 日志辅助 ──

def _estimate_tokens(msgs: list) -> int:
    """粗略估算消息列表的 token 数（中英混合按 ~3 chars/token）。"""
    total_chars = 0
    for m in msgs:
        content = m.get("content", "")
        if isinstance(content, str):
            total_chars += len(content)
        elif isinstance(content, list):
            total_chars += sum(len(json.dumps(x, default=str)) for x in content)
        for tc in m.get("tool_calls", []):
            total_chars += len(tc.get("function", {}).get("arguments", ""))
    return total_chars // 3


def _truncate_for_log(text: str, max_len: int = 500) -> str:
    """截断过长文本用于日志显示。"""
    if not text or len(text) <= max_len:
        return text or ""
    return text[:max_len] + f"...[+{len(text) - max_len} chars]"


def _roles_tail(msgs: list, n: int = 8) -> str:
    """返回最近 n 条消息的角色缩写链，如 S→U→A→T→T→A。"""
    abbr = {"system": "S", "user": "U", "assistant": "A", "tool": "T"}
    return "→".join(abbr.get(m["role"], "?") for m in msgs[-n:])


def _locate_skill_dir(settings) -> Path:
    configured = getattr(settings.paths, "attachment_skill_dir", None)
    return Path(configured) if configured else Path(__file__).resolve().parents[1]


def _load_tools(skill_dir: Path):
    path = skill_dir / "scripts" / "tools.py"
    if not path.exists():
        raise FileNotFoundError(f"tools.py not found: {path}")
    spec = importlib.util.spec_from_file_location("_tools", str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _read_if_exists(path: Path) -> str:
    return path.read_text("utf-8") if path.exists() else ""


class AttachmentChecker:
    """Python 调用桥接。把 SKILL.md 喂给 LLM，转发工具调用，收集结果。"""

    MAX_TURNS = 200

    def __init__(self, settings=None, llm_client=None, *,
                 skill_dir: Optional[Path] = None):
        from invoice_toolkit.config import Settings
        self._settings = settings or Settings.from_env()
        self._skill_dir = Path(skill_dir) if skill_dir else _locate_skill_dir(self._settings)
        self._tools = _load_tools(self._skill_dir)

        from invoice_toolkit.llm_client import LLMClient
        self._llm: LLMClient = llm_client or LLMClient(self._settings.llm)
        logger.info("[INIT] skill_dir=%s", self._skill_dir)

    # ── system prompt = SKILL.md 原文 ──

    def _build_prompt(self) -> str:
        refs = self._skill_dir / "references"
        parts = [
            ("SKILL.md",   _read_if_exists(self._skill_dir / "SKILL.md")),
            ("rules.md",   _read_if_exists(refs / "rules.md")),
            ("tools.md",   _read_if_exists(refs / "tools.md")),
        ]
        for name, text in parts:
            logger.info("[PROMPT] %-12s %6d chars", name, len(text))

        prompt = "\n\n---\n\n".join(filter(None, [
            p[1] for p in parts
        ] + [
            "## 约束\n"
            "1. 完成后必须调 finish_check（传 summary + all_results）。\n"
            "2. 工具出错不中断，记录后继续。\n"
            "3. 不需要 cd，直接调工具名。",
        ]))
        logger.info("[PROMPT] total system prompt: %d chars, ~%d tokens",
                     len(prompt), len(prompt) // 3)
        return prompt

    # ── 工具路由：纯转发 ──

    def _exec(self, name: str, args: dict) -> Any:
        t, s = self._tools, self._settings
        router = {
            "get_config":                lambda: t.get_config(s),
            "get_ocr_names":             lambda: t.get_ocr_names(s),
            "collect_files":             lambda: t.collect_files(s, args["category"]),
            "collect_source_candidates": lambda: t.collect_source_candidates(s, args["person"]),
            "lookup_invoice_details":    lambda: t.lookup_invoice_details(s, args["filename"]),
            "extract_attachment_text":   lambda: t.extract_attachment_text(s, args["filepath"]),
            "copy_file":                 lambda: t.copy_file(args["src"], args["dst_dir"], args.get("mark_used", True)),
            "generate_meal_doc":         lambda: t.generate_meal_doc(
                s, args["person"], args["amount"], args.get("seller", ""),
                args.get("commodity", ""), args.get("invoice_filename", ""),
                args.get("name_list", s.NAME_LIST), args.get("reason")),
            "fix_meal_doc":              lambda: t.fix_meal_doc(
                s, args["original_path"], args.get("invoice_filename", ""),
                args["person"], args["amount"], args.get("target_persons", []),
                args.get("required_count", 1), args.get("reason_text")),
            "merge_meal_docs":           lambda: t.merge_meal_docs(s, args["generated_files"]),
            "save_attachment_report":    lambda: t.save_attachment_report(s, args["results"]),
            "backup_file":              lambda: t.backup_file(s, args["filepath"], args.get("delete_original", True)),
            "finish_check":              lambda: {"status": "completed"},
        }
        handler = router.get(name)
        if not handler:
            return {"error": f"unknown tool: {name}"}
        try:
            return handler()
        except Exception as e:
            logger.error("[TOOL_ERROR] %s → %s: %s", name,
                         type(e).__name__, e, exc_info=True)
            return {"error": str(e)}

    # ── 工具声明（LLM API 需要的最小 schema）──

    @staticmethod
    def _tool_defs():
        def _f(name, desc, props=None, req=None):
            return {"type": "function", "function": {
                "name": name, "description": desc,
                "parameters": {"type": "object",
                               "properties": props or {},
                               "required": req or []}}}
        return [
            _f("get_config", "获取系统配置"),
            _f("get_ocr_names", "获取 OCR 识别名单"),
            _f("collect_files", "收集类别目录文件",
               {"category": {"type": "string"}}, ["category"]),
            _f("collect_source_candidates", "收集来源目录候选附件",
               {"person": {"type": "string"}}, ["person"]),
            _f("lookup_invoice_details", "查询发票 OCR 详情",
               {"filename": {"type": "string"}}, ["filename"]),
            _f("extract_attachment_text", "OCR 提取附件文字",
               {"filepath": {"type": "string"}}, ["filepath"]),
            _f("copy_file", "复制文件",
               {"src": {"type": "string"}, "dst_dir": {"type": "string"},
                "mark_used": {"type": "boolean"}}, ["src", "dst_dir"]),
            _f("generate_meal_doc", "生成加班餐情况说明",
               {"person": {"type": "string"}, "amount": {"type": "number"},
                "seller": {"type": "string"}, "commodity": {"type": "string"},
                "invoice_filename": {"type": "string"},
                "reason": {"type": "string"}}, ["person", "amount"]),
            _f("fix_meal_doc", "修复加班餐情况说明",
               {"original_path": {"type": "string"}, "person": {"type": "string"},
                "amount": {"type": "number"},
                "target_persons": {"type": "array", "items": {"type": "string"}},
                "required_count": {"type": "integer"},
                "invoice_filename": {"type": "string"},
                "reason_text": {"type": "string"}}, ["original_path", "person", "amount"]),
            _f("merge_meal_docs", "合并加班餐说明文件",
               {"generated_files": {"type": "array", "items": {"type": "string"}}},
               ["generated_files"]),
            _f("save_attachment_report", "写入数据库",
               {"results": {"type": "array", "items": {"type": "object"}}},
               ["results"]),
            _f("backup_file", "备份文件",
               {"filepath": {"type": "string"},
                "delete_original": {"type": "boolean"}}, ["filepath"]),
            _f("finish_check", "检查完成，提交摘要和全部结果",
               {"summary": {"type": "object"},
                "all_results": {"type": "array", "items": {"type": "object"}}},
               ["summary", "all_results"]),
        ]

    # ── 传话循环 ──

    def _relay(self, user_msg: str) -> dict:
        """发消息给 LLM，转发工具调用，收集结果。不含任何业务逻辑。"""
        msgs = [
            {"role": "system", "content": self._build_prompt()},
            {"role": "user", "content": user_msg},
        ]
        defs = self._tool_defs()
        result = None

        logger.info("=" * 60)
        logger.info("[RELAY_START] %s", _truncate_for_log(user_msg, 200))
        logger.info("=" * 60)

        for turn in range(self.MAX_TURNS):
            est_tokens = _estimate_tokens(msgs)
            logger.info(
                "[TURN %03d] msgs=%d, ~%d tokens, tail=[%s]",
                turn, len(msgs), est_tokens, _roles_tail(msgs)
            )

            # 打印最新一条消息的摘要
            last = msgs[-1]
            last_content = last.get("content", "")
            if isinstance(last_content, str):
                logger.debug("[TURN %03d] last(%s): %s",
                             turn, last["role"],
                             _truncate_for_log(last_content, 300))
            elif isinstance(last_content, list):
                logger.debug("[TURN %03d] last(%s): %d blocks",
                             turn, last["role"], len(last_content))

            # ── 调 LLM ──
            try:
                resp = self._llm.chat_with_tools(msgs, defs)
            except Exception as e:
                logger.error("[TURN %03d] LLM API error: %s", turn, e)
                if "token" in str(e).lower() or "context" in str(e).lower():
                    before_len = len(msgs)
                    before_tokens = est_tokens
                    msgs = msgs[:1] + msgs[-20:]
                    after_tokens = _estimate_tokens(msgs)
                    logger.warning(
                        "[TURN %03d] ⚠ CONTEXT OVERFLOW — trimmed: "
                        "msgs %d→%d, ~tokens %d→%d",
                        turn, before_len, len(msgs),
                        before_tokens, after_tokens
                    )
                    continue
                break

            content = resp.get("content") or ""
            calls = resp.get("tool_calls") or []

            # ── 日志：LLM 回复概要 ──
            call_names = [tc.get("function", {}).get("name", "?")
                          for tc in calls]
            logger.info(
                "[TURN %03d] LLM → content=%d chars, tools=%d%s",
                turn, len(content), len(calls),
                f" [{', '.join(call_names)}]" if calls else ""
            )
            if content:
                logger.debug("[TURN %03d] LLM text: %s",
                             turn, _truncate_for_log(content, 400))

            # ── 无工具调用 ──
            if not calls:
                if result:
                    logger.info(
                        "[TURN %03d] ✓ No more calls & result ready → exit",
                        turn)
                    break
                msgs.append({"role": "assistant", "content": content})
                msgs.append({"role": "user",
                              "content": "请继续，完成后调 finish_check。"})
                logger.info(
                    "[TURN %03d] No calls, no result → nudge", turn)
                continue

            # ── 执行工具 ──
            msgs.append({"role": "assistant", "content": content,
                          "tool_calls": calls})

            for tc in calls:
                cid = tc.get("id", "")
                fn = tc.get("function", {})
                name = fn.get("name", "")
                try:
                    args = json.loads(fn.get("arguments", "{}"))
                except json.JSONDecodeError:
                    args = {}

                logger.info("[TURN %03d] → %s(%s)",
                            turn, name,
                            _truncate_for_log(
                                json.dumps(args, ensure_ascii=False), 300))

                out = self._exec(name, args)

                out_str = json.dumps(out, ensure_ascii=False, default=str)
                is_err = isinstance(out, dict) and "error" in out
                logger.info("[TURN %03d] ← %s: %d chars%s",
                            turn, name, len(out_str),
                            " ✗ ERROR" if is_err else " ✓")
                logger.debug("[TURN %03d] ← %s: %s",
                             turn, name,
                             _truncate_for_log(out_str, 500))

                if name == "finish_check":
                    result = {
                        "summary": args.get("summary", {}),
                        "all_results": args.get("all_results", []),
                    }
                    logger.info(
                        "[TURN %03d] ★ finish_check: summary=%s, "
                        "results=%d",
                        turn,
                        _truncate_for_log(json.dumps(
                            result["summary"], ensure_ascii=False), 200),
                        len(result["all_results"])
                    )

                rj = out_str[:8000]
                if len(out_str) > 8000:
                    logger.warning(
                        "[TURN %03d] ⚠ Result truncated: %s %d→8000",
                        turn, name, len(out_str)
                    )
                msgs.append({"role": "tool", "tool_call_id": cid,
                              "content": rj})

            if result:
                logger.info("[TURN %03d] ✓ Result ready → exit", turn)
                break
        else:
            logger.error(
                "[RELAY] ✗ Exhausted MAX_TURNS=%d without finish_check!",
                self.MAX_TURNS
            )

        # ── 结束汇总 ──
        final_turn = min(turn + 1, self.MAX_TURNS)
        if result:
            logger.info(
                "[RELAY_END] ✓ Done in %d turns, %d results, "
                "final context: %d msgs ~%d tokens",
                final_turn, len(result.get("all_results", [])),
                len(msgs), _estimate_tokens(msgs)
            )
        else:
            logger.warning(
                "[RELAY_END] ✗ Fallback after %d turns, "
                "final context: %d msgs ~%d tokens",
                final_turn, len(msgs), _estimate_tokens(msgs)
            )

        return result or self._fallback(msgs)

    # ── 公共接口 ──

    def check_all(self) -> Dict[str, List[Dict[str, Any]]]:
        logger.info("[CHECK_ALL] ▶ Starting full check")
        report = self._to_report(
            self._relay(
                "请检查所有类别的发票附件完整性。按 SKILL.md 步骤执行。"))
        logger.info("[CHECK_ALL] ◀ Done: %d categories, %d total items",
                    len(report), sum(len(v) for v in report.values()))
        return report

    def check_category(self, category: str) -> List[Dict[str, Any]]:
        logger.info("[CHECK_CAT] ▶ category=%s", category)
        items = self._to_report(
            self._relay(f"请仅检查【{category}】类别的附件完整性。")
        ).get(category, [])
        logger.info("[CHECK_CAT] ◀ %s → %d items", category, len(items))
        return items

    def save_report(self, report: Dict[str, List[Dict]]):
        rows = [{"旧文件名": it.get("发票文件", ""), "附件状态": it.get("状态", ""),
                 "缺少类型": it.get("缺少类型", ""), "匹配附件": it.get("匹配附件", ""),
                 "附件路径": it.get("附件路径", ""), "生成文件": it.get("生成文件", ""),
                 "校验详情": it.get("校验详情", ""), "附件类别": cat}
                for cat, items in report.items() for it in items]
        if rows:
            logger.info("[SAVE_REPORT] Saving %d rows", len(rows))
            self._tools.save_attachment_report(self._settings, rows)

    # ── 辅助 ──

    @staticmethod
    def _to_report(r: dict) -> Dict[str, List[Dict]]:
        out: Dict[str, List[Dict]] = {}
        for x in r.get("all_results", []):
            cat = x.get("附件类别", "未分类")
            out.setdefault(cat, []).append({
                "发票文件": x.get("旧文件名", ""),
                "所属人员": x.get("姓名/公司", ""),
                "来源路径": "",
                "状态": x.get("附件状态", ""),
                "缺少类型": x.get("缺少类型", ""),
                "匹配附件": x.get("匹配附件", ""),
                "生成文件": x.get("生成文件", ""),
                "校验详情": x.get("校验详情", ""),
            })
        return out

    @staticmethod
    def _fallback(msgs: list) -> dict:
        logger.warning(
            "[FALLBACK] Scanning history for save_attachment_report calls")
        results = []
        for m in reversed(msgs):
            if m.get("role") != "assistant":
                continue
            for tc in m.get("tool_calls", []):
                if tc.get("function", {}).get("name") \
                        == "save_attachment_report":
                    try:
                        a = json.loads(
                            tc["function"].get("arguments", "{}"))
                        results.extend(a.get("results", []))
                    except (json.JSONDecodeError, TypeError):
                        pass
        logger.warning("[FALLBACK] Recovered %d results from history",
                       len(results))
        return {"summary": {}, "all_results": results}