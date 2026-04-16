"""
发票与报销记录匹配模块（LangChain 版）— 规则匹配 → LLM 智能匹配。
"""

from __future__ import annotations

import logging
import re
from itertools import combinations
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
from langchain_core.prompts import ChatPromptTemplate

from invoice_toolkit.config import Settings
from invoice_toolkit.database import get_invoice_db, get_record_db, dataframe_to_records
from invoice_toolkit.llm_client import LLMClient

logger = logging.getLogger(__name__)

# =========================================================================
# LangChain Prompt
# =========================================================================

MATCH_PROMPT = ChatPromptTemplate.from_messages([
    ("system", "你是发票匹配专家，只返回JSON格式结果。"),
    ("human", """\
请根据发票数据和报销记录进行匹配。

## 匹配原则：
1. **金额优先** - 以发票「价税合计」为核心依据
2. **内容语义** - 结合商品名称、物品简介、备注、相对路径
3. **非强制** - 允许发票暂时无法匹配

## 发票数据：
{invoice_lines}

## 报销记录：
{record_lines}

返回 JSON，key 为发票编号（I1, I2...），value 为记录编号（R15, R16...）或 "未匹配"。
示例：{{"I1": "R15", "I2": "未匹配"}}

请只返回 JSON。"""),
])


# =========================================================================
# 工具函数
# =========================================================================

def find_amount_combination(amounts: List[float], target: float, tol: float = 0.001) -> Optional[List[float]]:
    for r in range(1, len(amounts) + 1):
        for combo in combinations(amounts, r):
            if abs(sum(combo) - target) < tol:
                return list(combo)
    return None


def extract_amounts_from_remark(remark: str, target_amount: float, tol: float = 0.001) -> List[float]:
    if pd.isna(remark) or not remark:
        return []
    numbers = [float(x) for x in re.findall(r"\d+\.?\d*", str(remark))]
    return find_amount_combination(numbers, target_amount, tol) or []


# =========================================================================
# 匹配结果构造
# =========================================================================

def _inv_match(seq, rec_row, match_type, combo_str="") -> Dict[str, Any]:
    return {
        "匹配序号": seq, "匹配姓名": rec_row.get("姓名/公司", ""),
        "匹配金额": rec_row.get("金额", ""), "匹配简介": rec_row.get("物品简介", ""),
        "是否匹配": "已匹配", "匹配方式": match_type, "组合金额": combo_str,
    }

def _empty_rec_match() -> Dict[str, Any]:
    return {
        "匹配发票": [], "匹配发票金额": [], "是否匹配": "未匹配",
        "匹配方式": "", "备注分解金额": None, "未匹配金额": None,
    }


# =========================================================================
# 核心匹配器
# =========================================================================

