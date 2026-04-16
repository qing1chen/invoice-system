"""
报销转卡金额计算与附件去重引擎

纯计算模块，零外部依赖（仅 Python 标准库）。

两种调用方式：
  1. Python import: from calculate_amounts import calculate_transfer_amounts
  2. Bash CLI:      echo '{"records": [...]}' | python calculate_amounts.py
                    → stdout 输出 JSON 结果

功能：
  1. 按「姓名/公司」分组，对同户名的多条记录金额求和
  2. 生成转卡汇总指令（每个户名只出现一次）
  3. 按附件路径去重，生成附件上传指令
  4. 生成记录ID映射（供 Browser Agent 在 skip 时引用 record_id）
  5. 将计算字段注入到每条记录中
"""

import json
import sys
from collections import defaultdict
from typing import Any, Dict, List
from urllib.parse import unquote


def normalize_path(p: str) -> str:
    """归一化附件路径：统一分隔符、合并连续斜杠、去除尾部斜杠、解码 URI。"""
    n = p.strip()
    n = n.replace("\\", "/")
    while "//" in n:
        n = n.replace("//", "/")
    n = n.rstrip("/")
    try:
        n = unquote(n)
    except Exception:
        pass
    return n


def calculate_transfer_amounts(
    records: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """按「姓名/公司」分组，计算每个户名的转卡金额汇总。

    Returns:
        dict: amount_by_person, detail_by_person, transfer_summary,
              attachment_summary, record_id_map, enriched_records
    """
    # 1. 按户名分组
    person_amounts: Dict[str, list] = defaultdict(list)
    for rec in records:
        name = (rec.get("姓名/公司") or "").strip()
        if not name:
            continue
        try:
            amt = float(rec.get("金额", 0) or 0)
        except (ValueError, TypeError):
            amt = 0.0
        person_amounts[name].append(amt)

    # 2. 计算汇总
    amount_by_person: Dict[str, float] = {}
    detail_by_person: Dict[str, str] = {}
    for name, amts in person_amounts.items():
        total = round(sum(amts), 2)
        amount_by_person[name] = total
        if len(amts) == 1:
            detail_by_person[name] = f"{amts[0]:.2f}"
        else:
            parts = "+".join(f"{a:.2f}" for a in amts)
            detail_by_person[name] = f"{parts}={total:.2f}"

    # 3. 转卡汇总
    summary_lines = []
    for name in person_amounts:
        total_str = f"{amount_by_person[name]:.2f}"
        detail_str = detail_by_person[name]
        line = f"在「转卡」区域中找到户名为{name}的那一行，将其金额设置为 {total_str}"
        if "+" in detail_str:
            line += f"（明细：{detail_str}）"
        line += "，点击保存。"
        summary_lines.append(line)
    transfer_summary = "\n".join(summary_lines)

    # 4. 附件去重
    seen_paths: set = set()
    attachment_lines: list = []
    for rec in records:
        has_attachment = rec.get("匹配附件")
        att_path_raw = rec.get("附件路径", "")
        if not has_attachment or not att_path_raw:
            continue
        paths = [p.strip() for p in att_path_raw.split(",") if p.strip()]
        for p in paths:
            normalized = normalize_path(p)
            if normalized in seen_paths:
                continue
            seen_paths.add(normalized)
            attachment_lines.append(
                f"在补充说明中上传附件，点击上传附件，"
                f"输入文件路径「{normalized}」，点击文件，点击打开。"
            )
    attachment_summary = "\n".join(attachment_lines)

    # 5. 记录ID映射
    id_map_lines = [
        f"  - record_id={rec.get('db_id', '?')}, "
        f"序号={rec.get('序号', '?')}, "
        f"姓名={rec.get('姓名/公司', '?')}, "
        f"票号={rec.get('发票号码', '?')}, "
        f"金额={rec.get('金额', '?')}"
        for rec in records
    ]
    record_id_map = "\n".join([
        f"本报销单包含 {len(records)} 条记录，record_id 与发票的对应关系如下：",
        *id_map_lines,
        "如需跳过某条记录的操作，请在 skip 动作的 value 中注明对应的 record_id。",
    ])

    # 6. 注入
    enriched = []
    for rec in records:
        new_rec = dict(rec)
        name = (rec.get("姓名/公司") or "").strip()
        if name and name in amount_by_person:
            new_rec["转卡金额"] = f"{amount_by_person[name]:.2f}"
            new_rec["转卡明细"] = detail_by_person[name]
        else:
            new_rec["转卡金额"] = str(rec.get("金额", "0.00"))
            new_rec["转卡明细"] = str(rec.get("金额", "0.00"))
        new_rec["转卡汇总"] = transfer_summary
        new_rec["附件汇总"] = attachment_summary
        new_rec["记录ID映射"] = record_id_map
        enriched.append(new_rec)

    return {
        "amount_by_person": amount_by_person,
        "detail_by_person": detail_by_person,
        "transfer_summary": transfer_summary,
        "attachment_summary": attachment_summary,
        "record_id_map": record_id_map,
        "enriched_records": enriched,
    }


# ─── CLI 入口：stdin JSON → stdout JSON ──────────────────
if __name__ == "__main__":
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        json.dump({"success": False, "error": f"JSON 解析失败: {e}"}, sys.stdout, ensure_ascii=False)
        sys.exit(1)

    records = payload.get("records", [])
    if not records:
        json.dump({"success": False, "error": "records 为空"}, sys.stdout, ensure_ascii=False)
        sys.exit(1)

    result = calculate_transfer_amounts(records)
    result["success"] = True
    json.dump(result, sys.stdout, ensure_ascii=False, default=str)
