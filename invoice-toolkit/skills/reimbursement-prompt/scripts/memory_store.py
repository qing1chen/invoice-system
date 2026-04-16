"""
报销 Agent 记忆存储引擎

纯文件系统记忆模块，零外部依赖（仅 Python 标准库）。

两种调用方式：
  1. Python import: from memory_store import MemoryStore
  2. Bash CLI:      echo '{"action": "init", "base_dir": "./memory"}' | python memory_store.py
                    → stdout 输出 JSON 结果

记忆层次：
  procedural/ — 程序性记忆（flow 完整流程 + fragment 可复用片段）
  semantic/   — 语义记忆（UI 地图、业务规则、表单规律）
  episodic/   — 情景记忆（执行日志、错误汇总）
  meta/       — 元记忆（置信度、变更日志）

设计原则：
  - Markdown 存储，LLM 原生可读
  - MEMORY.md 作为指针索引（类似 Claude Code memory.md）
  - 置信度衰减模型驱动执行模式选择
  - 零外部依赖，可通过 bash 独立运行
"""

import json
import os
import re
import sys
import math
import glob
import shutil
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple


# ─── 常量 ──────────────────────────────────────────────────

CONFIDENCE_FLASH = 0.85       # 高于此值 → 直接执行
CONFIDENCE_VERIFY = 0.50      # 高于此值 → 带验证执行
CONFIDENCE_ARCHIVE = 0.30     # 低于此值 → 归档
DECAY_FACTOR = 0.995          # 每日置信度衰减因子
FAIL_WEIGHT = 3               # 失败权重倍数
INDEX_MAX_LINES = 200         # MEMORY.md 最大行数


# ─── Frontmatter 解析器（零依赖替代 YAML） ─────────────────

def parse_frontmatter(text: str) -> Tuple[Dict[str, Any], str]:
    """解析 Markdown frontmatter（--- 分隔的键值对）+ 正文。

    支持的值类型：字符串、数字、布尔、列表（- item 格式）。
    不支持嵌套对象（保持简单）。
    """
    if not text.startswith("---"):
        return {}, text

    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text

    meta: Dict[str, Any] = {}
    current_key: Optional[str] = None
    current_list: Optional[list] = None

    for line in parts[1].strip().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        # 列表项
        if stripped.startswith("- ") and current_key is not None:
            if current_list is None:
                current_list = []
            item = stripped[2:].strip().strip('"').strip("'")
            current_list.append(item)
            meta[current_key] = current_list
            continue

        # 保存上一个列表
        current_list = None

        # 键值对
        if ":" in stripped:
            key, _, val = stripped.partition(":")
            key = key.strip()
            val = val.strip()
            current_key = key

            if not val:
                # 可能是列表的开始
                meta[key] = []
                current_list = meta[key]
                continue

            # 类型推断
            meta[key] = _parse_value(val)

    body = parts[2].strip() if len(parts) > 2 else ""
    return meta, body


def _parse_value(val: str) -> Any:
    """将字符串值解析为合适的 Python 类型。"""
    if val.lower() in ("true", "yes"):
        return True
    if val.lower() in ("false", "no"):
        return False
    if val.lower() in ("null", "none", "~"):
        return None
    # 尝试数字
    try:
        if "." in val:
            return float(val)
        return int(val)
    except ValueError:
        pass
    # 去引号
    if (val.startswith('"') and val.endswith('"')) or \
       (val.startswith("'") and val.endswith("'")):
        return val[1:-1]
    # JSON 数组
    if val.startswith("[") and val.endswith("]"):
        try:
            return json.loads(val)
        except json.JSONDecodeError:
            pass
    return val


def dump_frontmatter(meta: Dict[str, Any], body: str) -> str:
    """将 frontmatter 字典 + 正文序列化为 Markdown 文件内容。"""
    lines = ["---"]
    for key, val in meta.items():
        if isinstance(val, list):
            lines.append(f"{key}:")
            for item in val:
                lines.append(f"  - {item}")
        elif isinstance(val, bool):
            lines.append(f"{key}: {'true' if val else 'false'}")
        elif val is None:
            lines.append(f"{key}: null")
        else:
            lines.append(f"{key}: {val}")
    lines.append("---")
    lines.append("")
    lines.append(body)
    return "\n".join(lines)


# ─── 置信度计算 ─────────────────────────────────────────────

def compute_confidence(
    success_count: int,
    fail_count: int,
    days_since_last_use: float = 0.0,
) -> float:
    """计算记忆置信度，含时间衰减。

    公式: base * decay^days
    base = success / (success + fail * FAIL_WEIGHT)
    """
    total = success_count + fail_count * FAIL_WEIGHT
    if total == 0:
        base = 0.5  # 无数据时给中间值
    else:
        base = success_count / total

    decayed = base * (DECAY_FACTOR ** days_since_last_use)
    return round(min(max(decayed, 0.0), 1.0), 4)