class SmartInvoiceRecordMatcher:
    """智能发票匹配器：规则优先 + LLM 补充"""

    def __init__(self, settings: Settings | None = None, llm_client: LLMClient | None = None, *, use_llm: bool = True):
        self._settings = settings or Settings.from_env()
        self._batch_size = self._settings.batch_size
        self.use_llm = use_llm
        self._llm = llm_client or LLMClient(self._settings.llm) if use_llm else None
        self._invoice_db = get_invoice_db(self._settings)
        self._record_db = get_record_db(self._settings)
        self._match_chain = self._llm.build_chain(MATCH_PROMPT, output_json=True) if self._llm else None

    def match(self, invoice_df: pd.DataFrame, record_df: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame]:
        logger.info("开始匹配 (发票 %d 张, 记录 %d 条)", len(invoice_df), len(record_df))

        # Step 1: 规则匹配
        inv_matches, unmatched, rec_matches = self._rule_based_match(invoice_df, record_df)
        logger.info("规则匹配: %d 张, 待处理: %d 张", len(inv_matches), len(unmatched))

        # Step 2: LLM 匹配
        llm_matches: Dict[str, Dict] = {}
        if self.use_llm and self._match_chain and unmatched:
            matched_seqs = {s for s, info in rec_matches.items() if info["是否匹配"] == "已匹配"}
            filtered = record_df[~record_df["序号"].isin(matched_seqs)]
            llm_matches, rec_matches = self._llm_match(unmatched, filtered, rec_matches)
            logger.info("LLM 匹配: %d 张", sum(1 for v in llm_matches.values() if v.get("是否匹配") == "已匹配"))

        all_matches = {**inv_matches, **llm_matches}
        inv_result = self._build_invoice_result(invoice_df, all_matches)
        rec_result = self._build_record_result(record_df, rec_matches)
        self._print_summary(inv_result, rec_result, all_matches)
        return inv_result, rec_result

    def match_to_db(self, inv_df: pd.DataFrame, rec_df: pd.DataFrame) -> None:
        inv_records = dataframe_to_records(inv_df)
        self._invoice_db.upsert_match_results(inv_records)
        self._invoice_db.upsert_ocr_results(inv_records)
        self._record_db.upsert_match_results(dataframe_to_records(rec_df))
        logger.info("匹配结果已保存到数据库")

    # 向后兼容
    match_to_excel = match_to_db

    # ------------------------------------------------------------------
    # 规则匹配
    # ------------------------------------------------------------------

    def _rule_based_match(self, invoice_df, record_df, tol=0.001):
        inv_matches, rec_matches, used = {}, {}, set()

        # 统计发票金额出现次数
        inv_amt_counts: Dict[float, int] = {}
        for _, row in invoice_df.iterrows():
            if pd.notna(row.get("价税合计")):
                key = round(float(row["价税合计"]), 2)
                inv_amt_counts[key] = inv_amt_counts.get(key, 0) + 1

        # 统计记录金额出现次数
        rec_amt_counts: Dict[float, int] = {}
        for _, row in record_df.iterrows():
            if pd.notna(row.get("金额")):
                key = round(float(row["金额"]), 2)
                rec_amt_counts[key] = rec_amt_counts.get(key, 0) + 1

        unmatched: List[Dict] = []

        # 第一轮：精确金额 1:1（发票或记录中存在重复金额则跳过，交给 LLM）
        for _, inv_row in invoice_df.iterrows():
            inv_name = inv_row.get("旧文件名", "")
            if pd.isna(inv_row.get("价税合计")):
                unmatched.append(inv_row.to_dict())
                continue
            inv_amt = round(float(inv_row["价税合计"]), 2)
            matched = False
            for _, rec_row in record_df.iterrows():
                seq = rec_row["序号"]
                if seq in used:
                    continue
                if abs(inv_amt - round(float(rec_row.get("金额", 0)), 2)) < tol and inv_amt_counts.get(inv_amt, 0) <= 1 and rec_amt_counts.get(inv_amt, 0) <= 1:
                    inv_matches[inv_name] = _inv_match(seq, rec_row, "金额精确(1对1)")
                    rec_matches[seq] = {
                        "匹配发票": [inv_name], "匹配发票金额": [inv_amt],
                        "是否匹配": "已匹配", "匹配方式": "金额精确(1对1)",
                        "备注分解金额": None, "未匹配金额": None,
                    }
                    used.add(seq)
                    matched = True
                    break
            if not matched:
                unmatched.append(inv_row.to_dict())

        # 第二轮：备注分解
        still_unmatched = []
        for inv_dict in unmatched:
            inv_name = inv_dict.get("旧文件名", "")
            if pd.isna(inv_dict.get("价税合计")):
                still_unmatched.append(inv_dict)
                continue
            inv_amt = round(float(inv_dict["价税合计"]), 2)
            matched = False
            for _, rec_row in record_df.iterrows():
                seq = rec_row["序号"]
                if seq in used:
                    continue
                result = self._try_remark_match(rec_row, inv_amt, tol)
                if result:
                    inv_matches[inv_name] = _inv_match(seq, rec_row, result["match_type"], result.get("combination_str", ""))
                    rm = rec_matches.setdefault(seq, _empty_rec_match())
                    rm["匹配发票"].append(inv_name)
                    rm["匹配发票金额"].append(inv_amt)
                    rm["匹配方式"] = result["match_type"]
                    rm["备注分解金额"] = result.get("remark_amounts")
                    rm["未匹配金额"] = result.get("unmatched_amounts")
                    total_inv = round(sum(rm["匹配发票金额"]), 2)
                    rm["是否匹配"] = "已匹配" if abs(round(float(rec_row.get("金额", 0)), 2) - total_inv) < tol else "部分匹配"
                    matched = True
                    break
            if not matched:
                still_unmatched.append(inv_dict)

        # 第三轮：发票组合
        final_unmatched = [d for d in still_unmatched if d.get("旧文件名", "") not in inv_matches]
        for _, rec_row in record_df.iterrows():
            seq = rec_row["序号"]
            if seq in used or seq in rec_matches:
                continue
            combo = self._try_invoice_combination(rec_row, round(float(rec_row.get("金额", 0)), 2), final_unmatched, tol)
            if combo:
                for name in combo["matched_invoices"]:
                    inv_matches[name] = _inv_match(seq, rec_row, combo["match_type"], combo.get("combination_str", ""))
                rec_matches[seq] = {
                    "匹配发票": combo["matched_invoices"],
                    "匹配发票金额": [round(float(d.get("价税合计", 0)), 2) for d in final_unmatched if d.get("旧文件名") in combo["matched_invoices"]],
                    "是否匹配": "已匹配", "匹配方式": combo["match_type"],
                    "备注分解金额": None, "未匹配金额": None,
                }
                used.add(seq)
                final_unmatched = [d for d in final_unmatched if d.get("旧文件名") not in combo["matched_invoices"]]

        return inv_matches, final_unmatched, rec_matches

    def _try_remark_match(self, rec_row, target, tol):
        remark_amounts = extract_amounts_from_remark(rec_row.get("备注", ""), round(float(rec_row.get("金额", 0)), 2), tol)
        if not remark_amounts:
            return None
        for amt in remark_amounts:
            if abs(amt - target) < tol:
                rest = list(remark_amounts)
                rest.remove(amt)
                return {"match_type": "备注分解", "combination_str": "+".join(map(str, remark_amounts)),
                        "remark_amounts": remark_amounts, "unmatched_amounts": rest}
        return None

    def _try_invoice_combination(self, rec_row, target, unmatched, tol):
        inv_amounts: Dict[float, List[str]] = {}
        for inv in unmatched:
            if pd.isna(inv.get("价税合计")):
                continue
            key = round(float(inv["价税合计"]), 2)
            inv_amounts.setdefault(key, []).append(inv.get("旧文件名", ""))

        unique_combo, count = None, 0
        for r in range(2, len(inv_amounts) + 1):
            for combo in combinations(inv_amounts.keys(), r):
                if abs(sum(combo) - target) < tol:
                    count += 1
                    if count > 1:
                        return None
                    unique_combo = combo

        if unique_combo and count == 1:
            names = [n for amt in unique_combo for n in inv_amounts[amt]]
            return {"matched_invoices": names, "match_type": f"发票组合(1对{len(names)})",
                    "combination_str": "+".join(map(str, unique_combo))}
        return None

    # ------------------------------------------------------------------
    # LLM 匹配
    # ------------------------------------------------------------------

    def _llm_match(self, unmatched_invoices, record_df, rec_matches):
        if not unmatched_invoices or not self._match_chain:
            return {}, rec_matches

        results: Dict[str, Dict] = {}
        record_map = {row.get("序号"): row.to_dict() for _, row in record_df.iterrows()}
        total = len(unmatched_invoices)

        for i in range(0, total, self._batch_size):
            batch = unmatched_invoices[i:i + self._batch_size]
            inv_lines, rec_lines = self._format_llm_input(batch, record_df)

            try:
                batch_result = self._match_chain.invoke({"invoice_lines": inv_lines, "record_lines": rec_lines})
            except (ValueError, RuntimeError) as exc:
                logger.error("LLM 匹配失败: %s", exc)
                for item in batch:
                    results[item.get("旧文件名", "")] = {"是否匹配": "未匹配"}
                continue

            for idx, item in enumerate(batch):
                inv_name = item.get("旧文件名", "")
                inv_amt = round(float(item.get("价税合计", 0)), 2)
                match_val = batch_result.get(f"I{idx + 1}", "未匹配")

                if match_val == "未匹配" or not str(match_val).startswith("R"):
                    results[inv_name] = {"是否匹配": "未匹配"}
                    continue

                try:
                    rec_seq = int(str(match_val)[1:])
                except ValueError:
                    results[inv_name] = {"是否匹配": "未匹配"}
                    continue

                if rec_seq not in record_map:
                    results[inv_name] = {"是否匹配": "未匹配"}
                    continue

                record = record_map[rec_seq]
                results[inv_name] = _inv_match(rec_seq, pd.Series(record), "LLM智能")

                rm = rec_matches.setdefault(rec_seq, _empty_rec_match())
                rm["匹配发票"].append(inv_name)
                rm["匹配发票金额"].append(inv_amt)
                rm["匹配方式"] = "LLM智能"

                total_inv = round(sum(rm["匹配发票金额"]), 2)
                diff = round(float(record.get("金额", 0)) - total_inv, 2)
                rm["是否匹配"] = "已匹配" if abs(diff) < 0.01 else "部分匹配"
                rm["未匹配金额"] = [] if abs(diff) < 0.01 else [diff]

            logger.info("LLM 匹配进度: %d/%d", min(i + self._batch_size, total), total)
        return results, rec_matches

    @staticmethod
    def _format_llm_input(invoices, record_df):
        rec_lines = [f"R{row.get('序号', 'X')}. {' | '.join(f'{k}:{v}' for k, v in row.dropna().to_dict().items() if k != '序号')}"
                     for _, row in record_df.iterrows()]
        inv_lines = [f"I{i}. {' | '.join(f'{k}:{v}' for k, v in item.items() if v not in (None, '', []))}"
                     for i, item in enumerate(invoices, 1)]
        return "\n".join(inv_lines), "\n".join(rec_lines)

    # ------------------------------------------------------------------
    # 结果构建
    # ------------------------------------------------------------------

    @staticmethod
    def _build_invoice_result(df, matches):
        result = df.copy()
        for fld in ("匹配序号", "匹配姓名", "匹配简介", "匹配方式", "组合金额"):
            result[fld] = result["旧文件名"].apply(lambda x, f=fld: matches.get(x, {}).get(f, ""))
        result["是否匹配"] = result["旧文件名"].apply(lambda x: matches.get(x, {}).get("是否匹配", "未匹配"))
        return result

    @staticmethod
    def _build_record_result(df, rec_matches):
        result = df.copy()
        def _get(seq, field, join_char=None):
            val = rec_matches.get(seq, {}).get(field, [] if join_char else "")
            if join_char and isinstance(val, list):
                return join_char.join(map(str, val))
            return val or ""

        result["匹配发票"] = result["序号"].apply(lambda s: _get(s, "匹配发票", ","))
        result["匹配发票金额"] = result["序号"].apply(lambda s: _get(s, "匹配发票金额", "+"))
        result["是否匹配"] = result["序号"].apply(lambda s: _get(s, "是否匹配"))
        result["匹配方式"] = result["序号"].apply(lambda s: _get(s, "匹配方式"))
        result["组合金额"] = result["序号"].apply(lambda s: _get(s, "组合金额"))
        result["备注分解金额"] = result["序号"].apply(lambda s: "+".join(map(str, rec_matches.get(s, {}).get("备注分解金额") or [])))
        result["未匹配金额"] = result["序号"].apply(lambda s: "+".join(map(str, rec_matches.get(s, {}).get("未匹配金额") or [])))
        return result

    @staticmethod
    def _print_summary(inv_df, rec_df, all_matches):
        inv_matched = (inv_df["是否匹配"] == "已匹配").sum()
        rec_matched = (rec_df["是否匹配"] == "已匹配").sum()
        rule_kw = ("金额精确", "备注分解", "发票组合")
        rule_count = sum(1 for v in all_matches.values() if any(kw in v.get("匹配方式", "") for kw in rule_kw))
        llm_count = sum(1 for v in all_matches.values() if v.get("匹配方式") == "LLM智能")

        print(f"\n{'=' * 60}\n匹配完成！")
        print(f"【发票】{len(inv_df)} 张 | 已匹配 {inv_matched} (规则 {rule_count}, LLM {llm_count}) | 未匹配 {len(inv_df) - inv_matched}")
        print(f"【记录】{len(rec_df)} 条 | 已匹配 {rec_matched} | 未匹配 {len(rec_df) - rec_matched}\n{'=' * 60}")


# =========================================================================
# 便捷入口
# =========================================================================

def match_and_save(settings=None) -> None:
    """从数据库读取数据，执行匹配并写回。"""
    settings = settings or Settings.from_env()
    invoice_db, record_db = get_invoice_db(settings), get_record_db(settings)

    invoice_df = invoice_db.get_ocr_dataframe()
    if invoice_df.empty:
        logger.error("发票数据库无 OCR 结果")
        return

    record_df = record_db.to_dataframe()
    if record_df.empty:
        logger.error("记录数据库为空")
        return

    matcher = SmartInvoiceRecordMatcher(settings=settings)
    inv_result, rec_result = matcher.match(invoice_df, record_df)
    matcher.match_to_db(inv_result, rec_result)