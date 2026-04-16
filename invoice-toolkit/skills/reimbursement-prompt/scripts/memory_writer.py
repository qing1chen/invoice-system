"""
报销 Agent 记忆提取与写入引擎

从 BrowserAgent 执行历史中提取记忆，写入文件系统。
类似 Claude Code 的 "Auto Memory" + browser-use 的 "long_term_memory" 机制。

纯计算模块，零外部依赖（仅 Python 标准库）。

两种调用方式：
  1. Python import: from memory_writer import MemoryWriter
  2. Bash CLI:      echo '{"base_dir":"./memory", "session": {...}}' | python memory_writer.py
                    → stdout 输出 JSON 结果

功能：
  1. 从执行历史中提取动作序列 → 生成/更新 flow
  2. 识别可复用操作片段 → 生成 fragment
  3. 提取 UI 元素发现 → 更新 semantic/ui-map.md
  4. 提取错误模式 → 更新 episodic/errors
  5. 生成 session 摘要 → 写入 episodic/sessions
  6. 更新置信度
  7. 重建 MEMORY.md 索引
"""

import json
import sys
import re
import hashlib
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from collections import Counter

try:
    from .memory_store import MemoryStore, compute_confidence, execution_mode
except ImportError:
    from memory_store import MemoryStore, compute_confidence, execution_mode


# ─── 步骤数据结构约定 ───────────────────────────────────────
#
# BrowserAgent 的每一步（step）应包含以下字段：
#
# {
#   "step":         int,       # 步骤序号
#   "action":       str,       # 动作类型: click/fill/select/upload_file/scroll/wait/...
#   "target":       str|None,  # 操作目标描述（如「智能报销」按钮）
#   "index":        int|None,  # DOM 元素索引
#   "selector":     str|None,  # CSS 选择器（js_click 时使用）
#   "value":        str|None,  # 填写值/文件路径/URL/等待秒数/skip原因/done说明
#   "result":       str,       # "success" | "fail"
#   "error":        str|None,  # 失败原因
#   "page_url":     str|None,  # 当前页面 URL
#   "page_title":   str|None,  # 当前页面标题
#   "duration_sec": float,     # 本步耗时
#   "memory":       str|None,  # agent 自生成的步骤摘要（browser-use 风格）
#   "new_elements":  list|None, # 本步新出现的 DOM 元素
#   "modal_detected": bool,    # 是否检测到模态弹窗
# }
#
# session 元数据：
# {
#   "session_id":   str,
#   "category":     str,       # 报销类别
#   "record_ids":   list[int], # 处理的记录 ID 列表
#   "task":         str,       # 原始任务文本
#   "url":          str,       # 起始 URL
#   "flow_used":    str|None,  # 使用的 flow_id（如果有）
#   "mode":         str,       # exploration | execution | replay
# }