def execution_mode(confidence: float) -> str:
    """根据置信度决定执行模式。"""
    if confidence >= CONFIDENCE_FLASH:
        return "flash"       # 直接执行，跳过推理
    elif confidence >= CONFIDENCE_VERIFY:
        return "verify"      # 按流程执行，每步验证
    elif confidence >= CONFIDENCE_ARCHIVE:
        return "explore"     # 需要探索增强
    else:
        return "archive"     # 不可靠，仅供参考


# ─── 目录结构 ───────────────────────────────────────────────

SKELETON_DIRS = [
    "procedural/flows",
    "procedural/fragments",
    "semantic",
    "episodic/sessions",
    "episodic/errors",
    "meta",
]

INITIAL_FILES = {
    "semantic/ui-map.md": (
        "---\n"
        "last_verified: null\n"
        "page_count: 0\n"
        "---\n\n"
        "# 报销系统 UI 地图\n\n"
        "> 此文件由探索 agent 自动维护。每次探索发现新页面或元素时追加。\n\n"
        "（尚未探索，等待首次执行后填充）\n"
    ),
    "semantic/rules.md": (
        "---\n"
        "last_updated: null\n"
        "---\n\n"
        "# 业务规则\n\n"
        "## 类别与报销入口映射\n\n"
        "| 类别 | 按钮文本 | 验证状态 |\n"
        "|------|---------|----------|\n"
        "| 材料、快递 | 日常报销(专用材料、邮寄费、办公费等) | ⏳ 待验证 |\n"
        "| 出差 | 国内差旅 | ⏳ 待验证 |\n"
        "| 手机通讯费 | 手机通讯费(仅限横向科研及个人科研基金) | ⏳ 待验证 |\n"
        "| 加班餐 | 科研业务专项费(科研燃油、加班及接待餐) | ⏳ 待验证 |\n"
        "| 试剂耗材 | 试剂耗材管理平台预约报销 | ⏳ 待验证 |\n\n"
        "## 表单规则\n\n"
        "（尚未积累，等待首次执行后填充）\n"
    ),
    "semantic/field-patterns.md": (
        "---\n"
        "last_updated: null\n"
        "---\n\n"
        "# 表单字段规律\n\n"
        "> 记录各页面表单字段的类型、格式、特殊行为。\n\n"
        "（尚未探索，等待首次执行后填充）\n"
    ),
}


# ─── MemoryStore 类 ─────────────────────────────────────────

