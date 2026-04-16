"""
发票分类模块（LangChain 版）— 基于报销记录物品简介进行智能分类。
"""

from __future__ import annotations

import logging
from typing import Dict, List, Tuple
from pathlib import Path

import pandas as pd
from langchain_core.prompts import ChatPromptTemplate

from invoice_toolkit.config import Settings
from invoice_toolkit.database import get_invoice_db, get_record_db
from invoice_toolkit.file_utils import move_files_to_categories, print_classification_summary
from invoice_toolkit.llm_client import LLMClient

logger = logging.getLogger(__name__)

# =========================================================================
# LangChain Prompt
# =========================================================================

CLASSIFY_PROMPT = ChatPromptTemplate.from_messages([
    ("system", "你是报销记录分类专家，根据物品简介、报销人和发票辅助信息分类，只返回JSON。"),
    ("human", """\
你是报销分类助手。根据【物品简介】、【报销人】和【发票辅助信息】对报销记录分类。
【物品简介判断优先级最高】，按优先级命中后立即停止。

## 分类规则（按优先级）：

### 1. 出差（最高优先级）
满足任一 → 直接归为【出差】：
- 【发票路径】含非"济南"地名
- 【物品简介】或【发票路径】含"出差"
- 【物品简介】含火车/高铁/机票/住宿/差旅
- 【物品简介】含"垫付"且关联地名或国际会议

### 2. 加班餐（未命中出差时）
- 含"加班餐"或明显餐饮类词汇

### 3. 打车（未命中出差时）
- 含滴滴/出租车/网约车/打车，且路径无非济南地名

### 4. 其他类别
| 类别 | 关键词 |
|------|--------|
| 快递 | 顺丰、快递、运单、邮寄 |
| 论文和专利 | 专利、版面费、年费、论文 |
| 打印 | 打印、复印（不含打印纸、墨盒耗材） |
| 材料 | 实验器材、电子元件、书籍、办公用品、打印纸、耗材 |

返回JSON：{{"1": "类别", "2": "类别", ...}}（键为批次中位置，从1开始）

待分类记录：
{batch_text}"""),
])


class InvoiceClassifier:
    """报销记录分类器"""

    def __init__(self, settings: Settings | None = None, llm_client: LLMClient | None = None) -> None:
        self._settings = settings or Settings.from_env()
        self._categories = self._settings.CATEGORIES
        self._batch_size = self._settings.batch_size
        self._llm = llm_client or LLMClient(self._settings.llm)
        self._chain = self._llm.build_chain(CLASSIFY_PROMPT, output_json=True)

    def classify(self, df: pd.DataFrame) -> Dict[int, str]:
        """对记录 DataFrame 进行分类，返回 {序号: 类别}。"""
        items: List[Tuple[int, str]] = []
        for _, row in df.iterrows():
            parts = [f"物品简介:{row.get('物品简介', '')}", f"报销人:{row.get('姓名/公司', '')}"]
            if v := row.get("发票路径", ""):
                parts.append(f"发票路径:{v}")
            if v := row.get("商品名称", ""):
                clean_name = str(v).strip('[]"').replace('", "', '/')
                parts.append(f"商品名称:{clean_name}")
            if v := row.get("销售方名称", ""):
                parts.append(f"销售方:{v}")
            items.append((int(row.get("序号", 0)), "  ".join(parts)))

        results: Dict[int, str] = {}
        total = len(items)
        for i in range(0, total, self._batch_size):
            batch = items[i:i + self._batch_size]
            batch_text = "\n".join(f"{idx + 1}. {info}" for idx, (_, info) in enumerate(batch))
            try:
                result = self._chain.invoke({"batch_text": batch_text})
            except (ValueError, RuntimeError) as exc:
                logger.error("批次分类失败: %s", exc)
                result = {}
            for idx, (seq, _) in enumerate(batch):
                cat = result.get(str(idx + 1), "未分类")
                results[seq] = cat if cat in self._categories else "未分类"
            logger.info("已处理 %d/%d 条记录", min(i + self._batch_size, total), total)

        return results


# =========================================================================
# 便捷函数
# =========================================================================

def classify_and_save(settings: Settings | None = None) -> pd.DataFrame:
    """对报销记录分类并保存到数据库。"""
    settings = settings or Settings.from_env()
    invoice_db = get_invoice_db(settings)
    record_db = get_record_db(settings)

    df = record_db.to_dataframe()
    if df.empty:
        logger.error("记录数据库为空，请先执行 match 步骤")
        return df

    logger.info("共 %d 条报销记录待分类", len(df))

    # 关联发票辅助信息
    inv_info = invoice_db.get_invoice_info_for_records()
    for field in ("发票路径", "商品名称", "销售方名称"):
        key_map = {"发票路径": "parent", "商品名称": "商品名称", "销售方名称": "销售方名称"}
        df[field] = df["序号"].apply(
            lambda s: inv_info.get(str(int(s)), {}).get(key_map[field], "")
        ) if inv_info else ""

    # LLM 分类
    classifier = InvoiceClassifier(settings=settings)
    results = classifier.classify(df)
    df["category"] = df["序号"].apply(lambda s: results.get(int(s), "未分类"))

    # 打印摘要
    _print_summary(df)

    # 写入 records.db
    record_db.upsert_category([{"序号": int(row["序号"]), "category": row["category"]} for _, row in df.iterrows()])

    # 反向同步至 invoices.db
    synced = invoice_db.sync_categories_from_records(record_db)
    if synced:
        logger.info("已同步 %d 条发票的类别", synced)
    else:
        logger.warning("未同步任何发票类别，请先执行 match 步骤")

    return df


def move_from_classification(settings: Settings | None = None) -> None:
    """从发票数据库读取 category 并移动文件。"""
    settings = settings or Settings.from_env()
    invoice_db = get_invoice_db(settings)
    df = invoice_db.to_dataframe(where='category != ""')

    if df.empty:
        logger.error("发票数据库中没有分类结果，请先执行 match → classify")
        return

    if "旧文件名" in df.columns and "name" not in df.columns:
        df["name"] = df["旧文件名"]

    required = ["full_path", "name", "parent", "category"]
    if missing := [c for c in required if c not in df.columns]:
        logger.error("数据库缺少必要字段: %s", missing)
        return

    exists_mask = df["full_path"].apply(lambda p: pd.notna(p) and p and Path(p).exists())
    if skipped := (~exists_mask).sum():
        logger.warning("跳过 %d 个不存在的文件", skipped)
    df = df[exists_mask]

    logger.info("共 %d 个文件待移动", len(df))
    print_classification_summary(df)
    move_files_to_categories(df, str(settings.paths.invoice_root), dry_run=True)

    if input("\n是否执行实际移动？(y/n): ").strip().lower() == "y":
        move_files_to_categories(df, str(settings.paths.invoice_root), dry_run=False)
        logger.info("文件移动完成！")
    else:
        logger.info("已取消移动操作")


def _print_summary(df: pd.DataFrame) -> None:
    if df.empty or "category" not in df.columns:
        return
    total = len(df)
    print(f"\n{'=' * 50}\n  记录分类结果汇总\n{'=' * 50}")
    for cat, cnt in df.groupby("category").size().sort_values(ascending=False).items():
        print(f"  {cat:<12} {cnt:>4} 条  ({cnt / total * 100:.1f}%)")
    print(f"\n  总计: {total} 条，未分类: {(df['category'] == '未分类').sum()} 条\n{'=' * 50}")


# 向后兼容别名
move_from_excel = move_from_classification
