"""
命令行入口
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import defaultdict
from pathlib import Path

from invoice_toolkit.config import Settings

logger = logging.getLogger(__name__)

_COMMANDS = [
    "classify", "move", "match", "classify-move",
    "pipeline", "check", "check-names", "agent", "clean", "rag",
]


def _setup_logging(verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def cmd_classify(settings: Settings) -> None:
    from invoice_toolkit.classifier import classify_and_save
    classify_and_save(settings)


def cmd_move(settings: Settings) -> None:
    from invoice_toolkit.classifier import move_from_classification
    move_from_classification(settings)


def cmd_classify_move(settings: Settings) -> None:
    from invoice_toolkit.classifier import classify_and_save, move_from_classification
    classify_and_save(settings)
    if input(f"\n{'=' * 60}\n分类完成，是否继续移动文件？(y/n): ").strip().lower() == "y":
        move_from_classification(settings)
    else:
        logger.info("已跳过移动步骤")


def cmd_match(settings: Settings) -> None:
    import pandas as pd
    from invoice_toolkit.ocr import InvoiceOCRProcessor
    from invoice_toolkit.matcher import SmartInvoiceRecordMatcher
    from invoice_toolkit.database import get_invoice_db, get_record_db, dataframe_to_records
    from invoice_toolkit.file_utils import drop_columns, filter_by_column, get_column_values

    processor = InvoiceOCRProcessor(settings)
    processor.run_all_checks()
    get_invoice_db(settings).backfill_full_path(settings.paths.source_root)

    invoice_data = drop_columns(
        pd.DataFrame(processor.result),
        ["购方名称", "商品单价", "新文件名", "购方税号"],
    )
    hand_data = pd.DataFrame(processor.hand_data)
    if "序号" not in hand_data.columns:
        hand_data["序号"] = range(1, len(hand_data) + 1)

    # 写入记录数据库
    record_db = get_record_db(settings)
    if record_db.count_records() == 0:
        logger.info("写入 %d 条记录", len(hand_data))
        record_db.upsert_records(dataframe_to_records(hand_data))
    else:
        logger.info("记录数据库已有数据，跳过写入")

    hand_for_match = drop_columns(hand_data, ["填写日期"])
    if "序号" not in hand_for_match.columns:
        hand_for_match["序号"] = range(1, len(hand_for_match) + 1)

    names = get_column_values(invoice_data, "姓名/公司")
    matcher = SmartInvoiceRecordMatcher(settings, use_llm=True)

    inv_results, rec_results = [], []
    for name in names:
        print(f"\n正在处理: {name}")
        inv_df, rec_df = matcher.match(
            filter_by_column(invoice_data, "姓名/公司", name),
            filter_by_column(hand_for_match, "姓名/公司", name),
        )
        inv_results.append(inv_df)
        rec_results.append(rec_df)

    all_inv = pd.concat(inv_results, ignore_index=True)
    all_hand = pd.concat(rec_results, ignore_index=True)

    # 公司/其他记录匹配
    company_inv = filter_by_column(all_inv, "是否匹配", "未匹配")
    company_rec = filter_by_column(hand_for_match, "姓名/公司", lambda x: ~x.isin(settings.NAME_LIST))
    if len(company_inv) > 0 and len(company_rec) > 0:
        print("\n正在处理: 公司/其他记录")
        ci, cr = matcher.match(company_inv, company_rec)
        all_inv = pd.concat([filter_by_column(all_inv, "是否匹配", lambda x: x != "未匹配"), ci], ignore_index=True)
        all_hand = pd.concat([all_hand, cr], ignore_index=True)

    matcher.match_to_db(all_inv, all_hand)


def cmd_pipeline(settings: Settings) -> None:
    stages = [
        ("1/5", "OCR 识别 + 报销匹配", cmd_match),
        ("2/5", "发票文件分类", cmd_classify),
        ("3/5", "按分类移动文件", cmd_move),
        ("4/5", "附件完整性检查", cmd_check),
        ("5/5", "文件名规范检查", cmd_check_names),
    ]
    for i, (label, desc, func) in enumerate(stages):
        print(f"\n{'=' * 60}\n  [{label}] {desc}\n{'=' * 60}")
        func(settings)
        if i < len(stages) - 1:
            if input(f"\n[{label}] 完成。继续？(y/n): ").strip().lower() != "y":
                logger.info("用户中断于: %s", desc)
                return
    print(f"\n{'=' * 60}\n  完整流程执行完毕！\n{'=' * 60}")


def cmd_check(settings: Settings) -> None:
    from invoice_toolkit.checker import AttachmentChecker
    checker = AttachmentChecker(settings)
    report = checker.check_all()
    checker.save_report(report)

    print(f"\n{'=' * 60}\n  附件完整性检查结果\n{'=' * 60}")
    totals = {"all": 0, "missing": 0, "gen": 0, "fail": 0, "fixed": 0}

    for cat, items in report.items():
        if not items:
            continue
        by_status = defaultdict(list)
        for item in items:
            by_status[item["状态"]].append(item)
        totals["all"] += len(items)
        totals["missing"] += len(by_status.get("缺少附件", []))
        totals["gen"] += len(by_status.get("已自动生成", []))
        totals["fail"] += len(by_status.get("附件校验不通过", []))
        totals["fixed"] += len(by_status.get("附件已修复", []))

        print(f"\n【{cat}】{len(items)} 张发票")
        if ok := by_status.get("附件齐全"):
            print(f"  ✓ {len(ok)} 张齐全")
        for item in by_status.get("已自动生成", []):
            print(f"  ✓ {item['发票文件']} → {Path(item.get('生成文件', '')).name}")
        for item in by_status.get("附件已修复", []):
            print(f"  ⚠ {item['发票文件']} — {item.get('校验详情', '')}")
        for item in by_status.get("附件校验不通过", []):
            print(f"  ✗ {item['发票文件']} — {item.get('校验详情', '')}")
        for item in by_status.get("缺少附件", []):
            print(f"  ✗ {item['发票文件']} ← 缺少: {item['缺少类型']}")

    t = totals
    print(f"\n总计: {t['all']} 张，{t['missing']} 缺少，{t['gen']} 已生成，{t['fixed']} 已修复，{t['fail']} 校验失败")
    if t["missing"] == 0 and t["fail"] == 0 and t["all"] > 0:
        print("所有发票附件齐全且校验通过！")
    print("=" * 60)


def cmd_check_names(settings: Settings) -> None:
    from invoice_toolkit.checker import AttachmentChecker
    checker = AttachmentChecker(settings)
    results = checker.check_filenames(dry_run=True)
    checker.save_filename_report(results)

    total = len(results)
    ok = sum(1 for r in results if r["状态"] == "文件名规范")
    need = sum(1 for r in results if r["状态"] == "建议重命名")

    print(f"\n{'=' * 60}\n  发票文件名规范检查\n{'=' * 60}")
    print(f"\n共 {total} 个文件，{ok} 个规范" + (f"，{need} 个需修正" if need else ""))

    by_cat = defaultdict(list)
    for r in results:
        by_cat[r["类别"]].append(r)
    for cat, items in by_cat.items():
        bad = [i for i in items if i["状态"] != "文件名规范"]
        if not bad:
            print(f"\n【{cat}】{len(items)} 个，全部规范 ✓")
            continue
        print(f"\n【{cat}】{len(items)} 个，{len(bad)} 个需修正:")
        for r in bad[:10]:
            print(f"  {r['当前文件名']} → {r['建议文件名']}  ({r['修正原因']})")
        if len(bad) > 10:
            print(f"    ... 还有 {len(bad) - 10} 个")

    if need and input("\n执行重命名？(y/n): ").strip().lower() == "y":
        results = checker.check_filenames(dry_run=False)
        checker.save_filename_report(results)
        print(f"\n已重命名 {sum(1 for r in results if r['已重命名'])} 个文件")
    elif total > 0 and not need:
        print("\n所有文件名均已规范！")
    print("=" * 60)


def cmd_agent(settings: Settings) -> None:
    from invoice_toolkit.agent import InvoiceAgent
    InvoiceAgent(settings=settings, verbose=True).interactive_session()


def cmd_clean(settings: Settings) -> None:
    from invoice_toolkit.file_utils import clean_project_data
    paths = settings.paths
    print(f"{'=' * 60}\n  即将清理:")
    print(f"  1. 源文件  — {paths.source_root}")
    print(f"  2. 输出    — {paths.output_dir}")
    print(f"  3. 缓存    — {paths.cache_dir}\n{'=' * 60}")

    if input("\n确认清理？不可撤销！(y/n): ").strip().lower() != "y":
        print("已取消。")
        return
    stats = clean_project_data(settings, dry_run=False)
    failed = stats["failed"]
    print(f"\n清理完成: {stats['source_files']} 源文件, {stats['output_files']} 输出, {stats['cache_items']} 缓存")
    if stats.get("db_cleaned"):
        print("  数据库已清空")
    if failed:
        print(f"\n⚠ {len(failed)} 个文件被占用，请关闭相关程序后重试。")


def cmd_rag(settings: Settings) -> None:
    from invoice_toolkit.rag import ReimbursementQA
    ReimbursementQA(settings=settings).interactive_session()


_MENU_MAP = {
    "1": "match", "2": "classify", "3": "move", "4": "check", "5": "check-names",
    "6": "classify-move", "7": "pipeline", "8": "agent", "9": "rag", "10": "clean",
}

_DISPATCH = {
    "classify": cmd_classify, "move": cmd_move, "match": cmd_match,
    "classify-move": cmd_classify_move, "pipeline": cmd_pipeline,
    "check": cmd_check, "check-names": cmd_check_names,
    "agent": cmd_agent, "clean": cmd_clean, "rag": cmd_rag,
}


def _interactive_menu() -> str | None:
    print(f"{'=' * 60}\n  发票识别、分类与报销记录匹配工具\n{'=' * 60}\n")
    print("  单步: 1.match  2.classify  3.move  4.check  5.check-names")
    print("  组合: 6.classify-move  7.pipeline")
    print("  智能: 8.agent  9.rag")
    print("  维护: 10.clean  0.退出\n")
    return _MENU_MAP.get(input("选项 (0-10): ").strip())


def main() -> None:
    parser = argparse.ArgumentParser(prog="invoice-toolkit", description="发票识别、分类与报销记录匹配工具")
    parser.add_argument("command", nargs="?", choices=_COMMANDS, default=None)
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--project-root", type=str, default=None)
    args = parser.parse_args()

    command = args.command or _interactive_menu()
    if command is None:
        print("已退出。")
        sys.exit(0)

    _setup_logging(args.verbose)
    settings = Settings.from_env(project_root=Path(args.project_root) if args.project_root else None)
    settings.paths.ensure_dirs()
    _DISPATCH[command](settings)


if __name__ == "__main__":
    main()