class MemoryStore:
    """报销 Agent 记忆存储引擎。

    所有操作基于文件系统，Markdown 存储，JSON 元数据。
    """

    def __init__(self, base_dir: str):
        self.base_dir = os.path.abspath(base_dir)

    # ── 初始化 ──────────────────────────────────────────

    def init(self) -> Dict[str, Any]:
        """初始化记忆目录结构，生成骨架文件。

        幂等操作：已存在的文件不会被覆盖。
        """
        created_dirs: List[str] = []
        created_files: List[str] = []

        # 创建目录
        for d in SKELETON_DIRS:
            full = os.path.join(self.base_dir, d)
            if not os.path.exists(full):
                os.makedirs(full, exist_ok=True)
                created_dirs.append(d)

        # 创建初始文件
        for rel_path, content in INITIAL_FILES.items():
            full = os.path.join(self.base_dir, rel_path)
            if not os.path.exists(full):
                with open(full, "w", encoding="utf-8") as f:
                    f.write(content)
                created_files.append(rel_path)

        # 创建 confidence.json
        conf_path = os.path.join(self.base_dir, "meta/confidence.json")
        if not os.path.exists(conf_path):
            self._write_json(conf_path, {
                "flows": {},
                "fragments": {},
                "semantic": {},
            })
            created_files.append("meta/confidence.json")

        # 创建 changelog.md
        log_path = os.path.join(self.base_dir, "meta/changelog.md")
        if not os.path.exists(log_path):
            with open(log_path, "w", encoding="utf-8") as f:
                f.write("# 记忆变更日志\n\n")
                f.write(f"- {self._now_str()} 初始化记忆系统\n")
            created_files.append("meta/changelog.md")

        # 生成 MEMORY.md 索引
        self.rebuild_index()
        if "MEMORY.md" not in created_files:
            created_files.append("MEMORY.md")

        return {
            "success": True,
            "base_dir": self.base_dir,
            "created_dirs": created_dirs,
            "created_files": created_files,
        }

    # ── 写入：程序性记忆 ────────────────────────────────

    def write_flow(
        self,
        flow_id: str,
        category: str,
        steps: List[Dict[str, Any]],
        *,
        source: str = "exploration",
        preconditions: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """写入或更新一个完整流程（flow）。

        Args:
            flow_id:   流程唯一标识，如 "daily-reimbursement"
            category:  报销类别，如 "日常报销"
            steps:     步骤列表，每步包含 action/target/notes 等
            source:    来源，exploration | manual | merged
            preconditions: 前置条件列表

        Returns:
            写入结果，包含文件路径和版本号
        """
        safe_name = self._safe_filename(category)
        flow_path = os.path.join(
            self.base_dir, "procedural/flows", f"{safe_name}.flow.md"
        )

        # 如果已存在，读取旧版本号
        version = 1
        old_meta: Dict[str, Any] = {}
        if os.path.exists(flow_path):
            old_meta, _ = self._read_md(flow_path)
            old_ver = old_meta.get("version", 0)
            version = old_ver + 1

        # 构建正文
        body_lines = [f"# {category}完整流程\n"]
        for i, step in enumerate(steps, 1):
            body_lines.append(f"## Step {i}: {step.get('title', '未命名')}")
            body_lines.append(f"- action: {step.get('action', '?')}")
            if step.get("target"):
                body_lines.append(f"- target: {step['target']}")
            if step.get("fragment"):
                body_lines.append(f"- fragment: {step['fragment']}")
            if step.get("input"):
                body_lines.append(
                    f"- input: {json.dumps(step['input'], ensure_ascii=False)}"
                )
            if step.get("precondition"):
                body_lines.append(f"- precondition: {step['precondition']}")
            if step.get("fallback"):
                body_lines.append(f"- fallback: {step['fallback']}")
            if step.get("verify"):
                body_lines.append(f"- verify: {step['verify']}")
            if step.get("notes"):
                body_lines.append(f"- notes: {step['notes']}")
            if step.get("wait_after"):
                body_lines.append(f"- wait_after: {step['wait_after']}")
            if step.get("on_skip"):
                body_lines.append(f"- on_skip: {step['on_skip']}")
            body_lines.append("")

        # 构建 frontmatter
        now = self._now_str()
        meta = {
            "flow_id": flow_id,
            "category": category,
            "version": version,
            "confidence": old_meta.get("confidence", 0.5),
            "success_count": old_meta.get("success_count", 0),
            "fail_count": old_meta.get("fail_count", 0),
            "last_used": old_meta.get("last_used", "null"),
            "last_updated": now,
            "source": source,
            "step_count": len(steps),
        }
        if preconditions:
            meta["preconditions"] = preconditions

        content = dump_frontmatter(meta, "\n".join(body_lines))
        self._write_file(flow_path, content)

        # 更新 confidence.json
        self._update_confidence_entry("flows", flow_id, meta)

        # 写变更日志
        self._append_changelog(
            f"{'更新' if version > 1 else '创建'}流程 {category} v{version}"
        )

        return {
            "success": True,
            "path": flow_path,
            "flow_id": flow_id,
            "version": version,
            "category": category,
        }

    def write_fragment(
        self,
        fragment_id: str,
        title: str,
        steps: List[Dict[str, Any]],
        *,
        known_issues: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """写入或更新一个可复用操作片段（fragment）。

        Args:
            fragment_id:  片段唯一标识，如 "upload-invoice"
            title:        片段标题，如 "上传发票"
            steps:        步骤列表，支持分支（branches 字段）
            known_issues: 已知问题列表

        Returns:
            写入结果
        """
        safe_name = self._safe_filename(title)
        frag_path = os.path.join(
            self.base_dir, "procedural/fragments", f"{safe_name}.frag.md"
        )

        # 读旧数据
        old_meta: Dict[str, Any] = {}
        if os.path.exists(frag_path):
            old_meta, _ = self._read_md(frag_path)

        # 构建正文
        body_lines = [f"# {title}\n"]
        for i, step in enumerate(steps, 1):
            if step.get("type") == "branch":
                # 分支结构
                body_lines.append(f"## 分支: {step.get('title', '?')}")
                body_lines.append(f"- condition: {step.get('condition', '?')}")
                for j, sub in enumerate(step.get("actions", []), 1):
                    body_lines.append(
                        f"  {j}. {sub.get('action', '?')}"
                        f" {sub.get('target', '')}"
                    )
                body_lines.append("")
            else:
                # 普通步骤
                action_desc = step.get("action", "?")
                target = step.get("target", "")
                line = f"{i}. {action_desc}"
                if target:
                    line += f" 「{target}」"
                if step.get("value"):
                    line += f" → {step['value']}"
                body_lines.append(line)

        # frontmatter
        now = self._now_str()
        meta = {
            "fragment_id": fragment_id,
            "confidence": old_meta.get("confidence", 0.5),
            "success_count": old_meta.get("success_count", 0),
            "fail_count": old_meta.get("fail_count", 0),
            "last_used": old_meta.get("last_used", "null"),
            "last_updated": now,
        }
        if known_issues:
            meta["known_issues"] = known_issues

        content = dump_frontmatter(meta, "\n".join(body_lines))
        self._write_file(frag_path, content)

        self._update_confidence_entry("fragments", fragment_id, meta)
        self._append_changelog(f"更新片段 {title}")

        return {
            "success": True,
            "path": frag_path,
            "fragment_id": fragment_id,
        }

    # ── 写入：情景记忆 ────────────────────────────────

    def write_episode(
        self,
        session_id: str,
        category: str,
        record_ids: List[int],
        outcome: str,
        summary: str,
        key_events: List[str],
        discoveries: List[str],
        memory_updates: List[str],
        *,
        mode: str = "exploration",
        flow_used: Optional[str] = None,
        duration_sec: float = 0.0,
        steps_total: int = 0,
        steps_succeeded: int = 0,
        steps_failed: int = 0,
        errors: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """写入一次执行的情景记忆。

        Args:
            session_id:  会话标识
            category:    报销类别
            record_ids:  处理的记录 ID 列表
            outcome:     结果，success | partial_success | failure
            summary:     执行摘要文本
            key_events:  关键事件列表
            discoveries: 新发现列表
            memory_updates: 建议的记忆更新列表
            errors:      错误详情列表

        Returns:
            写入结果
        """
        now = self._now_str()
        date_prefix = datetime.now().strftime("%Y-%m-%d")
        safe_cat = self._safe_filename(category)
        filename = f"{date_prefix}_{safe_cat}_{session_id}.ep.md"
        ep_path = os.path.join(self.base_dir, "episodic/sessions", filename)

        meta = {
            "session_id": session_id,
            "timestamp": now,
            "mode": mode,
            "category": category,
            "record_ids": json.dumps(record_ids),
            "outcome": outcome,
            "duration_sec": duration_sec,
            "steps_total": steps_total,
            "steps_succeeded": steps_succeeded,
            "steps_failed": steps_failed,
        }
        if flow_used:
            meta["flow_used"] = flow_used

        body_lines = [
            "# 执行摘要\n",
            summary,
            "",
            "# 关键事件\n",
        ]
        for evt in key_events:
            body_lines.append(f"- {evt}")

        body_lines.extend(["", "# 新发现\n"])
        for disc in discoveries:
            body_lines.append(f"- {disc}")

        body_lines.extend(["", "# 记忆更新建议\n"])
        for upd in memory_updates:
            body_lines.append(f"- → {upd}")

        content = dump_frontmatter(meta, "\n".join(body_lines))
        self._write_file(ep_path, content)

        # 如果有错误，追加到对应的 error 汇总文件
        if errors:
            for err in errors:
                self._append_error(err)

        self._append_changelog(f"记录会话 {session_id} ({outcome})")

        return {
            "success": True,
            "path": ep_path,
            "session_id": session_id,
            "outcome": outcome,
        }

    def _append_error(self, error: Dict[str, Any]) -> None:
        """追加错误到对应的 error 汇总文件。"""
        err_type = self._safe_filename(error.get("type", "unknown"))
        err_path = os.path.join(
            self.base_dir, "episodic/errors", f"{err_type}.md"
        )

        if os.path.exists(err_path):
            meta, body = self._read_md(err_path)
            meta["occurrences"] = meta.get("occurrences", 0) + 1
            meta["last_seen"] = self._now_str()
            # 追加新条目
            new_entry = (
                f"\n### {self._now_str()}\n"
                f"- 场景: {error.get('context', '未知')}\n"
                f"- 处理: {error.get('resolution', '未解决')}\n"
            )
            body += new_entry
            self._write_file(err_path, dump_frontmatter(meta, body))
        else:
            meta = {
                "error_type": error.get("type", "unknown"),
                "occurrences": 1,
                "last_seen": self._now_str(),
            }
            body = (
                f"# {error.get('type', '未知错误')}\n\n"
                f"## 模式\n\n{error.get('pattern', '待分析')}\n\n"
                f"## 记录\n\n"
                f"### {self._now_str()}\n"
                f"- 场景: {error.get('context', '未知')}\n"
                f"- 处理: {error.get('resolution', '未解决')}\n"
            )
            self._write_file(err_path, dump_frontmatter(meta, body))

    # ── 写入：语义记忆 ────────────────────────────────

    def update_semantic(
        self,
        file_name: str,
        entries: List[Dict[str, str]],
    ) -> Dict[str, Any]:
        """向语义记忆文件追加或更新条目。

        Args:
            file_name:  目标文件名（如 "ui-map.md"、"rules.md"）
            entries:    条目列表，每条包含 section + content

        Returns:
            更新结果
        """
        file_path = os.path.join(self.base_dir, "semantic", file_name)
        if not os.path.exists(file_path):
            return {"success": False, "error": f"文件不存在: {file_name}"}

        meta, body = self._read_md(file_path)
        meta["last_updated"] = self._now_str()

        for entry in entries:
            section = entry.get("section", "")
            content = entry.get("content", "")
            if not section or not content:
                continue

            section_header = f"## {section}"
            if section_header in body:
                # 在该 section 末尾追加（下一个 ## 之前）
                pattern = re.compile(
                    rf"({re.escape(section_header)}.*?)(\n## |\Z)",
                    re.DOTALL,
                )
                def replacer(m):
                    existing = m.group(1).rstrip()
                    # 避免重复：如果 content 已包含在该 section 中就跳过
                    if content.strip() in existing:
                        return m.group(0)
                    next_section = m.group(2)
                    return f"{existing}\n{content}\n{next_section}"

                body = pattern.sub(replacer, body, count=1)
            else:
                # 新增 section
                body += f"\n\n{section_header}\n\n{content}\n"

        self._write_file(file_path, dump_frontmatter(meta, body))

        return {
            "success": True,
            "path": file_path,
            "entries_processed": len(entries),
        }

    # ── 读取 / 查询 ────────────────────────────────────

    def query_flow(
        self,
        category: str,
    ) -> Dict[str, Any]:
        """根据类别查找最佳流程。

        Returns:
            {found, flow_id, confidence, mode, meta, body, path}
        """
        flows_dir = os.path.join(self.base_dir, "procedural/flows")
        if not os.path.isdir(flows_dir):
            return {"found": False}

        best: Optional[Tuple[float, str, Dict, str]] = None

        for fname in os.listdir(flows_dir):
            if not fname.endswith(".flow.md"):
                continue
            fpath = os.path.join(flows_dir, fname)
            meta, body = self._read_md(fpath)
            if meta.get("category") != category:
                continue

            # 计算当前置信度（含衰减）
            days = self._days_since(meta.get("last_used"))
            conf = compute_confidence(
                meta.get("success_count", 0),
                meta.get("fail_count", 0),
                days,
            )

            if best is None or conf > best[0]:
                best = (conf, fpath, meta, body)

        if best is None:
            return {"found": False, "category": category}

        conf, fpath, meta, body = best
        mode = execution_mode(conf)
        return {
            "found": True,
            "flow_id": meta.get("flow_id"),
            "category": category,
            "confidence": conf,
            "mode": mode,
            "version": meta.get("version", 1),
            "meta": meta,
            "body": body,
            "path": fpath,
        }

    def query_fragment(self, fragment_id: str) -> Dict[str, Any]:
        """根据 fragment_id 加载片段。"""
        frags_dir = os.path.join(self.base_dir, "procedural/fragments")
        if not os.path.isdir(frags_dir):
            return {"found": False}

        for fname in os.listdir(frags_dir):
            if not fname.endswith(".frag.md"):
                continue
            fpath = os.path.join(frags_dir, fname)
            meta, body = self._read_md(fpath)
            if meta.get("fragment_id") == fragment_id:
                days = self._days_since(meta.get("last_used"))
                conf = compute_confidence(
                    meta.get("success_count", 0),
                    meta.get("fail_count", 0),
                    days,
                )
                return {
                    "found": True,
                    "fragment_id": fragment_id,
                    "confidence": conf,
                    "meta": meta,
                    "body": body,
                    "path": fpath,
                }

        return {"found": False, "fragment_id": fragment_id}

    def query_errors(
        self,
        error_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        """查询错误汇总。不指定类型则返回全部。"""
        errs_dir = os.path.join(self.base_dir, "episodic/errors")
        if not os.path.isdir(errs_dir):
            return {"errors": []}

        results = []
        for fname in os.listdir(errs_dir):
            if not fname.endswith(".md"):
                continue
            fpath = os.path.join(errs_dir, fname)
            meta, body = self._read_md(fpath)
            if error_type and meta.get("error_type") != error_type:
                continue
            results.append({
                "error_type": meta.get("error_type", fname),
                "occurrences": meta.get("occurrences", 0),
                "last_seen": meta.get("last_seen"),
                "body": body,
                "path": fpath,
            })

        results.sort(key=lambda x: x["occurrences"], reverse=True)
        return {"errors": results}

    def get_exploration_context(
        self,
        category: str,
    ) -> Dict[str, Any]:
        """获取探索模式所需的完整上下文。

        包含：UI 地图 + 业务规则 + 相关错误 + 已有的相近 flow/fragment。
        这是发给 LLM 的背景知识包。
        """
        context: Dict[str, Any] = {"category": category}

        # 语义记忆
        for fname in ("ui-map.md", "rules.md", "field-patterns.md"):
            fpath = os.path.join(self.base_dir, "semantic", fname)
            if os.path.exists(fpath):
                _, body = self._read_md(fpath)
                context[fname.replace(".md", "")] = body

        # 相关错误
        errors = self.query_errors()
        context["known_errors"] = errors["errors"][:5]  # 取最频繁的 5 个

        # 已有 flow（即使不完全匹配，也提供参考）
        flow_result = self.query_flow(category)
        if flow_result.get("found"):
            context["existing_flow"] = {
                "flow_id": flow_result["flow_id"],
                "confidence": flow_result["confidence"],
                "body": flow_result["body"],
            }

        # 所有可用 fragments
        frags_dir = os.path.join(self.base_dir, "procedural/fragments")
        fragments = []
        if os.path.isdir(frags_dir):
            for fname in os.listdir(frags_dir):
                if fname.endswith(".frag.md"):
                    fpath = os.path.join(frags_dir, fname)
                    meta, body = self._read_md(fpath)
                    fragments.append({
                        "fragment_id": meta.get("fragment_id"),
                        "confidence": meta.get("confidence", 0),
                        "body_preview": body[:300],
                    })
        context["available_fragments"] = fragments

        # 最近 5 条 session（同类别）
        sessions_dir = os.path.join(self.base_dir, "episodic/sessions")
        recent = []
        if os.path.isdir(sessions_dir):
            files = sorted(os.listdir(sessions_dir), reverse=True)
            for fname in files:
                if not fname.endswith(".ep.md"):
                    continue
                fpath = os.path.join(sessions_dir, fname)
                meta, body = self._read_md(fpath)
                if meta.get("category") == category:
                    recent.append({
                        "session_id": meta.get("session_id"),
                        "outcome": meta.get("outcome"),
                        "timestamp": meta.get("timestamp"),
                        "summary": body[:500],
                    })
                    if len(recent) >= 5:
                        break
        context["recent_sessions"] = recent

        return context

    def list_flows(self) -> List[Dict[str, Any]]:
        """列出所有可用流程及其置信度。"""
        flows_dir = os.path.join(self.base_dir, "procedural/flows")
        results = []
        if not os.path.isdir(flows_dir):
            return results
        for fname in sorted(os.listdir(flows_dir)):
            if not fname.endswith(".flow.md"):
                continue
            fpath = os.path.join(flows_dir, fname)
            meta, _ = self._read_md(fpath)
            days = self._days_since(meta.get("last_used"))
            conf = compute_confidence(
                meta.get("success_count", 0),
                meta.get("fail_count", 0),
                days,
            )
            results.append({
                "flow_id": meta.get("flow_id"),
                "category": meta.get("category"),
                "version": meta.get("version", 1),
                "confidence": conf,
                "mode": execution_mode(conf),
                "success_count": meta.get("success_count", 0),
                "fail_count": meta.get("fail_count", 0),
                "path": fpath,
            })
        results.sort(key=lambda x: x["confidence"], reverse=True)
        return results

    def list_fragments(self) -> List[Dict[str, Any]]:
        """列出所有可用片段。"""
        frags_dir = os.path.join(self.base_dir, "procedural/fragments")
        results = []
        if not os.path.isdir(frags_dir):
            return results
        for fname in sorted(os.listdir(frags_dir)):
            if not fname.endswith(".frag.md"):
                continue
            fpath = os.path.join(frags_dir, fname)
            meta, _ = self._read_md(fpath)
            days = self._days_since(meta.get("last_used"))
            conf = compute_confidence(
                meta.get("success_count", 0),
                meta.get("fail_count", 0),
                days,
            )
            results.append({
                "fragment_id": meta.get("fragment_id"),
                "confidence": conf,
                "success_count": meta.get("success_count", 0),
                "fail_count": meta.get("fail_count", 0),
                "path": fpath,
            })
        return results

    # ── 置信度更新 ──────────────────────────────────────

    def update_confidence(
        self,
        memory_type: str,
        memory_id: str,
        success: bool,
    ) -> Dict[str, Any]:
        """执行后更新某条记忆的置信度。

        Args:
            memory_type: "flows" | "fragments"
            memory_id:   flow_id 或 fragment_id
            success:     本次执行是否成功
        """
        # 更新 confidence.json
        conf_path = os.path.join(self.base_dir, "meta/confidence.json")
        conf_data = self._read_json(conf_path)

        entry = conf_data.get(memory_type, {}).get(memory_id, {})
        if success:
            entry["success_count"] = entry.get("success_count", 0) + 1
        else:
            entry["fail_count"] = entry.get("fail_count", 0) + 1
        entry["last_used"] = self._now_str()

        new_conf = compute_confidence(
            entry.get("success_count", 0),
            entry.get("fail_count", 0),
            0.0,
        )
        entry["confidence"] = new_conf

        conf_data.setdefault(memory_type, {})[memory_id] = entry
        self._write_json(conf_path, conf_data)

        # 同步更新对应的 .md 文件 frontmatter
        if memory_type == "flows":
            self._sync_confidence_to_md(
                "procedural/flows", ".flow.md",
                "flow_id", memory_id, entry, success,
            )
        elif memory_type == "fragments":
            self._sync_confidence_to_md(
                "procedural/fragments", ".frag.md",
                "fragment_id", memory_id, entry, success,
            )

        return {
            "success": True,
            "memory_type": memory_type,
            "memory_id": memory_id,
            "new_confidence": new_conf,
            "mode": execution_mode(new_conf),
        }

    def _sync_confidence_to_md(
        self,
        subdir: str,
        suffix: str,
        id_key: str,
        id_val: str,
        entry: Dict,
        success: bool,
    ) -> None:
        """将 confidence.json 中的更新同步回 .md 文件 frontmatter。"""
        target_dir = os.path.join(self.base_dir, subdir)
        if not os.path.isdir(target_dir):
            return
        for fname in os.listdir(target_dir):
            if not fname.endswith(suffix):
                continue
            fpath = os.path.join(target_dir, fname)
            meta, body = self._read_md(fpath)
            if meta.get(id_key) == id_val:
                meta["confidence"] = entry["confidence"]
                meta["success_count"] = entry.get("success_count", 0)
                meta["fail_count"] = entry.get("fail_count", 0)
                meta["last_used"] = entry["last_used"]
                self._write_file(fpath, dump_frontmatter(meta, body))
                break

    # ── 索引重建 ────────────────────────────────────────

    def rebuild_index(self) -> Dict[str, Any]:
        """重建 MEMORY.md 索引文件。"""
        lines = [
            "# 报销 Agent 记忆索引\n",
            "> 本文件由 `rebuild_index()` 自动生成，请勿手动编辑。\n",
        ]

        # 可用流程
        flows = self.list_flows()
        lines.append("## 可用流程\n")
        if flows:
            for f in flows:
                flag = ""
                if f["mode"] == "flash":
                    flag = "⚡"
                elif f["mode"] == "explore":
                    flag = "⚠️"
                elif f["mode"] == "archive":
                    flag = "🗄️"
                rel = os.path.relpath(f["path"], self.base_dir)
                lines.append(
                    f"- [{f['category']} v{f['version']}]({rel})"
                    f" — 置信度 {f['confidence']:.2f},"
                    f" {f['success_count']}次成功"
                    f" {flag}"
                )
        else:
            lines.append("（尚无已学习的流程）\n")
        lines.append("")

        # 可用片段
        frags = self.list_fragments()
        lines.append("## 常用片段\n")
        if frags:
            for f in frags:
                rel = os.path.relpath(f["path"], self.base_dir)
                lines.append(
                    f"- [{f['fragment_id']}]({rel})"
                    f" — 置信度 {f['confidence']:.2f}"
                )
        else:
            lines.append("（尚无已学习的片段）\n")
        lines.append("")

        # 已知问题
        errs = self.query_errors()
        lines.append("## 已知问题\n")
        if errs["errors"]:
            for e in errs["errors"][:10]:
                rel = os.path.relpath(e["path"], self.base_dir)
                lines.append(
                    f"- [{e['error_type']}]({rel})"
                    f" — {e['occurrences']}次出现"
                )
        else:
            lines.append("（尚无错误记录）\n")
        lines.append("")

        # 语义知识
        lines.append("## 系统知识\n")
        for fname in ("ui-map.md", "rules.md", "field-patterns.md"):
            fpath = os.path.join(self.base_dir, "semantic", fname)
            if os.path.exists(fpath):
                meta, _ = self._read_md(fpath)
                verified = meta.get("last_verified") or meta.get(
                    "last_updated", "未知"
                )
                lines.append(
                    f"- [semantic/{fname}](semantic/{fname})"
                    f" — 最后更新 {verified}"
                )
        lines.append("")

        # 最近变更
        log_path = os.path.join(self.base_dir, "meta/changelog.md")
        if os.path.exists(log_path):
            with open(log_path, "r", encoding="utf-8") as f:
                log_lines = f.readlines()
            recent = [
                l.strip() for l in log_lines
                if l.strip().startswith("- ")
            ][-5:]
            if recent:
                lines.append("## 最近变更\n")
                for rl in recent:
                    lines.append(rl)
                lines.append("")

        index_content = "\n".join(lines)

        # 截断保护
        actual_lines = index_content.splitlines()
        if len(actual_lines) > INDEX_MAX_LINES:
            index_content = "\n".join(actual_lines[:INDEX_MAX_LINES])
            index_content += "\n\n> ⚠️ 索引已截断，请运行 dream 整合\n"

        index_path = os.path.join(self.base_dir, "MEMORY.md")
        self._write_file(index_path, index_content)

        return {
            "success": True,
            "path": index_path,
            "lines": len(index_content.splitlines()),
            "flows": len(flows),
            "fragments": len(frags),
        }

    # ── 内部工具方法 ────────────────────────────────────

    def _read_md(self, path: str) -> Tuple[Dict[str, Any], str]:
        """读取 Markdown 文件，解析 frontmatter。"""
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        return parse_frontmatter(content)

    def _write_file(self, path: str, content: str) -> None:
        """写入文件，自动创建目录。"""
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

    def _read_json(self, path: str) -> Dict[str, Any]:
        """读取 JSON 文件。"""
        if not os.path.exists(path):
            return {}
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _write_json(self, path: str, data: Dict[str, Any]) -> None:
        """写入 JSON 文件。"""
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _update_confidence_entry(
        self,
        memory_type: str,
        memory_id: str,
        meta: Dict[str, Any],
    ) -> None:
        """更新 confidence.json 中的条目。"""
        conf_path = os.path.join(self.base_dir, "meta/confidence.json")
        conf_data = self._read_json(conf_path)
        conf_data.setdefault(memory_type, {})[memory_id] = {
            "confidence": meta.get("confidence", 0.5),
            "success_count": meta.get("success_count", 0),
            "fail_count": meta.get("fail_count", 0),
            "last_used": meta.get("last_used"),
        }
        self._write_json(conf_path, conf_data)

    def _append_changelog(self, message: str) -> None:
        """追加变更日志。"""
        log_path = os.path.join(self.base_dir, "meta/changelog.md")
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        entry = f"- {self._now_str()} {message}\n"
        if os.path.exists(log_path):
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(entry)
        else:
            with open(log_path, "w", encoding="utf-8") as f:
                f.write("# 记忆变更日志\n\n")
                f.write(entry)

    @staticmethod
    def _now_str() -> str:
        return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    @staticmethod
    def _safe_filename(name: str) -> str:
        """将中文/特殊字符转为安全文件名。"""
        # 保留中文、字母、数字、下划线、连字符
        safe = re.sub(r"[^\w\u4e00-\u9fff\-]", "_", name)
        return safe.strip("_")[:80]

    @staticmethod
    def _days_since(ts_str: Optional[str]) -> float:
        """计算距某时间戳的天数。"""
        if not ts_str or ts_str == "null":
            return 30.0  # 无记录按 30 天计
        try:
            ts = datetime.strptime(ts_str[:19], "%Y-%m-%dT%H:%M:%S")
            delta = datetime.now() - ts
            return max(delta.total_seconds() / 86400, 0.0)
        except (ValueError, TypeError):
            return 30.0


# ─── CLI 入口：stdin JSON → stdout JSON ──────────────────

def _cli_main():
    """CLI 分发器，根据 action 字段调用不同方法。"""
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        json.dump(
            {"success": False, "error": f"JSON 解析失败: {e}"},
            sys.stdout, ensure_ascii=False,
        )
        sys.exit(1)

    action = payload.get("action", "")
    base_dir = payload.get("base_dir", "./memory")
    store = MemoryStore(base_dir)

    dispatch = {
        "init":                lambda: store.init(),
        "write_flow":          lambda: store.write_flow(**payload["data"]),
        "write_fragment":      lambda: store.write_fragment(**payload["data"]),
        "write_episode":       lambda: store.write_episode(**payload["data"]),
        "update_semantic":     lambda: store.update_semantic(**payload["data"]),
        "update_confidence":   lambda: store.update_confidence(**payload["data"]),
        "query_flow":          lambda: store.query_flow(**payload["data"]),
        "query_fragment":      lambda: store.query_fragment(**payload["data"]),
        "query_errors":        lambda: store.query_errors(
            **payload.get("data", {})
        ),
        "get_exploration_context": lambda: store.get_exploration_context(
            **payload["data"]
        ),
        "list_flows":          lambda: {"flows": store.list_flows()},
        "list_fragments":      lambda: {"fragments": store.list_fragments()},
        "rebuild_index":       lambda: store.rebuild_index(),
    }

    handler = dispatch.get(action)
    if not handler:
        json.dump(
            {
                "success": False,
                "error": f"未知 action: {action}",
                "available_actions": list(dispatch.keys()),
            },
            sys.stdout, ensure_ascii=False,
        )
        sys.exit(1)

    try:
        result = handler()
        if isinstance(result, dict) and "success" not in result:
            result["success"] = True
        json.dump(result, sys.stdout, ensure_ascii=False, default=str)
    except Exception as e:
        json.dump(
            {"success": False, "error": str(e)},
            sys.stdout, ensure_ascii=False,
        )
        sys.exit(1)


if __name__ == "__main__":
    _cli_main()
