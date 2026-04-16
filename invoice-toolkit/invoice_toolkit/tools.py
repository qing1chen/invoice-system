"""
LangChain 工具定义模块

将 OCR 识别、发票分类、报销匹配、附件检查等核心能力封装为 LangChain Tool，
使其可被 Agent 统一调度，也可作为独立函数直接调用。

数据持久化：
    - 原版：各模块输出到独立 Excel 文件
    - 新版：统一写入发票数据库 (invoices.db) 和记录数据库 (records.db)
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from langchain.tools import tool

from invoice_toolkit.config import Settings

logger = logging.getLogger(__name__)

# 模块级缓存，避免每次 Tool 调用都重建 Settings
_cached_settings: Optional[Settings] = None


def _get_settings() -> Settings:
    global _cached_settings
    if _cached_settings is None:
        _cached_settings = Settings.from_env()
        _cached_settings.paths.ensure_dirs()
    return _cached_settings


def set_settings(settings: Settings) -> None:
    """外部注入 Settings（Agent 初始化时调用）"""
    global _cached_settings
    _cached_settings = settings


# =========================================================================
# Tool 1: 目录扫描
# =========================================================================

@tool
def scan_invoice_directory(directory: str = "") -> str:
    """扫描发票源目录，返回文件清单统计。
    不传参数则扫描默认源目录 data/课题组成员文件/。
    返回各子目录下的文件数量统计。"""
    from invoice_toolkit.file_utils import build_flat_file_table

    settings = _get_settings()
    scan_path = directory or str(settings.paths.source_root)

    df = build_flat_file_table(scan_path)
    if df.empty:
        return f"目录 {scan_path} 下没有找到任何文件。"

    # 按 parent 分组统计
    summary = df.groupby("parent").size().to_dict()
    lines = [f"扫描目录: {scan_path}", f"共 {len(df)} 个文件:"]
    for parent, count in sorted(summary.items()):
        lines.append(f"  {parent}: {count} 个文件")

    return "\n".join(lines)


# =========================================================================
# Tool 2: OCR 识别
# =========================================================================

@tool
def run_ocr_recognition(dummy: str = "") -> str:
    """对源目录中的发票文件执行 OCR 识别。
    自动处理 PDF 和图片格式，结果保存到发票数据库。
    如果已有缓存结果则直接加载，不会重复识别。"""
    from invoice_toolkit.ocr import InvoiceOCRProcessor

    settings = _get_settings()
    processor = InvoiceOCRProcessor(settings)
    processor.run_all_checks()

    # FIX: OCR 仅记录相对路径，此处补全 full_path，
    # 确保后续分类和文件移动能正确定位文件。
    from invoice_toolkit.database import get_invoice_db
    invoice_db = get_invoice_db(settings)
    invoice_db.backfill_full_path(settings.paths.source_root)

    recognized = len(processor.result)
    unrecognized = len(processor.unrecognized_files)

    return (
        f"OCR 识别完成:\n"
        f"  成功识别: {recognized} 张发票\n"
        f"  未识别文件: {unrecognized} 个\n"
        f"  结果已保存到发票数据库"
    )


# =========================================================================
# Tool 3: 报销匹配
# =========================================================================

@tool
def run_invoice_matching(dummy: str = "") -> str:
    """执行发票与报销记录的智能匹配（OCR + 规则 + LLM）。
    先运行 OCR 识别，再通过规则匹配和 LLM 智能匹配建立发票与报销记录的对应关系。
    结果保存到发票数据库和记录数据库。"""
    from invoice_toolkit.cli import cmd_match

    settings = _get_settings()
    cmd_match(settings)

    return (
        f"匹配完成:\n"
        f"  发票匹配结果: 已保存到发票数据库\n"
        f"  记录匹配结果: 已保存到记录数据库"
    )


# =========================================================================
# Tool 4: 发票分类
# =========================================================================

@tool
def run_invoice_classification(dummy: str = "") -> str:
    """对发票文件进行智能分类（出差/打车/加班餐/材料/快递/打印/论文和专利）。
    基于文件路径、文件名和匹配简介，由 LLM 判断发票类别。
    结果保存到发票数据库。"""
    from invoice_toolkit.classifier import classify_and_save

    settings = _get_settings()
    df = classify_and_save(settings)

    # FIX: classify_and_save 返回的列名是 'category'（不是 '类别'），
    # 且当记录数据库为空时可能返回无 category 列的空 df。
    if "category" in df.columns:
        counts = df["category"].value_counts().to_dict()
    else:
        counts = {}
    lines = ["分类完成:"]
    for cat, cnt in sorted(counts.items(), key=lambda x: -x[1]):
        lines.append(f"  {cat}: {cnt} 条")
    lines.append("结果已保存到发票数据库")

    return "\n".join(lines)


# =========================================================================
# Tool 5: 文件移动
# =========================================================================

@tool
def run_file_move(confirm: str = "n") -> str:
    """根据分类结果将发票文件复制到 output/发票/ 下对应的类别目录。
    传入 confirm='y' 执行实际移动，否则仅预览。"""
    import pandas as pd
    from pathlib import Path
    from invoice_toolkit.file_utils import move_files_to_categories
    from invoice_toolkit.database import get_invoice_db

    settings = _get_settings()
    paths = settings.paths

    # 从数据库加载分类结果
    invoice_db = get_invoice_db(settings)
    df = invoice_db.to_dataframe(where='category != ""')

    if df.empty:
        return "数据库中没有分类结果，请先执行分类。"

    # 兼容旧字段名
    if "旧文件名" in df.columns and "name" not in df.columns:
        df["name"] = df["旧文件名"]

    required = ["full_path", "name", "parent", "category"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        return f"数据库缺少必要字段: {missing}"

    exists_mask = df["full_path"].apply(lambda p: bool(pd.notna(p) and p and Path(str(p)).exists()))
    df = df[exists_mask]

    if confirm.strip().lower() == "y":
        move_files_to_categories(df, str(paths.invoice_root), dry_run=False)
        return f"已将 {len(df)} 个文件移动到 {paths.invoice_root}"
    else:
        move_files_to_categories(df, str(paths.invoice_root), dry_run=True)
        return f"预览模式: {len(df)} 个文件待移动到 {paths.invoice_root}，传入 confirm='y' 执行实际移动。"


# =========================================================================
# Tool 6: 附件完整性检查
# =========================================================================

@tool
def check_attachments(dummy: str = "") -> str:
    """检查分类后的发票是否有对应的附件文件。
    检查规则：
    - 打车类发票：每张发票应有对应的行程单
    - 加班餐发票：每张发票应有对应的情况说明（缺少时自动从模板生成 .docx）
      额外校验：金额匹配、人数×30≥金额、人名在名单中，不通过时自动修复
    - 打印类发票：应有对应的打印明细
    - 快递类发票：应有对应的快递明细
    结果保存到发票数据库。"""
    from invoice_toolkit.checker import AttachmentChecker

    settings = _get_settings()
    checker = AttachmentChecker(settings)
    report = checker.check_all()
    checker.save_report(report)

    # 构建摘要
    lines = ["附件完整性检查完成:"]
    total_all, total_ok, total_missing, total_gen, total_fail, total_fixed = 0, 0, 0, 0, 0, 0

    for category, items in report.items():
        if not items:
            continue

        ok = [i for i in items if i["状态"] == "附件齐全"]
        missing = [i for i in items if i["状态"] == "缺少附件"]
        generated = [i for i in items if i["状态"] == "已自动生成"]
        fixed = [i for i in items if i["状态"] == "附件已修复"]
        validation_fail = [i for i in items if i["状态"] == "附件校验不通过"]

        total_all += len(items)
        total_ok += len(ok)
        total_missing += len(missing)
        total_gen += len(generated)
        total_fail += len(validation_fail)
        total_fixed += len(fixed)

        parts = [f"共 {len(items)} 张发票"]
        if ok:
            parts.append(f"{len(ok)} 张齐全")
        if missing:
            parts.append(f"{len(missing)} 张缺少附件")
        if generated:
            parts.append(f"{len(generated)} 张已自动生成")
        if fixed:
            parts.append(f"{len(fixed)} 张已修复")
        if validation_fail:
            parts.append(f"{len(validation_fail)} 张校验不通过")
        lines.append(f"  【{category}】{'，'.join(parts)}")

        for m in missing:
            lines.append(f"    ✗ {m['发票文件']} — 缺少: {m['缺少类型']}")
        for g in generated:
            lines.append(f"    ✓ {g['发票文件']} — 已生成: {g.get('生成文件', '')}")
        for f_item in fixed:
            lines.append(f"    ⚠ {f_item['发票文件']} — 已修复: {f_item.get('校验详情', '')}")
        for v in validation_fail:
            lines.append(f"    ✗ {v['发票文件']} — 校验失败: {v.get('校验详情', '')}")

    if not any(report.values()):
        lines.append("  所有类别均无文件需要检查。")

    if total_all:
        lines.append(
            f"\n合计: {total_all} 张发票，"
            f"{total_ok} 张齐全，{total_missing} 张缺少附件，"
            f"{total_gen} 张已自动生成，{total_fixed} 张已修复，"
            f"{total_fail} 张校验不通过"
        )
    lines.append("详细报告已保存到发票数据库")
    return "\n".join(lines)


# =========================================================================
# Tool 7: 清理数据
# =========================================================================

@tool
def clean_project_data(confirm: str = "n") -> str:
    """清理项目生成的数据（源文件/输出/缓存/数据库），恢复到初始状态。
    传入 confirm='y' 执行实际清理，否则仅预览。"""
    from invoice_toolkit.file_utils import clean_project_data as _clean

    settings = _get_settings()
    dry_run = confirm.strip().lower() != "y"
    stats = _clean(settings, dry_run=dry_run)

    mode = "预览" if dry_run else "已清理"
    return (
        f"{mode}:\n"
        f"  源文件: {stats['source_files']} 个\n"
        f"  输出文件: {stats['output_files']} 个\n"
        f"  缓存条目: {stats['cache_items']} 个\n"
        f"  数据库: {'已清空' if not dry_run and stats.get('db_cleaned') else '未操作'}\n"
        f"  失败: {len(stats['failed'])} 个"
    )


# =========================================================================
# Tool 8: 文件名规范检查
# =========================================================================

@tool
def check_invoice_filenames(confirm: str = "n") -> str:
    """检查发票文件名是否符合「报销人+金额+用途」规范，并可自动修正。
    检查 output/发票/ 下各分类目录中的发票文件。
    规范格式：报销人+金额+用途.ext（用 "+" 连接）。
    结合 OCR 识别结果和匹配信息，通过 LLM 智能生成正确的文件名。
    传入 confirm='y' 执行实际重命名，否则仅预览。
    结果保存到发票数据库。"""
    from invoice_toolkit.checker import AttachmentChecker

    settings = _get_settings()
    checker = AttachmentChecker(settings)
    dry_run = confirm.strip().lower() != "y"
    results = checker.check_filenames(dry_run=dry_run)
    checker.save_filename_report(results)

    # 构建摘要
    mode = "预览" if dry_run else "执行"
    total = len(results)
    ok = sum(1 for r in results if r["状态"] == "文件名规范")
    renamed = sum(1 for r in results if r["已重命名"])
    need_rename = sum(1 for r in results if r["状态"] in ("建议重命名", "待重命名"))

    lines = [f"文件名规范检查完成（{mode}模式）:"]
    lines.append(f"  共检查 {total} 个发票文件")
    lines.append(f"  ✓ {ok} 个文件名已规范")
    if need_rename:
        lines.append(f"  ✗ {need_rename} 个文件名需要修正")
    if renamed:
        lines.append(f"  ✓ {renamed} 个文件已重命名")

    # 展示前 10 个需要修正的示例
    samples = [r for r in results if r["状态"] != "文件名规范"][:10]
    if samples:
        lines.append("\n示例:")
        for r in samples:
            lines.append(f"  {r['当前文件名']}")
            lines.append(f"    → {r['建议文件名']}  ({r['修正原因']})")

    remaining = len([r for r in results if r["状态"] != "文件名规范"]) - len(samples)
    if remaining > 0:
        lines.append(f"  ... 还有 {remaining} 个")

    if dry_run and need_rename:
        lines.append(f"\n传入 confirm='y' 执行实际重命名。")

    lines.append("详细报告已保存到发票数据库")
    return "\n".join(lines)


# =========================================================================
# Tool 9: 报销政策问答（RAG）
# =========================================================================

@tool
def query_reimbursement_policy(question: str) -> str:
    """基于《山东大学经费报销管理办法》回答报销政策问题。
    使用向量检索（RAG）从政策文档中找到相关条款，由 LLM 生成准确回答。
    首次调用会自动构建向量索引（耗时约 30 秒），后续调用直接使用缓存。
    示例问题：出差报销需要哪些材料？加班餐费怎么报销？票据报销期限是多久？"""
    from invoice_toolkit.rag import ReimbursementQA

    settings = _get_settings()
    try:
        qa = ReimbursementQA(settings=settings)
        result = qa.query_with_sources(question)
        answer = result["answer"]
        # 附加来源信息
        sources = set(s["source"] for s in result["sources"])
        if sources:
            answer += f"\n\n📎 参考来源: {', '.join(sources)}"
        return answer
    except FileNotFoundError as exc:
        return f"未找到政策文档: {exc}\n请将政策文档（.docx/.pdf/.txt）放入 model/ 目录。"
    except Exception as exc:
        logger.error("RAG 查询失败: %s", exc)
        return f"查询失败: {exc}"


# =========================================================================
# Tool 10: 重建向量索引
# =========================================================================

@tool
def rebuild_rag_index(dummy: str = "") -> str:
    """重建报销政策文档的向量索引。
    当 model/ 目录下的政策文档有变更时，使用此工具刷新索引。"""
    from invoice_toolkit.rag import ReimbursementQA

    settings = _get_settings()
    try:
        qa = ReimbursementQA(settings=settings)
        chunk_count = qa.rebuild()
        info = qa.get_index_info()
        return (
            f"向量索引重建完成:\n"
            f"  文档数量: {info['doc_count']}\n"
            f"  分块数量: {chunk_count}\n"
            f"  索引路径: {info['index_dir']}"
        )
    except Exception as exc:
        logger.error("索引重建失败: %s", exc)
        return f"索引重建失败: {exc}"


# =========================================================================
# 工具注册表
# =========================================================================

ALL_TOOLS = [
    scan_invoice_directory,
    run_ocr_recognition,
    run_invoice_matching,
    run_invoice_classification,
    run_file_move,
    check_attachments,
    clean_project_data,
    check_invoice_filenames,
    query_reimbursement_policy,
    rebuild_rag_index,
]
"""所有可用工具列表，供 Agent 初始化时使用"""