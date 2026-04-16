"""
invoice-toolkit MCP Server

基于 FastMCP 框架，将发票处理核心能力封装为 MCP 工具，
供任意 MCP 客户端（Claude Desktop、Cursor、Claude Code 等）调用。

数据持久化：
    - 发票数据库 (output/invoices.db) — OCR/匹配/分类/附件检查/文件名检查
    - 记录数据库 (output/records.db) — 报销明细/记录匹配结果

服务名称: invoice_toolkit_mcp

工具列表:
    1. invoice_scan_directory      — 扫描发票源目录，查看文件清单
    2. invoice_run_ocr             — 对发票文件执行 OCR 识别
    3. invoice_run_matching        — 执行发票与报销记录的智能匹配
    4. invoice_run_classification  — 对发票进行智能分类
    5. invoice_run_file_move       — 按分类结果移动文件到对应目录
    6. invoice_check_with_rules    — 基于 Skill 规则模板检查附件（支持自然语言规则）
    7. invoice_clean_data          — 清理项目数据
    8. invoice_run_pipeline        — 执行完整流程（OCR→匹配→分类→移动→检查）
    9. invoice_query_policy        — 报销政策智能问答（RAG）
   10. invoice_rebuild_rag_index   — 重建政策文档向量索引
   11. invoice_list_member_files   — 列出课题组成员目录下的文件清单（供前端文件管理）
   12. invoice_read_table          — 读取报销明细数据（从记录数据库，关联发票信息）
   13. invoice_save_table          — 保存报销明细数据到记录数据库
   14. invoice_update_record       — 更新单条报销记录（供前端内联编辑）
   15. invoice_delete_record       — 删除单条报销记录（供前端删除行）
   16. invoice_get_prompt_template — 获取报销提示词模板（Skill 加载）
   17. invoice_calculate_amounts   — 计算转卡金额（按户名分组）
   18. invoice_check_with_rules    — 基于 Skill 规则模板检查附件
   19. invoice_run_reimbursement   — LLM Agent 自主编排报销流程（新）

启动方式:
    # stdio 模式（本地集成）
    python -m invoice_toolkit.mcp_server

    # streamable HTTP 模式（远程访问）
    python -m invoice_toolkit.mcp_server --transport http --port 8000
"""

# FIX: 移除 from __future__ import annotations
# Pydantic v2 在 FastMCP 中需要真实的类型注解来生成 JSON Schema，
# from __future__ import annotations 会将所有注解变为惰性字符串，
# 导致 PydanticUserError: `...` is not fully defined

import argparse
import json
import logging
import os
from pathlib import Path
from typing import Optional, List, Dict, Any

from starlette.middleware.cors import CORSMiddleware

from pydantic import BaseModel, Field, ConfigDict

from mcp.server.fastmcp import FastMCP, Context

logger = logging.getLogger(__name__)

# =========================================================================
# Settings 单例（模块级，惰性初始化）
# =========================================================================

_settings = None


def _get_settings():
    """获取 Settings 单例，首次调用时自动初始化。"""
    global _settings
    if _settings is None:
        from invoice_toolkit.config import Settings
        _settings = Settings.from_env()
        _settings.paths.ensure_dirs()
        logger.info("invoice_toolkit_mcp 已启动, 项目根目录: %s", _settings.paths.project_root)
    return _settings


mcp = FastMCP("invoice_toolkit_mcp", port=8000)


def _format_error(e: Exception) -> str:
    """统一错误格式。"""
    return f"错误: {type(e).__name__} — {e}"


# =========================================================================
# 输入模型 (Pydantic)
# =========================================================================

class ScanDirectoryInput(BaseModel):
    """扫描目录参数。"""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    directory: Optional[str] = Field(
        default=None,
        description="要扫描的目录路径。不传则使用默认源目录 data/课题组成员文件/",
    )


class FileMoveInput(BaseModel):
    """文件移动参数。"""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    confirm: bool = Field(
        default=False,
        description="是否执行实际移动。False 仅预览，True 执行移动",
    )


class CleanDataInput(BaseModel):
    """清理数据参数。"""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    confirm: bool = Field(
        default=False,
        description="是否执行实际清理。False 仅预览，True 执行清理（不可撤销）",
    )


class PipelineInput(BaseModel):
    """完整流程参数。"""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    skip_ocr: bool = Field(
        default=False,
        description="是否跳过 OCR + 匹配步骤（适用于已有识别结果的场景）",
    )
    confirm_move: bool = Field(
        default=False,
        description="是否自动确认文件移动（False 则仅预览移动）",
    )


class RAGQueryInput(BaseModel):
    """报销政策问答参数。"""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    question: str = Field(
        description="关于报销政策的问题，如：出差报销需要哪些材料？",
    )


class CheckFilenamesInput(BaseModel):
    """文件名检查参数。"""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    dry_run: bool = Field(
        default=True,
        description="True 仅预览建议的修改，False 执行实际重命名",
    )


class ListMemberFilesInput(BaseModel):
    """列出成员文件参数。"""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    member: Optional[str] = Field(
        default=None,
        description="指定成员姓名，仅返回该成员目录下的文件。不传则返回全部成员的文件。",
    )


class SaveTableInput(BaseModel):
    """保存报销明细参数。"""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    rows: str = Field(
        description=(
            "JSON 字符串，格式为包含多条记录的数组，每条记录是 "
            "{序号, 姓名/公司, 填写日期, 金额, 物品简介, 备注, 类别} 对象"
        ),
    )


class UpdateRecordInput(BaseModel):
    """更新单条记录参数。"""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    record_id: int = Field(description="记录主键 id（records 表的 id 字段）")
    fields: str = Field(
        description=(
            "JSON 字符串，要更新的字段字典，如 "
            '{"金额": 100, "物品简介": "材料费"}'
        ),
    )


class DeleteRecordInput(BaseModel):
    """删除单条记录参数。"""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    record_id: int = Field(description="记录主键 id（records 表的 id 字段）")


class GetPromptTemplateInput(BaseModel):
    """获取提示词模板参数。"""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    template_name: str = Field(
        default="default",
        description="模板文件名（不含 .md 后缀）。默认为 'default'",
    )


class CalculateAmountsInput(BaseModel):
    """计算转卡金额参数。"""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    record_ids: str = Field(
        description=(
            "选中记录的 db_id 列表，JSON 数组字符串，"
            "如 '[1, 3, 5]'。传空数组 '[]' 则计算全部记录。"
        ),
    )


class CheckWithRulesInput(BaseModel):
    """基于 Skill 规则模板的附件检查参数。"""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    template_name: str = Field(
        default="rules",
        description=(
            "规则模板名称（不含 .md 后缀）。默认 'rules'。"
            "模板位于 skills/attachment-checker/templates/ 目录。"
        ),
    )
    category: Optional[str] = Field(
        default=None,
        description=(
            "要检查的类别名称。不传则检查模板中定义的全部类别。"
            "类别名称需与模板中的 ## 标题一致。"
        ),
    )
    dry_run: bool = Field(
        default=False,
        description="True 仅预览检查结果不写入数据库，False 执行完整检查并写入",
    )
    custom_rule: Optional[str] = Field(
        default=None,
        description=(
            "自定义检查规则（自然语言）。传入时覆盖模板中对应类别的规则。"
            "示例：'加班餐每人最高标准改为50元'"
        ),
    )


# =========================================================================
# Tool 1: 目录扫描
# =========================================================================