class MemoryWriter:
    """从 BrowserAgent 执行历史提取记忆并写入。"""

    def __init__(self, base_dir: str):
        self.store = MemoryStore(base_dir)
        self.base_dir = base_dir

    def process_session(
        self,
        session_meta: Dict[str, Any],
        steps: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """处理一次完整的 agent 执行会话，提取全部记忆。

        Args:
            session_meta: 会话元数据（session_id, category, record_ids, ...）
            steps:        执行步骤列表

        Returns:
            处理结果汇总
        """
        results: Dict[str, Any] = {"session_id": session_meta.get("session_id")}

        # 0. 基础统计
        stats = self._compute_stats(steps)
        results["stats"] = stats

        # 1. 生成 episode（情景记忆）
        episode_result = self._write_episode(session_meta, steps, stats)
        results["episode"] = episode_result

        # 2. 提取/更新 flow（程序性记忆 — 完整流程）
        if stats["outcome"] in ("success", "partial_success"):
            flow_result = self._extract_and_write_flow(session_meta, steps)
            results["flow"] = flow_result
        else:
            results["flow"] = {"skipped": True, "reason": "任务失败，不提取 flow"}

        # 3. 识别/更新 fragments（程序性记忆 — 可复用片段）
        frag_result = self._extract_and_write_fragments(steps)
        results["fragments"] = frag_result

        # 4. 提取 UI 知识（语义记忆）
        ui_result = self._extract_ui_knowledge(steps)
        results["ui_knowledge"] = ui_result

        # 5. 提取错误模式（情景记忆 — 错误汇总）
        err_result = self._extract_errors(steps)
        results["errors"] = err_result

        # 6. 更新置信度
        conf_result = self._update_confidences(session_meta, stats)
        results["confidence"] = conf_result

        # 7. 重建索引
        idx_result = self.store.rebuild_index()
        results["index"] = idx_result

        results["success"] = True
        return results

    # ── 统计分析 ────────────────────────────────────────

    def _compute_stats(self, steps: List[Dict]) -> Dict[str, Any]:
        """从执行步骤中计算基础统计。"""
        total = len(steps)
        succeeded = sum(1 for s in steps if s.get("result") == "success")
        failed = sum(1 for s in steps if s.get("result") == "fail")
        skipped = sum(1 for s in steps if s.get("action") == "skip")
        duration = sum(s.get("duration_sec", 0) for s in steps)

        # 判断 outcome
        done_steps = [s for s in steps if s.get("action") == "done"]
        if done_steps:
            last_done = done_steps[-1]
            # 如果 agent 自己报告了 success
            if "成功" in (last_done.get("value") or "") or \
               "完成" in (last_done.get("value") or ""):
                outcome = "success"
            else:
                outcome = "partial_success"
        elif failed > total * 0.5:
            outcome = "failure"
        elif skipped > 0 and succeeded > 0:
            outcome = "partial_success"
        elif succeeded > 0:
            outcome = "success"
        else:
            outcome = "failure"

        return {
            "outcome": outcome,
            "steps_total": total,
            "steps_succeeded": succeeded,
            "steps_failed": failed,
            "steps_skipped": skipped,
            "duration_sec": round(duration, 2),
            "action_counts": dict(Counter(
                s.get("action", "?") for s in steps
            )),
        }

    # ── 情景记忆写入 ────────────────────────────────────

    def _write_episode(
        self,
        meta: Dict,
        steps: List[Dict],
        stats: Dict,
    ) -> Dict[str, Any]:
        """生成并写入 session 情景记忆。"""
        # 关键事件：失败的步骤 + skip 步骤 + 耗时较长的步骤
        key_events = []
        discoveries = []
        memory_updates = []

        for s in steps:
            step_num = s.get("step", "?")
            action = s.get("action", "?")
            target = s.get("target", "")
            result = s.get("result", "")
            error = s.get("error", "")
            memory_note = s.get("memory", "")

            if result == "fail":
                key_events.append(
                    f"Step {step_num}: {action} 「{target}」失败"
                    f" — {error or '原因未知'}"
                )
            elif action == "skip":
                key_events.append(
                    f"Step {step_num}: 跳过 — {s.get('value', '原因未知')}"
                )
            elif s.get("duration_sec", 0) > 10:
                key_events.append(
                    f"Step {step_num}: {action} 「{target}」"
                    f" 耗时 {s['duration_sec']:.1f}s"
                )

            # 检测新发现
            if s.get("modal_detected"):
                discoveries.append(
                    f"Step {step_num}: 检测到模态弹窗"
                )
                memory_updates.append(
                    "episodic/errors/modal-blocking.md: 补充新弹窗案例"
                )
            if s.get("new_elements"):
                discoveries.append(
                    f"Step {step_num}: 发现 {len(s['new_elements'])} 个新元素"
                )
                memory_updates.append(
                    "semantic/ui-map.md: 补充新发现的页面元素"
                )

            # agent 自己的记忆笔记
            if memory_note and len(memory_note) > 20:
                discoveries.append(f"Agent 笔记: {memory_note[:200]}")

        # 生成摘要
        category = meta.get("category", "未知")
        record_ids = meta.get("record_ids", [])
        summary = (
            f"为 {len(record_ids)} 条{category}记录执行自动填报。"
            f"共 {stats['steps_total']} 步，"
            f"{stats['steps_succeeded']} 步成功，"
            f"{stats['steps_failed']} 步失败，"
            f"{stats['steps_skipped']} 步跳过。"
            f"耗时 {stats['duration_sec']:.1f} 秒。"
            f"结果: {stats['outcome']}。"
        )

        return self.store.write_episode(
            session_id=meta.get("session_id", self._gen_id()),
            category=category,
            record_ids=record_ids,
            outcome=stats["outcome"],
            summary=summary,
            key_events=key_events[:20],
            discoveries=discoveries[:10],
            memory_updates=memory_updates[:10],
            mode=meta.get("mode", "exploration"),
            flow_used=meta.get("flow_used"),
            duration_sec=stats["duration_sec"],
            steps_total=stats["steps_total"],
            steps_succeeded=stats["steps_succeeded"],
            steps_failed=stats["steps_failed"],
        )

    # ── 程序性记忆提取：Flow ───────────────────────────

    def _extract_and_write_flow(
        self,
        meta: Dict,
        steps: List[Dict],
    ) -> Dict[str, Any]:
        """从成功的执行历史中提取完整流程。"""
        category = meta.get("category", "未知")
        flow_id = self._category_to_flow_id(category)

        # 过滤出有意义的步骤（排除 wait、纯 scroll 等辅助步骤）
        meaningful_actions = (
            "click", "fill", "js_fill", "select",
            "upload_file", "go_to_url", "done",
        )
        flow_steps = []
        step_group: List[Dict] = []

        for s in steps:
            action = s.get("action", "")

            if action in meaningful_actions and s.get("result") == "success":
                step_data = {
                    "title": self._step_title(s),
                    "action": action,
                }
                if s.get("target"):
                    step_data["target"] = s["target"]
                if s.get("value") and action not in ("done",):
                    step_data["value"] = s["value"]
                if s.get("page_url"):
                    step_data["notes"] = f"页面: {s['page_url']}"

                # 检测可以引用的 fragment
                frag_id = self._match_known_fragment(s)
                if frag_id:
                    step_data["fragment"] = f"{frag_id}.frag.md"

                flow_steps.append(step_data)

            elif action == "skip":
                flow_steps.append({
                    "title": f"跳过: {s.get('value', '?')[:50]}",
                    "action": "skip",
                    "notes": s.get("value", ""),
                    "on_skip": "记录 record_id 到跳过列表",
                })

        if not flow_steps:
            return {"skipped": True, "reason": "无有效步骤可提取"}

        return self.store.write_flow(
            flow_id=flow_id,
            category=category,
            steps=flow_steps,
            source=meta.get("mode", "exploration"),
        )

    # ── 程序性记忆提取：Fragment ────────────────────────

    def _extract_and_write_fragments(
        self,
        steps: List[Dict],
    ) -> Dict[str, Any]:
        """从执行历史中识别和提取可复用操作片段。

        识别规则：
        - 连续 2+ 步成功操作构成一个逻辑单元
        - 具有清晰的「触发动作 → 系列操作 → 完成确认」模式
        - 围绕特定 UI 交互（上传、填表、选择等）
        """
        fragments_written = []

        # 模式 1: 上传类操作（upload_file 前后的 click 序列）
        upload_frags = self._detect_upload_fragments(steps)
        for frag in upload_frags:
            result = self.store.write_fragment(**frag)
            fragments_written.append(result)

        # 模式 2: 表单填写（连续 fill/select 操作）
        form_frags = self._detect_form_fragments(steps)
        for frag in form_frags:
            result = self.store.write_fragment(**frag)
            fragments_written.append(result)

        # 模式 3: 弹窗处理（modal_detected → click 确认）
        modal_frags = self._detect_modal_fragments(steps)
        for frag in modal_frags:
            result = self.store.write_fragment(**frag)
            fragments_written.append(result)

        return {
            "fragments_detected": len(fragments_written),
            "fragments": fragments_written,
        }

    def _detect_upload_fragments(
        self, steps: List[Dict],
    ) -> List[Dict[str, Any]]:
        """检测上传类操作片段。"""
        fragments = []
        i = 0
        while i < len(steps):
            s = steps[i]
            if s.get("action") == "upload_file" and s.get("result") == "success":
                # 回溯找到触发上传的 click
                start = max(0, i - 3)
                end = min(len(steps), i + 3)
                frag_steps = []
                for j in range(start, end):
                    sj = steps[j]
                    if sj.get("result") != "success":
                        continue
                    frag_steps.append({
                        "action": sj.get("action", "?"),
                        "target": sj.get("target", ""),
                        "value": sj.get("value", ""),
                    })

                target_name = s.get("target", "文件")
                # 根据上传的是发票还是附件来命名
                if "发票" in (s.get("value") or "") or "invoice" in (
                    s.get("value") or ""
                ).lower():
                    frag_id = "upload-invoice"
                    title = "上传发票"
                else:
                    frag_id = "upload-attachment"
                    title = "上传附件"

                fragments.append({
                    "fragment_id": frag_id,
                    "title": title,
                    "steps": frag_steps,
                })
                i = end
            else:
                i += 1
        return fragments

    def _detect_form_fragments(
        self, steps: List[Dict],
    ) -> List[Dict[str, Any]]:
        """检测表单填写类操作片段。"""
        fragments = []
        form_actions = {"fill", "js_fill", "select"}

        i = 0
        while i < len(steps):
            # 找连续的表单操作
            if steps[i].get("action") in form_actions and \
               steps[i].get("result") == "success":
                start = i
                while i < len(steps) and (
                    steps[i].get("action") in form_actions
                    or (
                        steps[i].get("action") == "click"
                        and "保存" in (steps[i].get("target") or "")
                    )
                ):
                    i += 1
                end = i

                if end - start >= 2:
                    frag_steps = []
                    for j in range(start, end):
                        sj = steps[j]
                        frag_steps.append({
                            "action": sj.get("action", "?"),
                            "target": sj.get("target", ""),
                            "value": sj.get("value", ""),
                        })

                    # 用第一个字段的 target 来命名
                    first_target = steps[start].get("target", "表单")
                    frag_id = f"form-{self._safe_id(first_target)}"
                    title = f"填写{first_target}表单"

                    fragments.append({
                        "fragment_id": frag_id,
                        "title": title,
                        "steps": frag_steps,
                    })
            else:
                i += 1
        return fragments

    def _detect_modal_fragments(
        self, steps: List[Dict],
    ) -> List[Dict[str, Any]]:
        """检测弹窗处理类操作片段。"""
        fragments = []
        for i, s in enumerate(steps):
            if s.get("modal_detected") and s.get("result") == "success":
                frag_steps = [{
                    "action": s.get("action", "click"),
                    "target": s.get("target", "弹窗按钮"),
                    "value": s.get("value", ""),
                }]
                # 弹窗处理通常是单步 click
                modal_text = s.get("target") or "弹窗"
                frag_id = f"modal-{self._safe_id(modal_text)}"
                fragments.append({
                    "fragment_id": frag_id,
                    "title": f"处理弹窗: {modal_text[:30]}",
                    "steps": frag_steps,
                    "known_issues": ["弹窗出现时底层元素不可点击"],
                })
        return fragments

    # ── 语义记忆提取 ────────────────────────────────────

    def _extract_ui_knowledge(
        self, steps: List[Dict],
    ) -> Dict[str, Any]:
        """从执行历史中提取 UI 元素知识，更新 ui-map.md。"""
        # 按页面 URL 分组
        pages: Dict[str, List[Dict]] = {}
        for s in steps:
            url = s.get("page_url", "unknown")
            if url not in pages:
                pages[url] = []
            pages[url].append(s)

        entries = []
        for url, page_steps in pages.items():
            elements = []
            for s in page_steps:
                target = s.get("target", "")
                action = s.get("action", "")
                if not target or action in ("wait", "scroll", "done"):
                    continue
                # 推断元素类型
                elem_type = self._infer_element_type(action, target)
                result = s.get("result", "")
                elements.append(f"- {elem_type}「{target}」({result})")

                # 新出现的元素
                for ne in (s.get("new_elements") or []):
                    elements.append(f"- *新增* {ne}")

            if elements:
                page_title = page_steps[0].get("page_title", "未知页面")
                content = (
                    f"### {page_title}\n"
                    f"URL: `{url}`\n\n"
                    + "\n".join(elements)
                )
                entries.append({
                    "section": page_title or url[:50],
                    "content": content,
                })

        if not entries:
            return {"updated": False, "reason": "无新 UI 知识"}

        return self.store.update_semantic("ui-map.md", entries)

    # ── 错误模式提取 ────────────────────────────────────

    def _extract_errors(
        self, steps: List[Dict],
    ) -> Dict[str, Any]:
        """从失败步骤中提取错误模式。"""
        errors = []
        for s in steps:
            if s.get("result") != "fail":
                continue

            error_msg = s.get("error", "")
            action = s.get("action", "")
            target = s.get("target", "")

            # 分类错误类型
            if s.get("modal_detected") or "modal" in error_msg.lower() \
               or "遮挡" in error_msg or "弹窗" in error_msg:
                err_type = "modal-blocking"
            elif "not found" in error_msg.lower() \
                 or "找不到" in error_msg or "不存在" in error_msg:
                err_type = "element-not-found"
            elif "timeout" in error_msg.lower() or "超时" in error_msg:
                err_type = "timeout"
            elif "readonly" in error_msg.lower() or "只读" in error_msg:
                err_type = "readonly-field"
            else:
                err_type = "other"

            errors.append({
                "type": err_type,
                "context": f"{action} 「{target}」: {error_msg}",
                "resolution": self._suggest_resolution(err_type, s),
                "pattern": self._error_pattern_desc(err_type),
            })

        if not errors:
            return {"errors_found": 0}

        # 写入（store.write_episode 已经处理了错误追加）
        for err in errors:
            self.store._append_error(err)

        return {
            "errors_found": len(errors),
            "error_types": list(set(e["type"] for e in errors)),
        }

    # ── 置信度更新 ──────────────────────────────────────

    def _update_confidences(
        self, meta: Dict, stats: Dict,
    ) -> Dict[str, Any]:
        """根据执行结果更新相关记忆的置信度。"""
        updates = []

        flow_used = meta.get("flow_used")
        if flow_used:
            success = stats["outcome"] in ("success", "partial_success")
            result = self.store.update_confidence("flows", flow_used, success)
            updates.append(result)

        return {"updates": updates}

    # ── 辅助方法 ────────────────────────────────────────

    @staticmethod
    def _step_title(step: Dict) -> str:
        """为一个执行步骤生成简短标题。"""
        action = step.get("action", "?")
        target = step.get("target", "")

        titles = {
            "click": f"点击 {target}",
            "fill": f"填写 {target}",
            "js_fill": f"JS填写 {target}",
            "select": f"选择 {target}",
            "upload_file": f"上传文件到 {target}",
            "go_to_url": f"导航到 {step.get('value', '?')[:50]}",
            "scroll": f"滚动 {step.get('value', '?')}px",
            "wait": f"等待 {step.get('value', '?')}秒",
            "skip": f"跳过: {step.get('value', '?')[:30]}",
            "done": "完成",
        }
        return titles.get(action, f"{action} {target}")

    @staticmethod
    def _infer_element_type(action: str, target: str) -> str:
        """从动作类型推断 UI 元素类型。"""
        if action in ("fill", "js_fill"):
            return "输入框"
        elif action == "select":
            return "下拉框"
        elif action == "upload_file":
            return "上传控件"
        elif action in ("click", "js_click"):
            if "按钮" in target or "button" in target.lower():
                return "按钮"
            elif "链接" in target or "link" in target.lower():
                return "链接"
            elif "复选框" in target or "checkbox" in target.lower():
                return "复选框"
            return "可点击元素"
        return "元素"

    @staticmethod
    def _suggest_resolution(err_type: str, step: Dict) -> str:
        """根据错误类型建议解决方案。"""
        resolutions = {
            "modal-blocking": "先关闭弹窗（点击确定/取消/Escape），再重试操作",
            "element-not-found": "检查元素是否需要滚动可见，或等待页面加载完成",
            "timeout": "增加等待时间，或检查网络连接",
            "readonly-field": "降级使用 js_fill 动作",
            "other": "记录问题，待人工分析",
        }
        return resolutions.get(err_type, "未知")

    @staticmethod
    def _error_pattern_desc(err_type: str) -> str:
        """返回错误类型的通用模式描述。"""
        patterns = {
            "modal-blocking": "弹窗出现后，底层元素不可点击。agent 必须先处理弹窗。",
            "element-not-found": "目标元素在当前 DOM 中找不到，可能未加载或在视口外。",
            "timeout": "操作超时，通常因页面加载慢或网络问题。",
            "readonly-field": "输入框有 readonly 属性，标准 fill 动作无法写入。",
            "other": "待分析的未分类错误。",
        }
        return patterns.get(err_type, "待分析")

    @staticmethod
    def _category_to_flow_id(category: str) -> str:
        """将类别名转为 flow_id。"""
        mapping = {
            "材料": "daily-reimbursement",
            "快递": "daily-reimbursement",
            "材料、快递": "daily-reimbursement",
            "日常报销": "daily-reimbursement",
            "出差": "travel-reimbursement",
            "国内差旅": "travel-reimbursement",
            "手机通讯费": "phone-reimbursement",
            "加班餐": "overtime-meal-reimbursement",
            "试剂耗材": "reagent-reimbursement",
        }
        return mapping.get(category, f"reimbursement-{category}")

    def _match_known_fragment(self, step: Dict) -> Optional[str]:
        """检查某步骤是否匹配已知的 fragment。"""
        action = step.get("action", "")
        target = step.get("target", "")

        if action == "upload_file":
            if "发票" in (step.get("value") or ""):
                return "upload-invoice"
            return "upload-attachment"
        return None

    @staticmethod
    def _safe_id(text: str) -> str:
        """将文本转为安全的标识符片段。"""
        safe = re.sub(r"[^\w\u4e00-\u9fff]", "-", text)
        return safe.strip("-")[:30].lower()

    @staticmethod
    def _gen_id() -> str:
        """生成简短唯一 ID。"""
        now = datetime.now().strftime("%H%M%S")
        h = hashlib.md5(str(datetime.now()).encode()).hexdigest()[:4]
        return f"{now}-{h}"


# ─── CLI 入口：stdin JSON → stdout JSON ──────────────────

if __name__ == "__main__":
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        json.dump(
            {"success": False, "error": f"JSON 解析失败: {e}"},
            sys.stdout, ensure_ascii=False,
        )
        sys.exit(1)

    base_dir = payload.get("base_dir", "./memory")
    session_meta = payload.get("session", {})
    steps = payload.get("steps", [])

    if not steps:
        json.dump(
            {"success": False, "error": "steps 为空"},
            sys.stdout, ensure_ascii=False,
        )
        sys.exit(1)

    writer = MemoryWriter(base_dir)
    result = writer.process_session(session_meta, steps)
    json.dump(result, sys.stdout, ensure_ascii=False, default=str)