@mcp.tool(
    name="invoice_scan_directory",
    annotations={
        "title": "扫描发票目录",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def invoice_scan_directory(params: ScanDirectoryInput) -> str:
    """扫描发票源目录，返回文件清单和统计信息。

    递归扫描指定目录（或默认源目录），按子目录汇总文件数量。
    用于了解当前有哪些发票文件待处理。

    Args:
        params (ScanDirectoryInput): 扫描参数
            - directory (Optional[str]): 自定义扫描路径

    Returns:
        str: 文件清单统计文本
    """
    from invoice_toolkit.file_utils import build_flat_file_table

    try:
        settings = _get_settings()
        scan_path = params.directory or str(settings.paths.source_root)

        df = build_flat_file_table(scan_path)
        if df.empty:
            return f"目录 {scan_path} 下没有找到任何文件。"

        summary = df.groupby("parent").size().to_dict()
        lines = [f"扫描目录: {scan_path}", f"共 {len(df)} 个文件:"]
        for parent, count in sorted(summary.items()):
            lines.append(f"  {parent}: {count} 个文件")

        return "\n".join(lines)
    except Exception as e:
        return _format_error(e)


# =========================================================================
# Tool 2: OCR 识别
# =========================================================================

@mcp.tool(
    name="invoice_run_ocr",
    annotations={
        "title": "OCR 发票识别",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def invoice_run_ocr() -> str:
    """对源目录中的发票文件执行 OCR 识别。

    自动处理 PDF 和图片格式，调用百度 OCR API 识别增值税发票信息。
    如果已有缓存结果则直接加载，不会重复识别。
    结果保存到发票数据库。

    Returns:
        str: OCR 识别结果摘要
    """
    from invoice_toolkit.ocr import InvoiceOCRProcessor

    try:
        settings = _get_settings()
        processor = InvoiceOCRProcessor(settings)
        processor.run_all_checks()

        # FIX: OCR 仅记录相对路径，此处补全 full_path 绝对路径，
        # 确保后续分类和文件移动步骤能正确定位文件。
        # 与 cmd_match (cli.py) 中的调用保持一致。
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
    except Exception as e:
        return _format_error(e)


# =========================================================================
# Tool 3: 报销匹配
# =========================================================================

@mcp.tool(
    name="invoice_run_matching",
    annotations={
        "title": "发票报销匹配",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def invoice_run_matching() -> str:
    """执行发票与报销记录的智能匹配。

    先运行 OCR 识别，再通过规则匹配和 LLM 智能匹配建立发票与报销记录的对应关系。
    匹配策略：金额精确匹配 → 备注分解匹配 → 发票组合匹配 → LLM 语义匹配。
    结果保存到发票数据库和记录数据库。

    Returns:
        str: 匹配结果摘要（含匹配率统计）
    """
    from invoice_toolkit.matcher import match_and_save

    try:
        settings = _get_settings()

        # 匹配前检查记录数据库是否为空，为空则从明细 Excel 写入
        from invoice_toolkit.database import get_record_db, dataframe_to_records
        record_db = get_record_db(settings)
        if record_db.count_records() == 0:
            import pandas as pd
            hand_excel = settings.paths.hand_excel
            if hand_excel.exists():
                df = pd.read_excel(str(hand_excel))
                col_map = {col: str(col).strip() for col in df.columns}
                df = df.rename(columns=col_map)
                if "序号" not in df.columns:
                    df["序号"] = range(1, len(df) + 1)
                record_db.upsert_records(dataframe_to_records(df))
                logger.info("记录数据库为空，已从明细文件写入 %d 条记录", len(df))
            else:
                logger.warning("记录数据库为空且明细文件不存在: %s", hand_excel)

        match_and_save(settings)

        # FIX: 与 cmd_match (cli.py) 保持一致——匹配完成后补全 full_path，
        # OCR 阶段仅记录相对路径，此处推导绝对路径供后续分类和文件移动使用。
        from invoice_toolkit.database import get_invoice_db
        invoice_db = get_invoice_db(settings)
        invoice_db.backfill_full_path(settings.paths.source_root)

        return (
            f"匹配完成:\n"
            f"  发票匹配结果: 已保存到发票数据库\n"
            f"  记录匹配结果: 已保存到记录数据库"
        )
    except Exception as e:
        return _format_error(e)


# =========================================================================
# Tool 4: 发票分类
# =========================================================================

@mcp.tool(
    name="invoice_run_classification",
    annotations={
        "title": "发票智能分类",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def invoice_run_classification() -> str:
    """对发票文件进行智能分类。

    基于文件路径、文件名和匹配简介，由 LLM 判断发票类别：
    出差、打车、加班餐、材料、快递、打印、论文和专利。
    分类规则有优先级：路径判断 > 文件名关键词 > LLM 语义理解。
    结果保存到发票数据库。

    Returns:
        str: 各类别分类数量统计
    """
    from invoice_toolkit.classifier import classify_and_save

    try:
        settings = _get_settings()
        df = classify_and_save(settings)

        counts = df["category"].value_counts().to_dict()
        lines = ["分类完成:"]
        for cat, cnt in sorted(counts.items(), key=lambda x: -x[1]):
            lines.append(f"  {cat}: {cnt} 张")
        lines.append("结果已保存到发票数据库")

        return "\n".join(lines)
    except Exception as e:
        return _format_error(e)


# =========================================================================
# Tool 5: 文件移动
# =========================================================================

@mcp.tool(
    name="invoice_run_file_move",
    annotations={
        "title": "按分类移动发票文件",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def invoice_run_file_move(params: FileMoveInput) -> str:
    """根据分类结果将发票文件复制到对应的类别目录。

    从发票数据库读取分类结果，将文件复制到 output/发票/<类别>/ 下。
    默认仅预览（不实际移动），需明确 confirm=True 才执行。

    Args:
        params (FileMoveInput): 移动参数
            - confirm (bool): 是否执行实际移动

    Returns:
        str: 移动/预览结果
    """
    import os
    import pandas as pd
    from invoice_toolkit.file_utils import move_files_to_categories
    from invoice_toolkit.database import get_invoice_db

    try:
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

        # FIX: 新分类流程（classify_and_save）只同步 category，不写入 full_path/parent。
        # 当 full_path 全为空时，从 OCR 阶段写入的「相对路径」（人名目录）和「旧文件名」推导。
        # 结构：source_root / 相对路径(人名目录) / 旧文件名  →  full_path
        #       parent = 相对路径（人名目录本身，非其 dirname）
        source_root = Path(settings.paths.source_root)
        fp_col_empty = (
            "full_path" not in df.columns
            or df["full_path"].fillna("").eq("").all()
        )
        if fp_col_empty:
            if "相对路径" not in df.columns or "旧文件名" not in df.columns:
                return (
                    "发票数据库缺少路径信息（full_path / 相对路径 / 旧文件名），无法定位文件。\n"
                    "请确认已完整执行 OCR 识别步骤。"
                )
            def _make_full_path(row):
                rel = row["相对路径"] or ""
                fname = row["旧文件名"] or ""
                return str(source_root / rel / fname) if rel else str(source_root / fname)
            df["full_path"] = df.apply(_make_full_path, axis=1)
            # parent 直接用相对路径（人名目录），不要再取 dirname
            df["parent"] = df["相对路径"].fillna(".").replace("", ".")

        required = ["full_path", "name", "parent", "category"]
        missing = [c for c in required if c not in df.columns]
        if missing:
            return f"数据库缺少必要字段: {missing}"

        # FIX: 原来的 lambda 在 full_path='' 时返回 ''（空字符串）而非 False，
        # pandas 将其解释为列名选择器，导致 KeyError。用 bool() 强制转为布尔值。
        exists_mask = df["full_path"].apply(
            lambda p: bool(pd.notna(p) and p and Path(str(p)).exists())
        )
        df = df[exists_mask]

        if params.confirm:
            move_files_to_categories(df, str(paths.invoice_root), dry_run=False)
            return f"已将 {len(df)} 个文件移动到 {paths.invoice_root}"
        else:
            move_files_to_categories(df, str(paths.invoice_root), dry_run=True)
            return (
                f"预览模式: {len(df)} 个文件待移动到 {paths.invoice_root}\n"
                f"确认后传入 confirm=True 执行实际移动。"
            )
    except Exception as e:
        return _format_error(e)


# =========================================================================
# Tool 7: 清理数据
# =========================================================================

@mcp.tool(
    name="invoice_clean_data",
    annotations={
        "title": "清理项目数据",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def invoice_clean_data(params: CleanDataInput) -> str:
    """清理项目生成的数据，恢复到初始状态。

    清理范围：
    - 源文件目录（data/课题组成员文件/）下的文件（保留文件夹结构）
    - output 目录下的所有输出文件
    - cache 目录（完全删除）

    ⚠ 此操作不可撤销！默认仅预览，需 confirm=True 执行。

    Args:
        params (CleanDataInput): 清理参数
            - confirm (bool): 是否执行实际清理

    Returns:
        str: 清理/预览统计
    """
    from invoice_toolkit.file_utils import clean_project_data as _clean

    try:
        settings = _get_settings()
        dry_run = not params.confirm
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
    except Exception as e:
        return _format_error(e)


# =========================================================================
# Tool 8: 完整流程
# =========================================================================

@mcp.tool(
    name="invoice_run_pipeline",
    annotations={
        "title": "执行完整发票处理流程",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def invoice_run_pipeline(params: PipelineInput, ctx: Context) -> str:
    """执行完整的发票处理流程。

    按顺序执行：OCR 识别 → 报销匹配 → 发票分类 → 文件移动 → 附件检查。
    每步完成后报告进度，如某步失败会跳过并继续后续步骤。

    Args:
        params (PipelineInput): 流程参数
            - skip_ocr (bool): 是否跳过 OCR + 匹配步骤
            - confirm_move (bool): 是否自动确认文件移动

    Returns:
        str: 完整流程执行结果摘要
    """
    from invoice_toolkit.matcher import match_and_save
    from invoice_toolkit.classifier import classify_and_save
    from invoice_toolkit.file_utils import move_files_to_categories
    from invoice_toolkit.checker import AttachmentChecker
    import pandas as pd

    settings = _get_settings()
    results = []

    # Step 1: OCR + 匹配
    if not params.skip_ocr:
        try:
            await ctx.report_progress(0.0, "[1/5] OCR 识别 + 报销匹配...")

            # FIX: 与 cmd_match (cli.py) 保持一致——
            # 1) 先运行 OCR，确保发票数据写入 invoices.db；
            # 2) 补全 full_path（OCR 阶段仅记录相对路径）；
            # 3) 若记录数据库为空，从明细 Excel 写入初始记录；
            # 4) 最后执行匹配。
            # 原来的 pipeline 直接调用 match_and_save，跳过了 OCR 初始化和
            # backfill_full_path，导致 invoices.db 为空或缺少 full_path，
            # 后续分类、移动步骤全部失败。
            from invoice_toolkit.ocr import InvoiceOCRProcessor
            from invoice_toolkit.database import get_invoice_db, get_record_db, dataframe_to_records

            # 1) OCR 识别：写入 invoices.db
            _processor = InvoiceOCRProcessor(settings)
            _processor.run_all_checks()

            # 2) 补全 full_path（OCR 仅记录相对路径，此处推导绝对路径）
            _inv_db_step1 = get_invoice_db(settings)
            _inv_db_step1.backfill_full_path(settings.paths.source_root)

            # 3) 记录数据库为空时，从明细 Excel 写入
            _record_db = get_record_db(settings)
            if _record_db.count_records() == 0:
                hand_excel = settings.paths.hand_excel
                if hand_excel.exists():
                    import pandas as _pd
                    _df_excel = _pd.read_excel(str(hand_excel))
                    _df_excel = _df_excel.rename(columns={c: str(c).strip() for c in _df_excel.columns})
                    if "序号" not in _df_excel.columns:
                        _df_excel["序号"] = range(1, len(_df_excel) + 1)
                    _record_db.upsert_records(dataframe_to_records(_df_excel))
                    logger.info("pipeline: 记录数据库为空，已从明细文件写入 %d 条记录", len(_df_excel))
                else:
                    logger.warning("pipeline: 记录数据库为空且明细文件不存在: %s", hand_excel)

            # 4) 执行匹配
            match_and_save(settings)
            results.append("[1/5] OCR + 匹配: ✓ 完成")
        except Exception as e:
            results.append(f"[1/5] OCR + 匹配: ✗ 失败 — {e}")
    else:
        results.append("[1/5] OCR + 匹配: ⏭ 已跳过")

    # Step 2: 分类
    try:
        await ctx.report_progress(0.3, "[2/5] 发票分类...")
        df = classify_and_save(settings)
        # FIX: classify_and_save 在记录为空时提前 return，返回的 df 没有 'category' 列，
        # 此时直接 df["category"] 会抛 KeyError。加列存在检查，给出明确提示。
        if "category" not in df.columns:
            results.append("[2/5] 分类: ✗ 失败 — 记录数据库为空，请先执行 match 步骤")
            df = None
        else:
            counts = df["category"].value_counts().to_dict()
            cat_str = ", ".join(f"{k}:{v}" for k, v in counts.items())
            results.append(f"[2/5] 分类: ✓ 完成 ({cat_str})")
    except Exception as e:
        results.append(f"[2/5] 分类: ✗ 失败 — {e}")
        df = None

    # Step 3: 文件移动
    if df is not None:
        try:
            import os as _os
            await ctx.report_progress(0.5, "[3/5] 文件移动...")

            # FIX: classify_and_save 返回的是 records 表 df，不含 full_path。
            # 重新从发票库读取，并在 full_path 为空时从「相对路径」推导。
            from invoice_toolkit.database import get_invoice_db as _get_inv_db
            _inv_db = _get_inv_db(settings)
            move_df = _inv_db.to_dataframe(where='category != ""')

            # FIX: 空 DataFrame 保护——当 invoices 表中没有已分类记录时，
            # to_dataframe 返回无列的空 DataFrame，后续访问 full_path 会 KeyError。
            if move_df.empty:
                results.append("[3/5] 文件移动: ✗ 跳过 — 发票数据库中没有已分类的发票")
                move_df = None  # 标记跳过，不再执行后续逻辑
            else:
                if "旧文件名" in move_df.columns and "name" not in move_df.columns:
                    move_df["name"] = move_df["旧文件名"]
                _src = Path(settings.paths.source_root)
                if (
                    "full_path" not in move_df.columns
                    or move_df["full_path"].fillna("").eq("").all()
                ) and "相对路径" in move_df.columns and "旧文件名" in move_df.columns:
                    def _make_fp(row):
                        rel = row["相对路径"] or ""
                        fname = row["旧文件名"] or ""
                        return str(_src / rel / fname) if rel else str(_src / fname)
                    move_df["full_path"] = move_df.apply(_make_fp, axis=1)
                    move_df["parent"] = move_df["相对路径"].fillna(".").replace("", ".")

                # 必要字段检查
                if "full_path" not in move_df.columns:
                    results.append(
                        "[3/5] 文件移动: ✗ 跳过 — 发票数据库缺少路径信息"
                        "（full_path / 相对路径），请确认已执行 OCR 步骤"
                    )
                    move_df = None
                else:
                    # FIX: bool() 防止空字符串被 pandas 误解为列名选择器
                    exists_mask = move_df["full_path"].apply(
                        lambda p: bool(pd.notna(p) and p and Path(str(p)).exists())
                    )
                    move_df = move_df[exists_mask]

            # 执行移动（仅当 move_df 仍有效时）
            if move_df is not None:
                dry_run = not params.confirm_move
                move_files_to_categories(
                    move_df, str(settings.paths.invoice_root), dry_run=dry_run
                )
                mode = "已移动" if params.confirm_move else "仅预览"
                results.append(f"[3/5] 文件移动: ✓ {mode} {len(move_df)} 个文件")
        except Exception as e:
            results.append(f"[3/5] 文件移动: ✗ 失败 — {e}")
    else:
        results.append("[3/5] 文件移动: ⏭ 跳过（分类未完成）")

    # Step 4: 附件检查（v5 Skill 编排版）
    try:
        await ctx.report_progress(0.8, "[4/5] 附件完整性检查（Skill 编排）...")

        # v5: AttachmentChecker 内部通过 SKILL.md + rules.md 构建 system prompt，
        # 由 LLM Agent 循环自行编排检查流程，无需外部传入规则字典。
        # skill_dir 定位优先级: settings.paths.attachment_skill_dir > 默认路径
        skill_dir = _find_checker_skill_dir()
        checker = AttachmentChecker(settings, skill_dir=skill_dir)
        report = checker.check_all()
        checker.save_report(report)

        total = sum(len(items) for items in report.values())
        missing = sum(
            1 for items in report.values()
            for i in items if i["状态"] == "缺少附件"
        )
        results.append(f"[4/5] 附件检查: ✓ 完成 (共 {total} 张, {missing} 张缺少)")
    except Exception as e:
        results.append(f"[4/5] 附件检查: ✗ 失败 — {e}")

    await ctx.report_progress(1.0, "流程完成")

    results.append("\n完整流程执行完毕！")
    return "\n".join(results)


# =========================================================================
# Tool 9: 报销政策问答（RAG）
# =========================================================================

@mcp.tool(
    name="invoice_query_policy",
    annotations={
        "title": "报销政策智能问答",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def invoice_query_policy(params: RAGQueryInput) -> str:
    """基于《山东大学经费报销管理办法》回答报销政策问题。

    使用 RAG（向量检索增强生成）从政策文档中检索相关条款，
    结合 LLM 生成准确、有据可查的回答。

    首次调用会自动构建向量索引（约 30 秒），后续使用缓存。

    示例问题：
    - 出差报销需要哪些材料？
    - 加班餐费报销有什么要求？
    - 单张发票金额超过多少需要附合同？
    - 票据丢失了怎么报销？

    Args:
        params (RAGQueryInput): 问答参数
            - question (str): 用户的报销政策问题

    Returns:
        str: 基于政策文档的回答（含参考来源）
    """
    from invoice_toolkit.rag import ReimbursementQA

    try:
        settings = _get_settings()
        qa = ReimbursementQA(settings=settings)
        result = qa.query_with_sources(params.question)
        answer = result["answer"]
        sources = set(s["source"] for s in result["sources"])
        if sources:
            answer += f"\n\n📎 参考来源: {', '.join(sources)}"
        return answer
    except FileNotFoundError as exc:
        return f"未找到政策文档: {exc}\n请将政策文档放入 model/ 目录。"
    except Exception as e:
        return _format_error(e)


# =========================================================================
# Tool 10: 重建向量索引
# =========================================================================

@mcp.tool(
    name="invoice_rebuild_rag_index",
    annotations={
        "title": "重建政策文档向量索引",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def invoice_rebuild_rag_index() -> str:
    """重建报销政策文档的向量索引。

    当 model/ 目录下的政策文档有变更（新增、修改、删除）时，
    调用此工具刷新向量索引，使问答系统使用最新的文档内容。

    Returns:
        str: 重建结果（文档数、分块数、索引路径）
    """
    from invoice_toolkit.rag import ReimbursementQA

    try:
        settings = _get_settings()
        qa = ReimbursementQA(settings=settings)
        chunk_count = qa.rebuild()
        info = qa.get_index_info()
        return (
            f"向量索引重建完成:\n"
            f"  文档数量: {info['doc_count']}\n"
            f"  分块数量: {chunk_count}\n"
            f"  索引路径: {info['index_dir']}"
        )
    except Exception as e:
        return _format_error(e)


# =========================================================================
# Tool 11: 列出课题组成员文件（供前端 FileManager 使用）
# =========================================================================

@mcp.tool(
    name="invoice_list_member_files",
    annotations={
        "title": "列出成员目录文件",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def invoice_list_member_files(params: ListMemberFilesInput) -> str:
    """列出 data/课题组成员文件/ 下各成员目录中的文件清单。

    返回 JSON 格式的文件列表，每个文件包含: id, name, size, owner, category,
    uploadTime, fullPath 等信息，供前端 FileManager 直接使用。

    Args:
        params (ListMemberFilesInput): 查询参数
            - member (Optional[str]): 指定成员姓名过滤

    Returns:
        str: JSON 格式的文件列表
    """
    import os
    import time

    try:
        settings = _get_settings()
        source_root = Path(settings.paths.source_root)

        if not source_root.exists():
            return json.dumps({"files": [], "error": f"目录不存在: {source_root}"}, ensure_ascii=False)

        files = []
        # 遍历成员目录
        for member_dir in sorted(source_root.iterdir()):
            if not member_dir.is_dir():
                continue
            member_name = member_dir.name

            # 如果指定了成员，则过滤
            if params.member and member_name != params.member:
                continue

            for file_path in sorted(member_dir.rglob("*")):
                if not file_path.is_file():
                    continue

                stat = file_path.stat()
                # 生成稳定 ID：基于相对路径的哈希
                rel_path = str(file_path.relative_to(source_root))
                file_id = f"fs_{hash(rel_path) & 0xFFFFFFFF:08x}"

                files.append({
                    "id": file_id,
                    "name": file_path.name,
                    "size": stat.st_size,
                    "owner": member_name,
                    "category": "未分类",
                    "uploadTime": time.strftime(
                        "%Y/%m/%d %H:%M:%S",
                        time.localtime(stat.st_mtime),
                    ),
                    "fullPath": str(file_path),
                    "relativePath": rel_path,
                })

        return json.dumps({"files": files}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"files": [], "error": _format_error(e)}, ensure_ascii=False)


# =========================================================================
# Tool 12: 读取报销明细表（供前端 TableEditor 使用）
# =========================================================================

@mcp.tool(
    name="invoice_read_table",
    annotations={
        "title": "读取报销明细表",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def invoice_read_table() -> str:
    """读取报销明细数据，返回 JSON 格式数据。

    优先从记录数据库读取（关联发票数据库获取类别/匹配附件）；
    若数据库为空，则从 data/明细.xlsx 加载并写入数据库。
    返回格式：{ rows: [...], columns: [...], source: "db"|"xlsx" }

    Returns:
        str: JSON 格式的明细表数据
    """
    import pandas as pd

    try:
        settings = _get_settings()
        from invoice_toolkit.database import get_record_db, get_invoice_db, dataframe_to_records

        record_db = get_record_db(settings)
        invoice_db = get_invoice_db(settings)

        # 优先从数据库读取（含关联发票信息）
        joined_rows = record_db.get_records_joined(invoice_db)

        if joined_rows:
            # 数据库有数据，直接使用
            rows = []
            for rec in joined_rows:
                record = {
                    "id": f"db_{rec['id']}",
                    "db_id": rec["id"],
                }
                for k, v in rec.items():
                    if k in ("updated_at",):
                        continue
                    if v is None:
                        record[k] = ""
                    elif isinstance(v, float) and k == "序号":
                        record[k] = int(v)
                    else:
                        record[k] = v
                rows.append(record)

            columns = [
                "序号", "姓名/公司", "填写日期", "金额", "物品简介",
                "备注", "类别", "匹配发票", "匹配附件",
            ]

            return json.dumps(
                {"rows": rows, "columns": columns, "source": "db"},
                ensure_ascii=False,
                default=str,
            )

        # 数据库为空，降级到 Excel
        data_dir = Path(settings.paths.project_root) / "data"
        excel_path = data_dir / "明细.xlsx"

        if not excel_path.exists():
            return json.dumps(
                {"rows": [], "columns": [], "source": "none",
                 "error": f"数据库为空且未找到: {excel_path}"},
                ensure_ascii=False,
            )

        df = pd.read_excel(str(excel_path))
        col_map = {col: str(col).strip() for col in df.columns}
        df = df.rename(columns=col_map)

        if "序号" not in df.columns:
            df["序号"] = range(1, len(df) + 1)

        # 写入数据库
        record_db.upsert_records(dataframe_to_records(df))

        rows = []
        for idx, row in df.iterrows():
            record = {"id": f"r_{idx}"}
            for col in df.columns:
                val = row[col]
                if pd.isna(val):
                    record[col] = ""
                elif isinstance(val, float) and col in ("序号",):
                    record[col] = int(val)
                else:
                    record[col] = val
            if "序号" not in record or record["序号"] == "":
                record["序号"] = idx + 1
            # 确保新列存在
            record.setdefault("类别", "")
            record.setdefault("匹配发票", "")
            record.setdefault("匹配附件", "")
            rows.append(record)

        columns = [
            "序号", "姓名/公司", "填写日期", "金额", "物品简介",
            "备注", "类别", "匹配发票", "匹配附件",
        ]

        return json.dumps(
            {"rows": rows, "columns": columns, "source": "xlsx"},
            ensure_ascii=False,
            default=str,
        )
    except Exception as e:
        return json.dumps(
            {"rows": [], "columns": [], "source": "error",
             "error": _format_error(e)},
            ensure_ascii=False,
        )


# =========================================================================
# Tool 13: 保存报销明细表（供前端 TableEditor 使用）
# =========================================================================

@mcp.tool(
    name="invoice_save_table",
    annotations={
        "title": "保存报销明细表",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def invoice_save_table(params: SaveTableInput) -> str:
    """将前端编辑后的报销明细数据保存到记录数据库和 data/明细.xlsx。

    接收 JSON 格式的行数据，写入记录数据库并同步更新 Excel 文件。
    写入前会备份原 Excel 文件为 明细.xlsx.bak。

    Args:
        params (SaveTableInput): 保存参数
            - rows (str): JSON 字符串格式的行数据数组

    Returns:
        str: 保存结果
    """
    import pandas as pd
    import shutil

    try:
        settings = _get_settings()
        from invoice_toolkit.database import get_record_db, dataframe_to_records

        # 解析 JSON
        rows = json.loads(params.rows)
        if not isinstance(rows, list):
            return "错误: rows 必须是数组格式"

        # 移除前端内部字段，同时分离有 db_id（已存在）和无 db_id（新增）的行
        rows_to_update = []   # [(db_id, fields_dict), ...]
        rows_to_insert = []   # [fields_dict, ...]
        ids_to_keep: set[int] = set()

        for row in rows:
            db_id = row.get("db_id")
            clean = {
                k: v for k, v in row.items()
                if k not in ("id", "db_id")
            }
            # 前端字段名映射：前端使用「类别」，数据库列名为 category
            if "类别" in clean and "category" not in clean:
                clean["category"] = clean.pop("类别")
            if db_id:
                ids_to_keep.add(int(db_id))
                rows_to_update.append((int(db_id), clean))
            else:
                rows_to_insert.append(clean)

        # ── 写入记录数据库（diff-based，保留行 id，兼容重复序号）──
        # 原实现：delete_all() + upsert_records() 全量替换，重新插入后
        # 所有行获得新 auto-increment id，前端持有的 db_id 全部失效。
        # 同时旧版 ON CONFLICT(序号) 会在序号重复时覆盖行，导致数据丢失。
        # 新实现：
        #   1. 删除前端已移除的行（id 不在 ids_to_keep 中）
        #   2. 更新已有行（按 db_id）
        #   3. 插入新行
        record_db = get_record_db(settings)

        # 1. 删除用户在前端删掉的行
        existing_ids = record_db.get_all_ids()
        ids_to_delete = [i for i in existing_ids if i not in ids_to_keep]
        if ids_to_delete:
            placeholders = ",".join("?" for _ in ids_to_delete)
            record_db._execute(
                f"DELETE FROM records WHERE id IN ({placeholders})",
                tuple(ids_to_delete),
            )
            logger.info("invoice_save_table: 删除 %d 条已移除记录", len(ids_to_delete))

        # 2. 更新已有行
        updated = 0
        for db_id, fields in rows_to_update:
            if record_db.update_record_by_id(db_id, fields):
                updated += 1

        # 3. 插入新行（纯 INSERT，不用 ON CONFLICT）
        inserted = record_db.upsert_records(rows_to_insert)

        # 同步写入 Excel（保持向后兼容）
        # 从数据库重新读取全量数据，确保 Excel 与 DB 完全一致
        all_rows_df = record_db.to_dataframe()
        data_dir = Path(settings.paths.project_root) / "data"
        excel_path = data_dir / "明细.xlsx"

        if excel_path.exists():
            backup_path = excel_path.with_suffix(".xlsx.bak")
            shutil.copy2(str(excel_path), str(backup_path))

        all_rows_df.to_excel(str(excel_path), index=False, engine="openpyxl")

        total = len(existing_ids) - len(ids_to_delete) + inserted
        return json.dumps(
            {
                "success": True,
                "message": (
                    f"保存成功: 更新 {updated} 条，新增 {inserted} 条，"
                    f"删除 {len(ids_to_delete)} 条，共 {total} 条记录已写入数据库和 {excel_path}"
                ),
            },
            ensure_ascii=False,
        )
    except json.JSONDecodeError as e:
        return json.dumps(
            {"success": False, "message": f"JSON 解析失败: {e}"},
            ensure_ascii=False,
        )
    except Exception as e:
        return json.dumps(
            {"success": False, "message": _format_error(e)},
            ensure_ascii=False,
        )


# =========================================================================
# Tool 14: 更新单条记录（供前端 TableEditor 使用）
# =========================================================================

@mcp.tool(
    name="invoice_update_record",
    annotations={
        "title": "更新单条报销记录",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def invoice_update_record(params: UpdateRecordInput) -> str:
    """更新 records 数据库中的单条报销记录。

    按主键 id 定位记录，更新指定字段值。
    已知列直接更新，额外字段存入 extra_fields。

    Args:
        params (UpdateRecordInput): 更新参数
            - record_id (int): 记录主键 id
            - fields (str): JSON 字符串格式的更新字段

    Returns:
        str: 更新结果
    """
    try:
        settings = _get_settings()
        from invoice_toolkit.database import get_record_db

        fields = json.loads(params.fields)
        if not isinstance(fields, dict):
            return "错误: fields 必须是字典格式"

        record_db = get_record_db(settings)
        success = record_db.update_record_by_id(params.record_id, fields)

        if success:
            return json.dumps(
                {"success": True, "message": f"记录 {params.record_id} 已更新"},
                ensure_ascii=False,
            )
        else:
            return json.dumps(
                {"success": False, "message": f"记录 {params.record_id} 更新失败"},
                ensure_ascii=False,
            )
    except json.JSONDecodeError as e:
        return json.dumps(
            {"success": False, "message": f"JSON 解析失败: {e}"},
            ensure_ascii=False,
        )
    except Exception as e:
        return json.dumps(
            {"success": False, "message": _format_error(e)},
            ensure_ascii=False,
        )


# =========================================================================
# Tool 15: 删除单条记录（供前端 TableEditor 使用）
# =========================================================================

@mcp.tool(
    name="invoice_delete_record",
    annotations={
        "title": "删除单条报销记录",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def invoice_delete_record(params: DeleteRecordInput) -> str:
    """从 records 数据库中删除一条报销记录。

    Args:
        params (DeleteRecordInput): 删除参数
            - record_id (int): 记录主键 id

    Returns:
        str: 删除结果
    """
    try:
        settings = _get_settings()
        from invoice_toolkit.database import get_record_db

        record_db = get_record_db(settings)
        success = record_db.delete_record_by_id(params.record_id)

        if success:
            return json.dumps(
                {"success": True, "message": f"记录 {params.record_id} 已删除"},
                ensure_ascii=False,
            )
        else:
            return json.dumps(
                {"success": False, "message": f"记录 {params.record_id} 删除失败"},
                ensure_ascii=False,
            )
    except Exception as e:
        return json.dumps(
            {"success": False, "message": _format_error(e)},
            ensure_ascii=False,
        )


# =========================================================================
# Tool 16: 获取提示词模板（Skill 加载）
# =========================================================================

# 提示词模板 Skill 目录——运行时自动探测：
# 1. 项目内 skills/reimbursement-prompt/templates/
# 2. 或与本文件同级 ../skills/reimbursement-prompt/templates/
_SKILL_TEMPLATE_DIRS: List[Path] = []


def _find_skill_template_dirs() -> List[Path]:
    """惰性查找 Skill 模板目录。"""
    global _SKILL_TEMPLATE_DIRS
    if _SKILL_TEMPLATE_DIRS:
        return _SKILL_TEMPLATE_DIRS

    candidates = [
        Path(__file__).resolve().parent / "skills" / "reimbursement-prompt" / "templates",
        Path(__file__).resolve().parent.parent / "skills" / "reimbursement-prompt" / "templates",
    ]
    # 也检查 Settings 的 project_root（如果已初始化）
    try:
        settings = _get_settings()
        candidates.insert(
            0,
            Path(settings.paths.project_root) / "skills" / "reimbursement-prompt" / "templates",
        )
    except Exception:
        pass

    _SKILL_TEMPLATE_DIRS = [d for d in candidates if d.is_dir()]
    return _SKILL_TEMPLATE_DIRS


# 附件检查 Skill 目录（v5: 指向 Skill 根目录，内含 SKILL.md / references/rules.md / scripts/tools.py）
_CHECKER_SKILL_DIR: Optional[Path] = None


def _find_checker_skill_dir() -> Optional[Path]:
    """惰性查找 attachment-checker Skill 的根目录（v5）。

    v5 的 AttachmentChecker 需要 skill_dir 参数指向 Skill 根目录，
    其中包含 SKILL.md、references/rules.md、scripts/tools.py 等。
    """
    global _CHECKER_SKILL_DIR
    if _CHECKER_SKILL_DIR is not None:
        return _CHECKER_SKILL_DIR

    candidates = []
    try:
        settings = _get_settings()
        # 优先使用 settings 中配置的路径
        configured = getattr(settings.paths, "attachment_skill_dir", None)
        if configured:
            candidates.append(Path(configured))
        candidates.append(
            Path(settings.paths.project_root) / "skills" / "attachment-checker",
        )
    except Exception:
        pass

    candidates.extend([
        Path(__file__).resolve().parent / "skills" / "attachment-checker",
        Path(__file__).resolve().parent.parent / "skills" / "attachment-checker",
    ])

    for d in candidates:
        # v5 Skill 根目录标志: 存在 SKILL.md 或 references/rules.md
        if d.is_dir() and (
            (d / "SKILL.md").exists()
            or (d / "references" / "rules.md").exists()
        ):
            _CHECKER_SKILL_DIR = d
            logger.info("Checker skill dir found: %s", d)
            return d

    logger.warning("Checker skill dir not found, candidates: %s", candidates)
    return None


# 保留旧函数名作为兼容（返回 templates 子目录列表）
_CHECKER_SKILL_DIRS: List[Path] = []


def _find_checker_skill_dirs() -> List[Path]:
    """兼容旧调用：返回 attachment-checker Skill 的 templates 目录列表。"""
    global _CHECKER_SKILL_DIRS
    if _CHECKER_SKILL_DIRS:
        return _CHECKER_SKILL_DIRS

    skill_dir = _find_checker_skill_dir()
    if skill_dir:
        for sub in ("templates", "references"):
            candidate = skill_dir / sub
            if candidate.is_dir():
                _CHECKER_SKILL_DIRS.append(candidate)

    logger.info("Checker template dirs: %s", _CHECKER_SKILL_DIRS)
    return _CHECKER_SKILL_DIRS


@mcp.tool(
    name="invoice_get_prompt_template",
    annotations={
        "title": "获取提示词模板",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def invoice_get_prompt_template(params: GetPromptTemplateInput) -> str:
    """从 Skill 中读取指定名称的报销提示词模板。

    模板文件位于 skills/reimbursement-prompt/templates/ 目录下，
    以 .md 为后缀。默认返回 default.md。

    模板语法：
    - 一次性区段（无标记）：仅执行一次
    - 重复区段（===标题=== ... ======）：为每条记录展开
    - {{变量名}} 引用报销记录或发票字段
    - {{转卡金额}} / {{转卡明细}} 为计算字段，由 invoice_calculate_amounts 注入

    Args:
        params (GetPromptTemplateInput): 模板参数
            - template_name (str): 模板文件名（不含 .md 后缀）

    Returns:
        str: JSON 格式，包含模板内容和元信息
    """
    try:
        name = params.template_name or "default"
        dirs = _find_skill_template_dirs()

        # 在所有候选目录中查找
        for d in dirs:
            md_path = d / f"{name}.md"
            if md_path.exists():
                content = md_path.read_text(encoding="utf-8")
                return json.dumps({
                    "success": True,
                    "template_name": name,
                    "template": content,
                    "source": str(md_path),
                }, ensure_ascii=False)

        # 列出可用模板
        available = []
        for d in dirs:
            available.extend(f.stem for f in d.glob("*.md"))

        return json.dumps({
            "success": False,
            "message": f"模板 '{name}' 不存在",
            "available_templates": available,
            "searched_dirs": [str(d) for d in dirs],
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps(
            {"success": False, "message": _format_error(e)},
            ensure_ascii=False,
        )


# =========================================================================
# Tool 17: 计算转卡金额
# =========================================================================

# 核心计算逻辑已抽取到 Skill 模块：
#   skills/reimbursement-prompt/scripts/calculate_amounts.py
# 通过动态导入调用，前后端及 Agent 编排器共用同一份代码。

def _load_skill_calculate_fn():
    """从 Skill 目录动态加载 calculate_transfer_amounts 函数。"""
    import importlib.util

    if hasattr(_load_skill_calculate_fn, "_cached"):
        return _load_skill_calculate_fn._cached

    candidates = []
    try:
        settings = _get_settings()
        candidates.append(
            Path(settings.paths.project_root)
            / "skills" / "reimbursement-prompt" / "scripts" / "calculate_amounts.py"
        )
    except Exception:
        pass

    this_dir = Path(__file__).resolve().parent
    candidates.extend([
        this_dir / "skills" / "reimbursement-prompt" / "scripts" / "calculate_amounts.py",
        this_dir.parent / "skills" / "reimbursement-prompt" / "scripts" / "calculate_amounts.py",
    ])

    for script_path in candidates:
        if script_path.exists():
            spec = importlib.util.spec_from_file_location(
                "reimbursement_prompt_calculate", str(script_path)
            )
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            fn = mod.calculate_transfer_amounts
            _load_skill_calculate_fn._cached = fn
            logger.info("已加载 Skill 计算模块: %s", script_path)
            return fn

    raise ImportError(
        "找不到 Skill 计算模块 calculate_amounts.py，"
        f"搜索路径: {[str(p) for p in candidates]}"
    )


def _calculate_transfer_amounts(
    records: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """委托给 Skill 模块执行金额计算。"""
    calc_fn = _load_skill_calculate_fn()
    return calc_fn(records)


def _load_skill_agent_fn():
    """加载 Agent 编排函数 run_agent_reimbursement。

    agent_orchestrator 已从 Skill 目录移到后端 invoice_toolkit 包内，
    直接 import 即可，不再需要动态搜索 Skill 目录。
    """
    if hasattr(_load_skill_agent_fn, "_cached"):
        return _load_skill_agent_fn._cached

    from invoice_toolkit.agent_orchestrator import run_agent_reimbursement
    _load_skill_agent_fn._cached = run_agent_reimbursement
    logger.info("已加载 Agent 编排模块: invoice_toolkit.agent_orchestrator")
    return run_agent_reimbursement


@mcp.tool(
    name="invoice_calculate_amounts",
    annotations={
        "title": "计算转卡金额",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def invoice_calculate_amounts(params: CalculateAmountsInput) -> str:
    """从数据库中读取指定记录，按户名分组计算转卡金额。

    将同一「姓名/公司」下的多条记录金额求和，生成：
    - 转卡金额：该户名下所有选中记录的金额总和
    - 转卡明细：各笔金额的加法表达式，如 "100.00+200.00=300.00"

    这些计算字段会注入到记录中，供模板渲染时使用 {{转卡金额}} 和 {{转卡明细}}。

    Args:
        params (CalculateAmountsInput): 计算参数
            - record_ids (str): JSON 数组字符串，选中记录的 db_id 列表

    Returns:
        str: JSON 格式的计算结果
    """
    try:
        settings = _get_settings()
        from invoice_toolkit.database import get_record_db, get_invoice_db

        record_db = get_record_db(settings)
        invoice_db = get_invoice_db(settings)

        # 解析 record_ids
        ids = json.loads(params.record_ids)
        if not isinstance(ids, list):
            return json.dumps(
                {"success": False, "message": "record_ids 必须是数组"},
                ensure_ascii=False,
            )

        # 获取记录（含发票关联信息）
        all_rows = record_db.get_records_joined(invoice_db)

        if ids:
            selected = [r for r in all_rows if r.get("id") in ids]
        else:
            selected = all_rows

        if not selected:
            return json.dumps(
                {"success": False, "message": "未找到匹配的记录"},
                ensure_ascii=False,
            )

        result = _calculate_transfer_amounts(selected)

        return json.dumps({
            "success": True,
            "record_count": len(selected),
            "amount_by_person": result["amount_by_person"],
            "detail_by_person": result["detail_by_person"],
            "enriched_records": result["enriched_records"],
        }, ensure_ascii=False, default=str)
    except json.JSONDecodeError as e:
        return json.dumps(
            {"success": False, "message": f"JSON 解析失败: {e}"},
            ensure_ascii=False,
        )
    except Exception as e:
        return json.dumps(
            {"success": False, "message": _format_error(e)},
            ensure_ascii=False,
        )


# =========================================================================
# Tool 18: 基于 Skill 规则模板的附件检查
# =========================================================================

def _parse_rules_inline(content: str) -> Dict[str, Dict[str, Any]]:
    """
    内联版规则解析：从 Markdown 格式的规则模板解析出结构化检查规则。
    无需 rule_parser 模块，可独立运行。
    """
    import re as _re
    rules: Dict[str, Dict[str, Any]] = {}
    sections = _re.split(r'\n## ', content)

    skip_titles = {
        "通用匹配策略", "异常标记格式", "文件名规范", "发票附件检查规则",
    }

    for section in sections:
        if not section.strip():
            continue
        section_lines = section.strip().split('\n')
        title = section_lines[0].strip().lstrip('#').strip()
        if title in skip_titles or not title:
            continue

        body = '\n'.join(section_lines[1:])
        rule: Dict[str, Any] = {
            "required_attachment": "",
            "invoice_keywords": [],
            "attachment_keywords": [],
            "description": "",
            "auto_generate": False,
            "conditional": False,
            "check_rule_text": "",
        }

        m = _re.search(r'\*\*必需附件\*\*\s*[:：]\s*(.+)', body)
        if m:
            rule["required_attachment"] = m.group(1).strip()

        m = _re.search(r'\*\*发票特征\*\*\s*[:：]\s*(.+)', body)
        if m:
            raw = m.group(1).strip()
            if not (raw.startswith("（") or raw.startswith("(")):
                rule["invoice_keywords"] = [
                    k.strip() for k in _re.split(r'[,，、]', raw) if k.strip()
                ]

        m = _re.search(r'\*\*附件特征\*\*\s*[:：]\s*(.+)', body)
        if m:
            rule["attachment_keywords"] = [
                k.strip() for k in _re.split(r'[,，、]', m.group(1).strip())
                if k.strip()
            ]

        m = _re.search(r'\*\*自动生成\*\*\s*[:：]\s*(.+)', body)
        if m:
            rule["auto_generate"] = m.group(1).strip().lower() in ("是", "yes", "true")

        m = _re.search(r'\*\*数据字段\*\*\s*[:：]\s*(.+)', body)
        if m:
            rule["data_fields"] = [
                k.strip() for k in _re.split(r'[,，、]', m.group(1).strip())
                if k.strip()
            ]

        if "条件" in rule.get("required_attachment", "") or "分级" in body:
            rule["conditional"] = True

        m = _re.search(
            r'\*\*检查规则[^*]*\*\*\s*[:：]\s*(.+?)(?=\n---|\n## |\Z)',
            body, _re.DOTALL,
        )
        if m:
            rule["check_rule_text"] = m.group(1).strip()

        rule["description"] = f"{title}发票需要对应的{rule['required_attachment']}"

        if rule["required_attachment"]:
            rules[title] = rule

    return rules


@mcp.tool(
    name="invoice_check_with_rules",
    annotations={
        "title": "基于规则模板检查附件",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
async def invoice_check_with_rules(params: CheckWithRulesInput) -> str:
    """基于 Skill 规则模板检查发票附件完整性。

    从 skills/attachment-checker/templates/ 读取自然语言检查规则，
    解析后执行附件检查。支持自定义规则覆盖。

    特性：
    - 读取可编辑的 Markdown 规则模板，规则可通过自然语言修改
    - 支持 custom_rule 参数动态覆盖特定类别的检查规则
    - 支持 dry_run 模式预览检查结果

    工作流：
    1. 加载规则模板 → 解析为结构化规则
    2. 如有 custom_rule，覆盖对应类别
    3. 与内置规则合并
    4. 调用 AttachmentChecker 执行检查
    5. 结果写入数据库（dry_run=False 时）

    Args:
        params (CheckWithRulesInput): 检查参数
            - template_name: 规则模板名称
            - category: 指定检查类别
            - dry_run: 是否仅预览
            - custom_rule: 自定义规则（自然语言）

    Returns:
        str: 检查结果摘要
    """
    from invoice_toolkit.checker import AttachmentChecker

    try:
        settings = _get_settings()

        # ── 1. 定位 Skill 目录 ──────────────────────────────
        skill_dir = _find_checker_skill_dir()
        if skill_dir is None:
            return json.dumps({
                "success": False,
                "message": "未找到 attachment-checker Skill 目录（需含 SKILL.md 或 references/rules.md）",
            }, ensure_ascii=False)

        # ── 2. 验证规则模板存在 ──────────────────────────────
        # v5: 规则以 Markdown 文件存储在 Skill 目录内，
        # AttachmentChecker 启动时自动读取并注入 LLM system prompt。
        rules_found = False
        for sub in ("references", "templates"):
            rules_path = skill_dir / sub / f"{params.template_name}.md"
            if rules_path.exists():
                rules_found = True
                break

        if not rules_found:
            available = []
            for sub in ("references", "templates"):
                sub_dir = skill_dir / sub
                if sub_dir.is_dir():
                    available.extend(f.stem for f in sub_dir.glob("*.md"))
            return json.dumps({
                "success": False,
                "message": f"规则模板 '{params.template_name}' 不存在",
                "available_templates": list(set(available)),
                "skill_dir": str(skill_dir),
            }, ensure_ascii=False)

        # ── 3. 处理自定义规则覆盖 ────────────────────────────
        # v5: 自定义规则通过临时修改 rules.md 文件实现，
        # 检查完成后恢复原始内容。
        custom_rule_backup = None
        if params.custom_rule:
            try:
                custom_rule_text = params.custom_rule
                category_hint = f"（类别: {params.category}）" if params.category else ""

                # 在 rules.md 末尾追加自定义规则段
                custom_section = (
                    f"\n\n---\n## 用户自定义规则覆盖 {category_hint}\n\n"
                    f"> 以下规则由用户动态传入，优先级高于上方同类别规则。\n\n"
                    f"{custom_rule_text}\n"
                )
                custom_rule_backup = (rules_path, rules_path.read_text(encoding="utf-8"))
                with open(rules_path, "a", encoding="utf-8") as f:
                    f.write(custom_section)
                logger.info("已追加自定义规则到 %s", rules_path)
            except Exception as e:
                logger.warning("追加自定义规则失败: %s，将使用原始规则", e)
                custom_rule_backup = None

        # ── 4. 执行检查（v5 Agent 循环）─────────────────────
        try:
            checker = AttachmentChecker(settings, skill_dir=skill_dir)

            if params.category:
                items = checker.check_category(params.category)
                report = {params.category: items}
            else:
                report = checker.check_all()

            if not params.dry_run:
                checker.save_report(report)
        finally:
            # 恢复 rules.md 原始内容
            if custom_rule_backup:
                backup_path, backup_content = custom_rule_backup
                try:
                    backup_path.write_text(backup_content, encoding="utf-8")
                    logger.info("已恢复 rules.md 原始内容")
                except Exception as e:
                    logger.error("恢复 rules.md 失败: %s", e)

        # ── 5. 生成摘要 ────────────────────────────────────
        lines = [
            f"附件检查完成（模板: {params.template_name}，"
            f"模式: {'预览' if params.dry_run else '已写入数据库'}）:"
        ]

        for category, items in report.items():
            if not items:
                continue
            missing = [i for i in items if i.get("状态") == "缺少附件"]
            generated = [i for i in items if i.get("状态") in ("已自动生成", "需要生成")]
            fixed = [i for i in items if i.get("状态") in ("附件已修复", "需要修复")]
            ok = [i for i in items if i.get("状态") == "附件齐全"]
            fail = [i for i in items if i.get("状态") == "附件校验不通过"]

            parts = [f"共 {len(items)} 张发票"]
            if ok:
                parts.append(f"{len(ok)} 张齐全")
            if missing:
                parts.append(f"{len(missing)} 张缺少附件")
            if generated:
                parts.append(f"{len(generated)} 张已自动生成")
            if fixed:
                parts.append(f"{len(fixed)} 张已修复")
            if fail:
                parts.append(f"{len(fail)} 张校验不通过")

            custom_hint = ""
            if params.custom_rule and (not params.category or params.category == category):
                custom_hint = f" （自定义规则: {params.custom_rule[:40]}…）"

            lines.append(f"  【{category}】{'，'.join(parts)}{custom_hint}")

            for m_item in missing[:3]:
                lines.append(
                    f"    ✗ {m_item.get('发票文件', m_item.get('旧文件名', '?'))}"
                    f" — 缺少: {m_item.get('缺少类型', '?')}"
                )
            for g_item in generated[:3]:
                lines.append(
                    f"    ✓ {g_item.get('发票文件', g_item.get('旧文件名', '?'))}"
                    f" — 已生成: {g_item.get('生成文件', '')}"
                )
            for f_item in fail[:3]:
                lines.append(
                    f"    ✗ {f_item.get('发票文件', f_item.get('旧文件名', '?'))}"
                    f" — 校验失败: {f_item.get('校验详情', '')}"
                )

        if not any(report.values()):
            lines.append("  所有类别均无文件需要检查。")

        return "\n".join(lines)

    except Exception as e:
        return _format_error(e)


# =========================================================================
# Tool 19: LLM Agent 自主编排报销流程
# =========================================================================

class RunReimbursementInput(BaseModel):
    """LLM Agent 自主编排报销流程参数。"""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    record_ids: str = Field(
        description=(
            "选中记录的 db_id 列表，JSON 数组字符串，如 '[1, 3, 5]'。"
        ),
    )
    target_url: str = Field(
        description="目标报销系统网址，如 'https://xxx.edu.cn/reimburse'",
    )
    max_steps: int = Field(
        default=10,
        description="最大工具调用轮数（防止无限循环），默认 10",
    )


@mcp.tool(
    name="invoice_run_reimbursement",
    annotations={
        "title": "LLM 自主编排报销流程",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def invoice_run_reimbursement(params: RunReimbursementInput) -> str:
    """LLM 自主编排的完整报销流程（两阶段）。

    第一阶段：LLM Agent 自主编排，生成浏览器操作指令
    第二阶段：将指令交给 BrowserAgent 执行浏览器自动化

    两个阶段是先后关系，互不干扰。

    Args:
        params (RunReimbursementInput): 编排参数

    Returns:
        str: JSON 格式的执行结果
    """
    try:
        ids = json.loads(params.record_ids)
        if not isinstance(ids, list):
            return json.dumps(
                {"success": False, "message": "record_ids 必须是数组"},
                ensure_ascii=False,
            )

        settings = _get_settings()

        # ── 第一阶段：Agent 编排，生成指令 ──────────────────
        agent_fn = _load_skill_agent_fn()

        agent_result = await agent_fn(
            record_ids=ids,
            target_url=params.target_url,
            settings=settings,
            max_steps=params.max_steps,
        )

        rendered_instruction = agent_result.get("rendered_instruction", "")

        if not agent_result.get("success") or not rendered_instruction:
            return json.dumps(agent_result, ensure_ascii=False, default=str)

        # ── 第二阶段：BrowserAgent 执行浏览器操作 ──────────
        from invoice_toolkit.browser_agent import run_browser_task
        from invoice_toolkit.database import get_record_db

        record_db = None
        if ids:
            try:
                record_db = get_record_db(settings)
            except Exception as e:
                logger.warning("获取 record_db 失败: %s", e)

        browser_result = await run_browser_task(
            task=rendered_instruction,
            url=params.target_url,
            settings=settings,
            record_ids=ids or None,
            record_db=record_db,
        )

        # 合并两阶段结果
        combined = {
            "success": browser_result.to_dict().get("success", False),
            "agent_steps": agent_result.get("steps", []),
            "agent_summary": agent_result.get("summary", ""),
            "rendered_instruction": rendered_instruction,
            "browser_result": browser_result.to_dict(),
        }

        return json.dumps(combined, ensure_ascii=False, default=str)

    except ImportError as e:
        return json.dumps(
            {"success": False, "message": f"Agent 模块加载失败: {e}"},
            ensure_ascii=False,
        )
    except Exception as e:
        return json.dumps(
            {"success": False, "message": _format_error(e)},
            ensure_ascii=False,
        )

# =========================================================================
# 入口
# =========================================================================

def main():
    # Windows 终端 UTF-8 支持：避免中文日志输出乱码
    import sys
    import os
    if sys.platform == "win32":
        os.environ.setdefault("PYTHONIOENCODING", "utf-8")
        # 等价于在终端执行 chcp 65001
        try:
            import ctypes
            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        except Exception:
            pass
        # 强制 stdout/stderr 使用 UTF-8
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        description="invoice-toolkit MCP Server",
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "http"],
        default="stdio",
        help="传输模式: stdio (本地) 或 http (远程)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="HTTP 模式端口号 (默认 8000)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # 启动时立即初始化 Settings 并创建数据库，不等待前端首次调用。
    # 这样可以确保：
    #   1. 数据库文件在服务就绪时就已存在，前端 GET /api/records 不会因
    #      数据库尚未创建而报错；
    #   2. 配置错误（环境变量缺失、路径不可写等）在启动阶段就能暴露，
    #      而不是在第一次工具调用时才抛出。
    try:
        _startup_settings = _get_settings()
        from invoice_toolkit.database import get_invoice_db, get_record_db
        get_invoice_db(_startup_settings)
        get_record_db(_startup_settings)
        logger.info("数据库初始化完成: invoices.db, records.db")
    except Exception as _exc:
        logger.warning("启动时数据库初始化失败（将在首次调用时重试）: %s", _exc)

    if args.transport == "http":
        import uvicorn
        from starlette.responses import JSONResponse
        from starlette.routing import Route

        app = mcp.streamable_http_app()

        # 在 MCP app 路由表头部插入 /health 端点（供 Docker healthcheck 使用）
        async def health(request):
            return JSONResponse({"status": "ok"})

        app.router.routes.insert(0, Route("/health", health, methods=["GET"]))

        # ── REST API 端点（供前端 TableEditor 直接调用）──────────
        # 这些端点包装了数据库操作，让前端可以通过标准 HTTP 调用
        # 而不必走 MCP 协议来执行简单的 CRUD。

        async def api_get_records(request):
            """GET /api/records — 获取所有记录（含关联发票信息）"""
            try:
                settings = _get_settings()
                from invoice_toolkit.database import get_record_db, get_invoice_db

                record_db = get_record_db(settings)
                invoice_db = get_invoice_db(settings)
                rows = record_db.get_records_joined(invoice_db)

                # 如果为空，尝试从 Excel 加载
                if not rows:
                    import pandas as pd
                    from invoice_toolkit.database import dataframe_to_records

                    data_dir = Path(settings.paths.project_root) / "data"
                    excel_path = data_dir / "明细.xlsx"
                    if excel_path.exists():
                        df = pd.read_excel(str(excel_path))
                        col_map = {col: str(col).strip() for col in df.columns}
                        df = df.rename(columns=col_map)
                        if "序号" not in df.columns:
                            df["序号"] = range(1, len(df) + 1)
                        record_db.upsert_records(dataframe_to_records(df))
                        rows = record_db.get_records_joined(invoice_db)

                return JSONResponse({"records": rows, "source": "db" if rows else "empty"})
            except Exception as e:
                return JSONResponse({"records": [], "error": str(e)}, status_code=500)

        async def api_update_record(request):
            """PUT /api/records/{id} — 更新单条记录

            当更新字段包含 category（类别）时，会同时更新 invoices.db 中
            该记录所匹配的发票的 category 字段，保持两库一致。
            """
            try:
                record_id = int(request.path_params["id"])
                body = await request.json()

                settings = _get_settings()
                from invoice_toolkit.database import get_record_db, get_invoice_db

                record_db = get_record_db(settings)
                # 移除前端内部字段
                fields = {k: v for k, v in body.items()
                          if k not in ("id", "db_id")}

                # 前端字段名映射：前端使用「类别」，数据库列名为 category
                category_value = None
                if "类别" in fields:
                    category_value = fields.pop("类别")
                    fields["category"] = category_value
                elif "category" in fields:
                    category_value = fields["category"]

                success = record_db.update_record_by_id(record_id, fields)

                # ── 同步类别到 invoices.db ──────────────────────
                # 当类别变化时，根据该记录的「匹配发票」字段找到关联的发票，
                # 同步更新 invoices 表的 category
                invoice_sync_count = 0
                if success and category_value is not None:
                    try:
                        # 先查出该记录的匹配发票字段
                        rec = record_db.get_record_by_id(record_id)
                        matched_inv = (rec or {}).get("匹配发票", "")
                        if matched_inv:
                            inv_names = [
                                n.strip()
                                for n in matched_inv.split(",")
                                if n.strip()
                            ]
                            if inv_names:
                                invoice_db = get_invoice_db(settings)
                                invoice_sync_count = (
                                    invoice_db.update_category_by_filenames(
                                        inv_names, category_value
                                    )
                                )
                    except Exception as sync_err:
                        logger.warning(
                            "同步发票类别失败 (record_id=%d): %s",
                            record_id, sync_err,
                        )

                if success:
                    msg = "已更新"
                    if invoice_sync_count > 0:
                        msg += f"，同步 {invoice_sync_count} 条发票类别"
                    return JSONResponse({"success": True, "message": msg})
                else:
                    return JSONResponse(
                        {"success": False, "message": "更新失败"},
                        status_code=400,
                    )
            except Exception as e:
                return JSONResponse(
                    {"success": False, "message": str(e)},
                    status_code=500,
                )

        async def api_delete_record(request):
            """DELETE /api/records/{id} — 删除单条记录"""
            try:
                record_id = int(request.path_params["id"])

                settings = _get_settings()
                from invoice_toolkit.database import get_record_db

                record_db = get_record_db(settings)
                success = record_db.delete_record_by_id(record_id)
                if success:
                    return JSONResponse({"success": True, "message": "已删除"})
                else:
                    return JSONResponse(
                        {"success": False, "message": "删除失败"},
                        status_code=400,
                    )
            except Exception as e:
                return JSONResponse(
                    {"success": False, "message": str(e)},
                    status_code=500,
                )

        # 注册 REST 路由
        app.router.routes.insert(0, Route("/api/records", api_get_records, methods=["GET"]))
        app.router.routes.insert(0, Route("/api/records/{id:int}", api_update_record, methods=["PUT"]))
        app.router.routes.insert(0, Route("/api/records/{id:int}", api_delete_record, methods=["DELETE"]))

        async def api_create_record(request):
            """POST /api/records — 创建一条新记录，返回 db_id"""
            try:
                body = await request.json()
                settings = _get_settings()
                from invoice_toolkit.database import get_record_db

                record_db = get_record_db(settings)

                # 前端字段名映射
                data = {k: v for k, v in body.items()
                        if k not in ("id", "db_id")}
                if "类别" in data and "category" not in data:
                    data["category"] = data.pop("类别")

                new_id = record_db.create_record(data)
                if new_id:
                    return JSONResponse({
                        "success": True,
                        "message": "已创建",
                        "db_id": new_id,
                    })
                else:
                    return JSONResponse(
                        {"success": False, "message": "创建失败"},
                        status_code=400,
                    )
            except Exception as e:
                return JSONResponse(
                    {"success": False, "message": str(e)},
                    status_code=500,
                )

        async def api_batch_save_records(request):
            """POST /api/records/batch — 批量保存记录到数据库

            已有 db_id 的行执行 UPDATE，无 db_id 的行执行 INSERT。
            同时将所有类别变更同步到 invoices.db。
            """
            try:
                body = await request.json()
                rows = body.get("rows", [])
                if not rows:
                    return JSONResponse(
                        {"success": False, "message": "无数据"},
                        status_code=400,
                    )

                settings = _get_settings()
                from invoice_toolkit.database import get_record_db, get_invoice_db

                record_db = get_record_db(settings)
                invoice_db = get_invoice_db(settings)

                stats = record_db.batch_upsert(rows)

                # ── 批量同步类别到 invoices.db ──────────────────
                inv_sync = 0
                for row in rows:
                    category = row.get("类别", "") or row.get("category", "")
                    matched_inv = row.get("匹配发票", "")
                    if category and matched_inv:
                        inv_names = [
                            n.strip() for n in matched_inv.split(",")
                            if n.strip()
                        ]
                        if inv_names:
                            inv_sync += invoice_db.update_category_by_filenames(
                                inv_names, category
                            )

                msg = (
                    f"已保存: 插入 {stats['inserted']} 条, "
                    f"更新 {stats['updated']} 条"
                )
                if stats["errors"]:
                    msg += f", 失败 {stats['errors']} 条"
                if inv_sync:
                    msg += f", 同步 {inv_sync} 条发票类别"

                return JSONResponse({
                    "success": stats["errors"] == 0,
                    "message": msg,
                    "stats": stats,
                    "invoice_synced": inv_sync,
                })
            except Exception as e:
                logger.error("batch save 失败: %s", e, exc_info=True)
                return JSONResponse(
                    {"success": False, "message": str(e)},
                    status_code=500,
                )

        app.router.routes.insert(0, Route("/api/records", api_create_record, methods=["POST"]))
        app.router.routes.insert(0, Route("/api/records/batch", api_batch_save_records, methods=["POST"]))

        async def api_browser_task(request):
            """POST /api/browser-task — 执行浏览器自动化任务"""
            try:
                body = await request.json()
                task = body.get("task", "")
                url = body.get("url", "")

                if not task:
                    return JSONResponse(
                        {"success": False, "message": "缺少 task 参数"},
                        status_code=400,
                    )
                if not url:
                    return JSONResponse(
                        {"success": False, "message": "缺少 url 参数"},
                        status_code=400,
                    )

                settings = _get_settings()

                # ── 调试：打印请求体中的覆盖参数，定位 180s 超时来源 ──
                override_keys = ("max_steps", "timeout", "max_llm_calls")
                body_overrides = {k: body.get(k) for k in override_keys if body.get(k) is not None}
                logger.info(
                    "[browser-task] 请求体覆盖参数: %s | "
                    "Settings.browser.timeout=%s (BROWSER_TIMEOUT env=%s)",
                    body_overrides if body_overrides else "(无)",
                    settings.browser.timeout,
                    os.environ.get("BROWSER_TIMEOUT", "(未设置)"),
                )

                from invoice_toolkit.browser_agent import run_browser_task

                # 【修改】从前端传入的 record_ids 数组初始化 record_db
                # 前端发送 record_ids: [12, 15, 23]，后端直接初始化数据库连接
                record_ids = body.get("record_ids") or []  # int 列表
                record_id = body.get("record_id")           # 单个 id（兼容旧调用）
                record_seq = body.get("record_seq")
                record_db = None

                # 只要有任何 record 标识，就初始化 record_db
                if record_ids or record_id is not None or record_seq is not None:
                    try:
                        from invoice_toolkit.database import get_record_db
                        record_db = get_record_db(settings)
                    except Exception as e:
                        logger.warning("获取 record_db 失败: %s", e)

                result = await run_browser_task(
                    task=task,
                    url=url,
                    settings=settings,
                    record_ids=record_ids or None,
                    record_id=record_id,
                    record_seq=record_seq,
                    record_db=record_db,
                    # 允许前端通过 body 覆盖部分参数
                    **{k: v for k, v in {
                        "max_steps": body.get("max_steps"),
                        "timeout": body.get("timeout"),
                        "max_llm_calls": body.get("max_llm_calls"),
                    }.items() if v is not None},
                )

                return JSONResponse(result.to_dict())

            except Exception as e:
                logger.error("browser-task 执行失败: %s", e, exc_info=True)
                return JSONResponse(
                    {"success": False, "message": str(e), "steps": []},
                    status_code=500,
                )

        # 注册路由
        app.router.routes.insert(
            0, Route("/api/browser-task", api_browser_task, methods=["POST"])
        )


        async def api_agent_reimbursement(request):
            """POST /api/agent-reimbursement — 两阶段报销流程 + 记忆系统。

            第零阶段：查询记忆 → 决定执行模式
            第一阶段：LLM Agent 编排，生成浏览器操作指令
            第二阶段：BrowserAgent 执行浏览器自动化
            第三阶段：写入记忆（不阻塞响应）

            请求体: {
                "record_ids": [1, 3, 5],
                "target_url": "https://xxx.edu.cn/reimburse",
                "max_steps": 10
            }
            """
            try:
                body = await request.json()
                record_ids = body.get("record_ids", [])
                target_url = body.get("target_url", "")
                max_steps = body.get("max_steps", 10)

                if not record_ids:
                    return JSONResponse(
                        {"success": False, "message": "缺少 record_ids"},
                        status_code=400,
                    )
                if not target_url:
                    return JSONResponse(
                        {"success": False, "message": "缺少 target_url"},
                        status_code=400,
                    )

                settings = _get_settings()

                # ── 第零阶段：查询记忆 ─────────────────────────
                mem_mode = "explore"
                mem_context = ""
                mem_flow = None
                category = "日常报销"

                try:
                    from invoice_toolkit.memory_integration import (
                        memory_read, memory_write, detect_category,
                    )

                    category = detect_category(record_ids, settings)
                    mem = memory_read(category)
                    mem_mode = mem["mode"]
                    mem_context = mem.get("context", "")
                    mem_flow = mem.get("flow")

                    logger.info(
                        "[Memory] 类别=%s, 模式=%s, 置信度=%.2f",
                        category, mem_mode, mem.get("confidence", 0),
                    )
                except ImportError:
                    logger.info("[Memory] 记忆模块未安装，使用默认流程")
                except Exception as e:
                    logger.warning("[Memory] 记忆查询失败（不影响主流程）: %s", e)

                # ── 第一阶段：Agent 编排，生成指令 ──────────────
                agent_fn = _load_skill_agent_fn()

                agent_result = await agent_fn(
                    record_ids=record_ids,
                    target_url=target_url,
                    settings=settings,
                    max_steps=max_steps,
                    memory_context=mem_context,  # ← 注入记忆上下文
                )

                rendered_instruction = agent_result.get("rendered_instruction", "")

                # 第一阶段失败或没有生成指令，直接返回
                if not agent_result.get("success") or not rendered_instruction:
                    return JSONResponse({
                        "success": False,
                        "memory_mode": mem_mode,
                        "agent_steps": agent_result.get("steps", []),
                        "agent_summary": agent_result.get("summary", "指令生成失败"),
                        "rendered_instruction": rendered_instruction,
                        "browser_result": None,
                    })

                logger.info(
                    "第一阶段完成，指令长度: %d 字符，开始第二阶段浏览器执行",
                    len(rendered_instruction),
                )

                # ── 第二阶段：BrowserAgent 执行 ────────────────
                from invoice_toolkit.browser_agent import run_browser_task
                from invoice_toolkit.database import get_record_db

                record_db = None
                if record_ids:
                    try:
                        record_db = get_record_db(settings)
                    except Exception as e:
                        logger.warning("获取 record_db 失败: %s", e)

                browser_result = await run_browser_task(
                    task=rendered_instruction,
                    url=target_url,
                    settings=settings,
                    record_ids=record_ids or None,
                    record_db=record_db,
                )

                # ── 第三阶段：写入记忆 ─────────────────────────
                try:
                    memory_write(
                        category=category,
                        record_ids=record_ids,
                        mode=mem_mode,
                        browser_result=browser_result,
                        flow_used=(
                            mem_flow.get("flow_id")
                            if mem_flow and mem_flow.get("found")
                            else None
                        ),
                    )
                except NameError:
                    pass  # memory_integration 未导入
                except Exception as e:
                    logger.warning("[Memory] 记忆写入失败（不影响主流程）: %s", e)

                return JSONResponse({
                    "success": browser_result.to_dict().get("success", False),
                    "memory_mode": mem_mode,
                    "agent_steps": agent_result.get("steps", []),
                    "agent_summary": agent_result.get("summary", ""),
                    "rendered_instruction": rendered_instruction,
                    "browser_result": browser_result.to_dict(),
                })

            except ImportError as e:
                logger.error("Agent 模块加载失败: %s", e)
                return JSONResponse(
                    {"success": False, "message": f"Agent 模块未找到: {e}",
                     "agent_steps": [], "browser_result": None},
                    status_code=500,
                )
            except Exception as e:
                logger.error("agent-reimbursement 执行失败: %s", e, exc_info=True)
                return JSONResponse(
                    {"success": False, "message": str(e),
                     "agent_steps": [], "browser_result": None},
                    status_code=500,
                )

        app.router.routes.insert(
            0, Route("/api/agent-reimbursement", api_agent_reimbursement, methods=["POST"])
        )


        # ── POST /api/render-prompt — LLM 智能渲染提示词模板 ──
        import re as _re

        def _parse_sections(template: str) -> list:
            """解析模板为 once / repeat 区段列表（与前端 parseSections 逻辑一致）。"""
            if not template or not template.strip():
                return []
            sections = []
            remaining = template
            while remaining:
                marker_match = _re.search(r'^(===.+?===)\s*$', remaining, _re.MULTILINE)
                if not marker_match:
                    if remaining.strip():
                        sections.append({"type": "once", "content": remaining.strip()})
                    break
                marker_idx = remaining.index(marker_match.group(0))
                before = remaining[:marker_idx].strip()
                if before:
                    sections.append({"type": "once", "content": before})
                remaining = remaining[marker_idx + len(marker_match.group(0)):]
                divider_match = _re.search(r'^======\s*$', remaining, _re.MULTILINE)
                if not divider_match:
                    if remaining.strip():
                        sections.append({"type": "repeat", "content": remaining.strip()})
                    break
                divider_idx = remaining.index(divider_match.group(0))
                repeat_content = remaining[:divider_idx].strip()
                if repeat_content:
                    sections.append({"type": "repeat", "content": repeat_content})
                remaining = remaining[divider_idx + len(divider_match.group(0)):]
            return sections

        # 聚合类计算字段：由 _calculate_transfer_amounts 汇总生成，
        # 可能合法地为空字符串（如没有附件要上传）。此时应渲染为空串，
        # 而非保留字面量 {{附件汇总}} 污染输出。
        _AGGREGATE_COMPUTED_FIELDS = {"转卡汇总", "附件汇总"}

        def _render_section_regex(content: str, record: dict) -> str:
            """正则方式渲染单个区段（LLM 失败时的回退）。"""
            def _replace(m):
                key = m.group(1).strip()
                val = record.get(key)
                if val is None or val == '':
                    if key in _AGGREGATE_COMPUTED_FIELDS:
                        return ''  # 聚合字段空值→空串
                    return m.group(0)
                return str(val)
            return _re.sub(r'\{\{(.+?)\}\}', _replace, content)

        async def api_render_prompt(request):
            """POST /api/render-prompt — 使用 LLM 智能渲染提示词模板。

            请求体: { "template": "...", "records": [...] }
            响应:   { "success": true, "rendered": "...", "amount_summary": {...} }

            v8 改动：渲染前自动按「姓名/公司」分组计算转卡金额，
            将 {{转卡金额}} / {{转卡明细}} 注入每条记录。
            """
            try:
                body = await request.json()
                template = body.get("template", "")
                records = body.get("records", [])

                if not template:
                    return JSONResponse(
                        {"success": False, "message": "缺少 template 参数"},
                        status_code=400,
                    )
                if not records:
                    return JSONResponse(
                        {"success": False, "message": "缺少 records 参数"},
                        status_code=400,
                    )

                # ── 自动计算转卡金额并注入记录 ──────────────────
                calc_result = _calculate_transfer_amounts(records)
                records = calc_result["enriched_records"]
                amount_summary = calc_result["amount_by_person"]
                logger.info(
                    "render-prompt: 已计算转卡金额 %s",
                    json.dumps(amount_summary, ensure_ascii=False),
                )

                sections = _parse_sections(template)
                first_record = records[0]
                parts = []

                # 尝试导入 LLMClient
                llm_client = None
                try:
                    from invoice_toolkit.llm_client import LLMClient
                    llm_client = LLMClient()
                except Exception as llm_init_err:
                    logger.warning("LLMClient 初始化失败，将回退正则: %s", llm_init_err)

                for section in sections:
                    if section["type"] == "once":
                        # 一次性区段：用第一条记录渲染
                        if llm_client:
                            try:
                                sys_prompt = (
                                    "你是一个模板渲染引擎。将模板中的 {{变量名}} 替换为对应的值。\n"
                                    "规则：1) 只输出渲染后的文本，不要添加任何解释；"
                                    "2) 如果某个变量没有对应值，根据上下文智能处理（跳过相关句子或标注缺失）；"
                                    "3) 如果模板中有条件逻辑（如「若...则...」），根据实际值只保留匹配的分支。"
                                )
                                user_prompt = (
                                    f"模板:\n{section['content']}\n\n"
                                    f"变量值:\n{json.dumps(first_record, ensure_ascii=False, indent=2)}"
                                )
                                rendered = llm_client.chat(sys_prompt, user_prompt)
                                parts.append(rendered.strip())
                                continue
                            except Exception as e:
                                logger.warning("LLM 渲染 once 区段失败，回退正则: %s", e)
                        parts.append(_render_section_regex(section["content"], first_record))

                    else:
                        # 重复区段：为每条记录展开
                        parts.append(
                            f"\n以下对 {len(records)} 张发票依次执行，严格按顺序逐一处理，每张只操作一次：\n"
                        )
                        expanded = []
                        for i, rec in enumerate(records):
                            label = (
                                f"【第 {i + 1}/{len(records)} 张: "
                                f"{rec.get('姓名/公司', '')} "
                                f"¥{rec.get('金额', '')}"
                                f"{' 票号' + rec['发票号码'] if rec.get('发票号码') else ''}】"
                            )
                            if llm_client:
                                try:
                                    sys_prompt = (
                                        "你是一个模板渲染引擎。将模板中的 {{变量名}} 替换为对应的值。\n"
                                        "规则：1) 只输出渲染后的文本，不要添加任何解释；"
                                        "2) 如果某个变量没有对应值，根据上下文智能处理；"
                                        "3) 处理条件逻辑时只保留匹配的分支。"
                                    )
                                    user_prompt = (
                                        f"模板:\n{section['content']}\n\n"
                                        f"变量值:\n{json.dumps(rec, ensure_ascii=False, indent=2)}"
                                    )
                                    rendered = llm_client.chat(sys_prompt, user_prompt)
                                    expanded.append(f"{label}\n{rendered.strip()}")
                                    continue
                                except Exception as e:
                                    logger.warning("LLM 渲染 repeat 区段 #%d 失败，回退正则: %s", i, e)
                            expanded.append(f"{label}\n{_render_section_regex(section['content'], rec)}")

                        parts.append("\n\n".join(expanded))

                rendered_text = "\n\n".join(parts)
                return JSONResponse({
                    "success": True,
                    "rendered": rendered_text,
                    "amount_summary": amount_summary,
                })

            except Exception as e:
                logger.error("render-prompt 失败: %s", e, exc_info=True)
                return JSONResponse(
                    {"success": False, "message": str(e)},
                    status_code=500,
                )

        app.router.routes.insert(
            0, Route("/api/render-prompt", api_render_prompt, methods=["POST"])
        )

        # ── GET /api/prompt-template — 获取 Skill 模板 ──────────
        async def api_get_prompt_template_http(request):
            """GET /api/prompt-template?name=default — 读取 Skill 提示词模板。

            供前端 ReimbursementAgent 加载默认模板或用户自定义模板。
            """
            try:
                name = request.query_params.get("name", "default")
                dirs = _find_skill_template_dirs()

                for d in dirs:
                    md_path = d / f"{name}.md"
                    if md_path.exists():
                        content = md_path.read_text(encoding="utf-8")
                        return JSONResponse({
                            "success": True,
                            "template_name": name,
                            "template": content,
                            "source": str(md_path),
                        })

                # 列出可用模板
                available = []
                for d in dirs:
                    available.extend(f.stem for f in d.glob("*.md"))

                return JSONResponse({
                    "success": False,
                    "message": f"模板 '{name}' 不存在",
                    "available_templates": available,
                }, status_code=404)
            except Exception as e:
                return JSONResponse(
                    {"success": False, "message": str(e)},
                    status_code=500,
                )

        app.router.routes.insert(
            0, Route("/api/prompt-template", api_get_prompt_template_http, methods=["GET"])
        )

        # ── POST /api/calculate-amounts — 计算转卡金额 ──────────
        async def api_calculate_amounts_http(request):
            """POST /api/calculate-amounts — 按户名分组计算转卡金额。

            请求体: { "records": [...] }
            响应:   { "success": true, "amount_by_person": {...}, "enriched_records": [...] }
            """
            try:
                body = await request.json()
                records = body.get("records", [])
                if not records:
                    return JSONResponse(
                        {"success": False, "message": "缺少 records 参数"},
                        status_code=400,
                    )
                result = _calculate_transfer_amounts(records)
                return JSONResponse({
                    "success": True,
                    "record_count": len(records),
                    "amount_by_person": result["amount_by_person"],
                    "detail_by_person": result["detail_by_person"],
                    "enriched_records": result["enriched_records"],
                })
            except Exception as e:
                return JSONResponse(
                    {"success": False, "message": str(e)},
                    status_code=500,
                )

        app.router.routes.insert(
            0, Route("/api/calculate-amounts", api_calculate_amounts_http, methods=["POST"])
        )

        # 添加 CORS 中间件，允许前端跨域访问
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_methods=["*"],
            allow_headers=["*"],
            expose_headers=["mcp-session-id"],
        )

        # ── 反向代理 Host 头修复 ──────────────────────────────
        # MCP SDK 的 transport_security.py 会校验 Host 头，
        # 经过 nginx 反向代理后 Host 变为 "localhost"，被 SDK 拒绝（421）。
        # 此 ASGI 中间件在请求进入 MCP 前将 Host 重写为 SDK 能接受的值。
        class HostRewriteMiddleware:
            """将 Host 和 Origin 头重写为 localhost:<port>，满足 MCP 安全校验。

            经过 Nginx 反向代理后，Host 变为 "localhost"，Origin 为 "http://localhost"，
            与 MCP SDK 期望的 localhost:<port> 不匹配，会导致 421 / 403。
            此中间件统一将两者重写为 SDK 能接受的值。
            """
            def __init__(self, asgi_app, port):
                self.app = asgi_app
                self.safe_host = f"localhost:{port}".encode()
                self.safe_origin = f"http://localhost:{port}".encode()

            async def __call__(self, scope, receive, send):
                if scope["type"] == "http":
                    new_headers = []
                    for k, v in scope.get("headers", []):
                        if k == b"host":
                            v = self.safe_host
                        elif k == b"origin":
                            v = self.safe_origin
                        new_headers.append((k, v))
                    scope = dict(scope, headers=new_headers)
                await self.app(scope, receive, send)

        wrapped_app = HostRewriteMiddleware(app, args.port)
        uvicorn.run(wrapped_app, host="0.0.0.0", port=args.port)
    else:
        mcp.run()


if __name__ == "__main__":
    main()