"""
数据库模块

使用 SQLite 替代 Excel 文件作为持久化存储。
提供两个数据库：

1. **发票数据库** (invoices.db)
   - 汇总 OCR 识别结果、发票匹配结果、分类结果、附件检查结果、文件名检查结果

2. **记录数据库** (records.db)
   - 存储输入的报销明细和记录匹配结果

设计原则：
    - 以 ``旧文件名`` 为发票的唯一标识（UNIQUE KEY）
    - 以 ``id``（auto-increment）为记录的唯一标识；``序号`` 仅为显示字段，允许重复
    - 各模块通过 ``upsert_*`` 方法写入自己负责的字段，互不干扰
    - 提供 ``to_dataframe()`` 方法兼容原有 DataFrame 驱动的业务逻辑
    - 所有写操作自动提交，读操作返回 pandas DataFrame
"""

from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Union

import pandas as pd

logger = logging.getLogger(__name__)


# =========================================================================
# 发票数据库表结构
# =========================================================================

_INVOICE_TABLE_SQL = """\
CREATE TABLE IF NOT EXISTS invoices (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,

    -- OCR 识别字段
    相对路径        TEXT    DEFAULT '',
    "姓名/公司"     TEXT    DEFAULT '',
    旧文件名        TEXT    NOT NULL UNIQUE,
    新文件名        TEXT    DEFAULT '',
    购方名称        TEXT    DEFAULT '',
    购方税号        TEXT    DEFAULT '',
    价税合计        TEXT    DEFAULT '',
    商品单价        TEXT    DEFAULT '',
    商品名称        TEXT    DEFAULT '',
    销售方名称      TEXT    DEFAULT '',
    发票类型        TEXT    DEFAULT '',
    发票号码        TEXT    DEFAULT '',
    发票代码        TEXT    DEFAULT '',
    开票日期        TEXT    DEFAULT '',
    税额            TEXT    DEFAULT '',
    校验码          TEXT    DEFAULT '',

    -- 匹配字段
    匹配序号        TEXT    DEFAULT '',
    匹配姓名        TEXT    DEFAULT '',
    匹配金额        TEXT    DEFAULT '',
    匹配简介        TEXT    DEFAULT '',
    是否匹配        TEXT    DEFAULT '',
    匹配方式        TEXT    DEFAULT '',
    组合金额        TEXT    DEFAULT '',

    -- 分类字段
    full_path       TEXT    DEFAULT '',
    parent          TEXT    DEFAULT '',
    category        TEXT    DEFAULT '',

    -- 附件检查字段已迁移到 records 表（见 _RECORD_TABLE_SQL）
    -- 历史遗留列由 _init_db 迁移时自动 DROP COLUMN

    -- 文件名检查字段
    文件路径        TEXT    DEFAULT '',
    建议文件名      TEXT    DEFAULT '',
    文件名状态      TEXT    DEFAULT '',
    修正原因        TEXT    DEFAULT '',
    已重命名        INTEGER DEFAULT 0,

    -- 异常标记字段
    异常标记        TEXT    DEFAULT '',
    标记原因        TEXT    DEFAULT '',

    -- 元数据
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

_INVOICE_TRIGGER_SQL = """\
CREATE TRIGGER IF NOT EXISTS update_invoice_timestamp
AFTER UPDATE ON invoices
FOR EACH ROW
BEGIN
    UPDATE invoices SET updated_at = CURRENT_TIMESTAMP WHERE id = OLD.id;
END;
"""


# =========================================================================
# 记录数据库表结构
# =========================================================================

_RECORD_TABLE_SQL = """\
CREATE TABLE IF NOT EXISTS records (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,

    -- 原始明细字段
    -- 注意: 序号不加 UNIQUE，Excel 明细中允许重复序号，以 id 为真正唯一标识
    序号            INTEGER,
    "姓名/公司"     TEXT    DEFAULT '',
    填写日期        TEXT    DEFAULT '',
    金额            REAL    DEFAULT 0,
    物品简介        TEXT    DEFAULT '',
    备注            TEXT    DEFAULT '',
    extra_fields    TEXT    DEFAULT '{}',

    -- 匹配字段
    匹配发票        TEXT    DEFAULT '',
    匹配发票金额    TEXT    DEFAULT '',
    是否匹配        TEXT    DEFAULT '',
    匹配方式        TEXT    DEFAULT '',
    组合金额        TEXT    DEFAULT '',
    备注分解金额    TEXT    DEFAULT '',
    未匹配金额      TEXT    DEFAULT '',

    -- 附件检查字段（原先在 invoices 表，现迁移至此）
    -- 由 checker.save_report 通过 匹配发票 反向查找后写入
    -- 多发票聚合规则：状态取最严重、详情按发票拼接、其余逗号去重合并
    附件状态        TEXT    DEFAULT '',
    缺少类型        TEXT    DEFAULT '',
    匹配附件        TEXT    DEFAULT '',
    附件路径        TEXT    DEFAULT '',
    生成文件        TEXT    DEFAULT '',
    校验详情        TEXT    DEFAULT '',
    附件类别        TEXT    DEFAULT '',

    -- 浏览器自动化报错记录
    浏览器报错        TEXT    DEFAULT '',

    -- 元数据
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

_RECORD_TRIGGER_SQL = """\
CREATE TRIGGER IF NOT EXISTS update_record_timestamp
AFTER UPDATE ON records
FOR EACH ROW
BEGIN
    UPDATE records SET updated_at = CURRENT_TIMESTAMP WHERE id = OLD.id;
END;
"""

# 记录表中基础 schema 定义的列（动态列会在首次加载时自动添加）
_RECORD_KNOWN_COLUMNS = {
    "id", "序号", "姓名/公司", "填写日期", "金额", "物品简介", "备注",
    "extra_fields", "匹配发票", "匹配发票金额", "是否匹配",
    "匹配方式", "组合金额", "备注分解金额", "未匹配金额", "updated_at",
    "category",
    # 附件检查字段（从 invoices 表迁移而来）
    "附件状态", "缺少类型", "匹配附件", "附件路径",
    "生成文件", "校验详情", "附件类别",
    # 浏览器自动化报错
    "浏览器报错",
}

# 附件检查字段列表（供迁移 / upsert 使用）
_ATTACHMENT_FIELDS = [
    "附件状态", "缺少类型", "匹配附件", "附件路径",
    "生成文件", "校验详情", "附件类别",
]


# =========================================================================
# 基础数据库类
# =========================================================================

class _BaseDatabase:
    """SQLite 数据库基类"""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        """子类实现：创建表和触发器"""
        raise NotImplementedError

    @contextmanager
    def _connect(self):
        """获取数据库连接的上下文管理器"""
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _execute(self, sql: str, params: tuple = ()) -> None:
        with self._connect() as conn:
            conn.execute(sql, params)

    def _executemany(self, sql: str, params_list: list[tuple]) -> None:
        with self._connect() as conn:
            conn.executemany(sql, params_list)

    def _fetchall(self, sql: str, params: tuple = ()) -> list[dict]:
        with self._connect() as conn:
            cursor = conn.execute(sql, params)
            columns = [desc[0] for desc in cursor.description]
            return [dict(zip(columns, row)) for row in cursor.fetchall()]

    def _fetchone(self, sql: str, params: tuple = ()) -> Optional[dict]:
        with self._connect() as conn:
            cursor = conn.execute(sql, params)
            row = cursor.fetchone()
            if row is None:
                return None
            columns = [desc[0] for desc in cursor.description]
            return dict(zip(columns, row))

    def _get_table_columns(self, table: str) -> list[str]:
        """获取表的所有列名"""
        rows = self._fetchall(f"PRAGMA table_info({table})")
        return [r["name"] for r in rows]

    @property
    def db_path(self) -> Path:
        return self._db_path

    def count(self, table: str) -> int:
        result = self._fetchone(f"SELECT COUNT(*) as cnt FROM {table}")
        return result["cnt"] if result else 0


# =========================================================================
# 发票数据库
# =========================================================================

class InvoiceDatabase(_BaseDatabase):
    """
    发票数据库

    以 ``旧文件名`` 为唯一标识，汇总以下模块的输出：
        - OCR 识别（ocr.py）
        - 发票匹配（matcher.py）
        - 发票分类（classifier.py）
        - 附件检查（checker.py）
        - 文件名检查（checker.py）

    Usage::

        db = InvoiceDatabase("output/invoices.db")
        db.upsert_ocr_results([{"旧文件名": "a.pdf", "价税合计": "100"}])
        db.upsert_match_results([{"旧文件名": "a.pdf", "是否匹配": "已匹配"}])
        df = db.to_dataframe()
    """

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(_INVOICE_TABLE_SQL)
            conn.execute(_INVOICE_TRIGGER_SQL)
            # 迁移：为旧表补加缺失列
            existing_cols = {
                row[1]
                for row in conn.execute("PRAGMA table_info(invoices)").fetchall()
            }
            for col in ("异常标记", "标记原因",
                        "发票类型", "发票号码", "发票代码",
                        "开票日期", "税额", "校验码"):
                if col not in existing_cols:
                    conn.execute(
                        f"ALTER TABLE invoices ADD COLUMN {col} TEXT DEFAULT ''"
                    )
                    logger.info("invoices 表迁移: 已添加 %s 列", col)

            # 迁移：从 invoices 表移除附件检查字段（已迁移到 records 表）
            # SQLite 3.35+ 支持 DROP COLUMN；失败时静默忽略（不影响功能，
            # 因为新代码不再写入这些列，只是会在旧库中保留为空）
            legacy_attachment_cols = [
                "附件状态", "缺少类型", "匹配附件", "附件路径",
                "生成文件", "校验详情", "附件类别",
            ]
            for col in legacy_attachment_cols:
                if col in existing_cols:
                    try:
                        conn.execute(f'ALTER TABLE invoices DROP COLUMN "{col}"')
                        logger.info("invoices 表迁移: 已移除 %s 列（迁至 records）", col)
                    except sqlite3.Error as exc:
                        # SQLite < 3.35 不支持 DROP COLUMN — 留列在表中也无害，
                        # 新代码不会再写入，旧数据保留用于历史参考
                        logger.debug(
                            "invoices 表迁移: 无法 DROP COLUMN %s (%s)，"
                            "保留旧列但不再使用", col, exc,
                        )

    # ------------------------------------------------------------------
    # OCR 模块接口
    # ------------------------------------------------------------------

    def upsert_ocr_results(self, records: list[dict]) -> int:
        """
        写入 / 更新 OCR 识别结果。

        Args:
            records: OCR 结果列表，每项至少包含 ``旧文件名``

        Returns:
            受影响的行数
        """
        if not records:
            return 0

        ocr_fields = [
            "相对路径", "姓名/公司", "旧文件名", "新文件名",
            "购方名称", "购方税号", "价税合计", "商品单价",
            "商品名称", "销售方名称",
            "发票类型", "发票号码", "发票代码",
            "开票日期", "税额", "校验码",
        ]
        return self._upsert_records(records, ocr_fields, "旧文件名")

    def get_ocr_results(self) -> list[dict]:
        """读取所有 OCR 结果"""
        return self._fetchall(
            'SELECT * FROM invoices WHERE 价税合计 != "" OR 销售方名称 != ""'
        )

    def get_ocr_dataframe(self) -> pd.DataFrame:
        """以 DataFrame 格式返回 OCR 结果（兼容旧代码）"""
        rows = self.get_ocr_results()
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows)
        # 还原商品单价/名称的 list 格式
        for col in ("商品单价", "商品名称"):
            if col in df.columns:
                df[col] = df[col].apply(_json_loads_safe)
        return df

    # ------------------------------------------------------------------
    # 匹配模块接口
    # ------------------------------------------------------------------

    def upsert_match_results(self, records: list[dict]) -> int:
        """写入 / 更新发票匹配结果"""
        match_fields = [
            "旧文件名", "匹配序号", "匹配姓名", "匹配金额",
            "匹配简介", "是否匹配", "匹配方式", "组合金额",
        ]
        return self._upsert_records(records, match_fields, "旧文件名")

    def get_match_results(self) -> pd.DataFrame:
        """以 DataFrame 格式返回发票匹配结果"""
        rows = self._fetchall("SELECT * FROM invoices")
        return pd.DataFrame(rows) if rows else pd.DataFrame()

    # ------------------------------------------------------------------
    # 分类模块接口
    # ------------------------------------------------------------------

    def upsert_classification(self, records: list[dict]) -> int:
        """
        写入 / 更新分类结果。

        Args:
            records: 每项包含 ``旧文件名``（对应 ``name``）和 ``category``
        """
        cls_fields = ["旧文件名", "full_path", "parent", "category"]
        return self._upsert_records(records, cls_fields, "旧文件名")

    def get_classification(self) -> pd.DataFrame:
        """以 DataFrame 格式返回分类结果"""
        rows = self._fetchall(
            'SELECT 旧文件名 as name, full_path, parent, category '
            'FROM invoices WHERE category != ""'
        )
        return pd.DataFrame(rows) if rows else pd.DataFrame()

    def get_invoice_info_for_records(self) -> Dict[str, Dict]:
        """
        返回 {匹配序号(str): {parent, 商品名称, 销售方名称}} 映射。

        供 classifier.py 在分类前将发票辅助信息（路径、商品名称）关联到
        报销记录上。仅返回 ``匹配序号`` 非空的发票行。

        当多张发票对应同一序号时，``商品名称`` 以 "/" 合并，
        ``parent`` 以最后一条为准（通常相同报销人目录相同）。

        Returns:
            ``{序号字符串: {"parent": ..., "商品名称": ..., "销售方名称": ...}}``
        """
        rows = self._fetchall(
            'SELECT 匹配序号, parent, 商品名称, 销售方名称 '
            'FROM invoices WHERE 匹配序号 != ""'
        )
        result: Dict[str, Dict] = {}
        for row in rows:
            seq = str(row.get("匹配序号", "")).strip()
            if not seq:
                continue
            goods = row.get("商品名称", "") or ""
            seller = row.get("销售方名称", "") or ""
            parent = row.get("parent", "") or ""
            if seq in result:
                # 多张发票同一序号：合并商品名称
                existing_goods = result[seq].get("商品名称", "")
                merged = "/".join(g for g in [existing_goods, goods] if g)
                result[seq]["商品名称"] = merged
                if parent:
                    result[seq]["parent"] = parent
            else:
                result[seq] = {
                    "parent": parent,
                    "商品名称": goods,
                    "销售方名称": seller,
                }
        return result

    def sync_categories_from_records(self, record_db: "RecordDatabase") -> int:
        """
        将 records.category 同步写回 invoices.category（通过 匹配序号 关联）。

        分类主体是报销记录（records），发票表的 ``category`` 字段由此方法
        从记录表反向填入，以供后续文件移动步骤使用。

        Args:
            record_db: 记录数据库实例（已写入 category）

        Returns:
            成功更新的发票行数
        """
        # 获取所有有匹配序号的发票
        inv_rows = self._fetchall(
            'SELECT 旧文件名, 匹配序号 FROM invoices WHERE 匹配序号 != ""'
        )
        if not inv_rows:
            return 0

        # 从 record_db 构建 {序号字符串: category} 映射
        try:
            cat_rows = record_db._fetchall(
                "SELECT 序号, category FROM records WHERE category IS NOT NULL AND category != ''"
            )
        except Exception as exc:
            logger.warning("读取记录分类失败: %s", exc)
            return 0

        cat_map: Dict[str, str] = {
            str(r["序号"]): r["category"]
            for r in cat_rows
            if r.get("category")
        }
        if not cat_map:
            return 0

        count = 0
        with self._connect() as conn:
            for row in inv_rows:
                seq = str(row.get("匹配序号", "")).strip()
                filename = row.get("旧文件名", "")
                category = cat_map.get(seq, "")
                if category and filename:
                    try:
                        conn.execute(
                            "UPDATE invoices SET category = ? WHERE 旧文件名 = ?",
                            (category, filename),
                        )
                        count += 1
                    except sqlite3.Error as exc:
                        logger.warning("同步发票类别失败 (%s): %s", filename, exc)

        logger.info("发票数据库: 从记录同步 %d 条类别", count)
        return count

    def update_category_by_filenames(
        self, filenames: list[str], category: str
    ) -> int:
        """
        按发票文件名批量更新 category 字段。

        当前端修改某条报销记录的类别时，需要同步更新其匹配发票的类别。

        Args:
            filenames: 发票文件名列表（对应 invoices.旧文件名）
            category:  要设置的分类名称

        Returns:
            成功更新的行数
        """
        if not filenames or not category:
            return 0

        count = 0
        with self._connect() as conn:
            for fname in filenames:
                fname = fname.strip()
                if not fname:
                    continue
                try:
                    cursor = conn.execute(
                        "UPDATE invoices SET category = ? WHERE 旧文件名 = ?",
                        (category, fname),
                    )
                    if cursor.rowcount > 0:
                        count += cursor.rowcount
                except sqlite3.Error as exc:
                    logger.warning(
                        "更新发票类别失败 (%s → %s): %s", fname, category, exc
                    )

        if count:
            logger.info(
                "发票数据库: 按文件名更新 %d 条类别 → %s", count, category
            )
        return count

    def backfill_full_path(self, source_root) -> int:
        """
        对 ``full_path`` 为空的发票行，根据磁盘文件补全绝对路径和 ``parent`` 字段。

        优先按 ``相对路径 + 旧文件名`` 构造路径；若文件不存在，则在
        ``source_root`` 下递归搜索同名文件作为兜底。

        通常在 OCR 步骤完成后立即调用，确保后续分类、移动步骤能正确
        读到 ``full_path``。

        Args:
            source_root: 发票源文件根目录（``Settings.paths.source_root``）

        Returns:
            成功补全的行数
        """
        rows = self._fetchall(
            "SELECT 旧文件名, 相对路径 FROM invoices "
            "WHERE (full_path = '' OR full_path IS NULL) AND 旧文件名 != ''"
        )
        if not rows:
            logger.debug("full_path 均已填充，无需补全")
            return 0

        source_root = Path(source_root)

        # 懒加载文件名索引：仅在构造路径失败时才递归扫目录，避免重复 I/O
        _name_index: dict[str, Path] | None = None

        def _get_name_index() -> dict[str, Path]:
            nonlocal _name_index
            if _name_index is None:
                _name_index = {}
                if source_root.exists():
                    for p in source_root.rglob("*"):
                        if p.is_file():
                            _name_index[p.name] = p
            return _name_index

        count = 0
        with self._connect() as conn:
            for row in rows:
                filename = row.get("旧文件名", "")
                if not filename:
                    continue

                rel_path = (row.get("相对路径") or "").strip()

                # 优先用已记录的相对路径直接构造
                if rel_path:
                    candidate = source_root / rel_path / filename
                else:
                    candidate = source_root / filename

                if candidate.exists():
                    full_path = str(candidate)
                    parent = rel_path or "."
                else:
                    # 兜底：递归搜索整棵源目录树
                    idx = _get_name_index()
                    if filename not in idx:
                        logger.warning("补全 full_path：找不到文件 %s", filename)
                        continue
                    p = idx[filename]
                    full_path = str(p)
                    try:
                        parent = str(p.parent.relative_to(source_root))
                    except ValueError:
                        parent = str(p.parent)

                try:
                    conn.execute(
                        "UPDATE invoices SET full_path = ?, parent = ? WHERE 旧文件名 = ?",
                        (full_path, parent, filename),
                    )
                    count += 1
                except sqlite3.Error as exc:
                    logger.warning("补全 full_path 失败 (%s): %s", filename, exc)

        logger.info("发票数据库: 补全 %d 条 full_path", count)
        return count

    # ------------------------------------------------------------------
    # 附件检查模块接口（已迁移到 RecordDatabase）
    # ------------------------------------------------------------------
    # 附件检查结果现在写入 records 表。checker.save_report 应调用
    # RecordDatabase.upsert_attachment_check 而不是这里的旧方法。
    # 此处不再提供 upsert_attachment_check / get_attachment_check。

    # ------------------------------------------------------------------
    # 文件名检查模块接口
    # ------------------------------------------------------------------

    def upsert_filename_check(self, records: list[dict]) -> int:
        """写入 / 更新文件名检查结果"""
        fn_fields = [
            "旧文件名", "文件路径", "建议文件名",
            "文件名状态", "修正原因", "已重命名",
        ]
        return self._upsert_records(records, fn_fields, "旧文件名")

    def get_filename_check(self) -> list[dict]:
        """读取文件名检查结果"""
        return self._fetchall('SELECT * FROM invoices WHERE 文件名状态 != ""')

    # ------------------------------------------------------------------
    # 异常标记模块接口
    # ------------------------------------------------------------------

    def upsert_anomaly_mark(self, records: list[dict]) -> int:
        """
        写入 / 更新异常标记。

        Args:
            records: 每项包含 ``旧文件名``、``异常标记``（如 "是"）、``标记原因``

        Returns:
            受影响的行数
        """
        mark_fields = ["旧文件名", "异常标记", "标记原因"]
        return self._upsert_records(records, mark_fields, "旧文件名")

    def append_anomaly_reason(self, filename: str, reason: str) -> None:
        """
        追加异常原因到已有记录（避免覆盖先前模块写入的原因）。

        如果该发票尚无异常标记，直接写入；如果已有标记，将新原因用
        分号拼接到已有原因之后。

        Args:
            filename: 发票文件名（旧文件名）
            reason:   本次要追加的异常原因
        """
        if not filename or not reason:
            return
        existing = self.get_by_filename(filename)
        if existing:
            old_mark = (existing.get("异常标记") or "").strip()
            old_reason = (existing.get("标记原因") or "").strip()
            if old_reason:
                # 避免重复追加相同原因
                if reason in old_reason:
                    return
                new_reason = f"{old_reason}; {reason}"
            else:
                new_reason = reason
            self.upsert_anomaly_mark([{
                "旧文件名": filename,
                "异常标记": "是",
                "标记原因": new_reason,
            }])
        else:
            self.upsert_anomaly_mark([{
                "旧文件名": filename,
                "异常标记": "是",
                "标记原因": reason,
            }])

    def get_anomaly_records(self) -> list[dict]:
        """读取所有标记了异常的发票记录"""
        return self._fetchall('SELECT * FROM invoices WHERE 异常标记 = "是"')

    def clear_anomaly_mark(self, filename: str) -> None:
        """清除指定发票的异常标记"""
        self.upsert_anomaly_mark([{
            "旧文件名": filename,
            "异常标记": "",
            "标记原因": "",
        }])

    # ------------------------------------------------------------------
    # 通用查询接口
    # ------------------------------------------------------------------

    def to_dataframe(self, where: str = "", params: tuple = ()) -> pd.DataFrame:
        """将整个发票表导出为 DataFrame"""
        sql = "SELECT * FROM invoices"
        if where:
            sql += f" WHERE {where}"
        rows = self._fetchall(sql, params)
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows)
        # 还原 list 字段
        for col in ("商品单价", "商品名称"):
            if col in df.columns:
                df[col] = df[col].apply(_json_loads_safe)
        return df

    def get_by_filename(self, filename: str) -> Optional[dict]:
        """按旧文件名查询单条发票记录"""
        return self._fetchone(
            "SELECT * FROM invoices WHERE 旧文件名 = ?", (filename,)
        )

    def get_all_filenames(self) -> list[str]:
        """获取所有已录入的发票文件名"""
        rows = self._fetchall("SELECT 旧文件名 FROM invoices")
        return [r["旧文件名"] for r in rows]

    def delete_all(self) -> None:
        """清空发票表（用于数据清理）"""
        self._execute("DELETE FROM invoices")
        logger.info("已清空发票数据库")

    def lookup_invoice_details(self, invoice_name: str) -> dict:
        """
        查找发票的详细信息（用于附件检查、文件名检查等模块）。

        返回所有非空字段（排除 id, updated_at 等元数据），
        具体哪些字段传给 LLM 由 rules.md 的「数据字段」配置决定。
        """
        row = self.get_by_filename(invoice_name)
        if not row:
            return {}
        _SKIP = {"id", "updated_at", "旧文件名"}
        result = {}
        for k, v in row.items():
            if k in _SKIP:
                continue
            if v is not None and str(v).strip():
                result[k] = str(v).strip()
        return result

    def get_all_invoice_data(self) -> Dict[str, Dict]:
        """
        返回 {旧文件名: {字段字典}} 的完整映射。

        等价于原 checker._load_all_invoice_data() 从多个 Excel 合并的逻辑。
        """
        rows = self._fetchall("SELECT * FROM invoices")
        result: Dict[str, Dict] = {}
        for row in rows:
            name = row.get("旧文件名", "")
            if not name:
                continue
            clean = {}
            for k, v in row.items():
                if k in ("id", "updated_at"):
                    continue
                if v is not None and str(v).strip():
                    clean[k] = str(v).strip()
            result[name] = clean
        return result

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _upsert_records(
        self,
        records: list[dict],
        fields: list[str],
        key_field: str,
    ) -> int:
        """通用 UPSERT 方法：按 key_field 去重，只更新 fields 中列出的字段"""
        if not records:
            return 0

        table_cols = set(self._get_table_columns("invoices"))
        valid_fields = [f for f in fields if f in table_cols]

        if key_field not in valid_fields:
            valid_fields.append(key_field)

        # 构建 UPSERT SQL
        cols = ", ".join(f'"{f}"' for f in valid_fields)
        placeholders = ", ".join("?" for _ in valid_fields)
        update_parts = ", ".join(
            f'"{f}" = excluded."{f}"'
            for f in valid_fields if f != key_field
        )

        sql = (
            f"INSERT INTO invoices ({cols}) VALUES ({placeholders}) "
            f"ON CONFLICT(旧文件名) DO UPDATE SET {update_parts}"
        )

        count = 0
        with self._connect() as conn:
            for record in records:
                values = []
                for f in valid_fields:
                    v = record.get(f, "")
                    # 将 list/dict 序列化为 JSON
                    if isinstance(v, (list, dict)):
                        v = json.dumps(v, ensure_ascii=False)
                    elif v is None:
                        v = ""
                    values.append(v)
                try:
                    conn.execute(sql, tuple(values))
                    count += 1
                except sqlite3.Error as exc:
                    logger.warning("发票记录写入失败 (%s): %s", record.get(key_field, "?"), exc)

        logger.info("发票数据库: 写入/更新 %d 条记录 (字段: %s)", count, ", ".join(valid_fields))
        return count


# =========================================================================
# 记录数据库
# =========================================================================

class RecordDatabase(_BaseDatabase):
    """
    报销记录数据库

    以 ``id``（auto-increment）为唯一标识，``序号`` 仅为显示/排序字段，允许重复。
    汇总以下数据：
        - 输入的报销明细（hand_data）
        - 记录匹配结果（matcher.py）

    Usage::

        db = RecordDatabase("output/records.db")
        db.upsert_records([{"序号": 1, "姓名/公司": "张三", "金额": 100}])
        db.upsert_match_results([{"序号": 1, "是否匹配": "已匹配"}])
        df = db.to_dataframe()
    """

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(_RECORD_TABLE_SQL)
            conn.execute(_RECORD_TRIGGER_SQL)
            self._migrate_records_table(conn)

    def _migrate_records_table(self, conn) -> None:
        """
        对已存在的 records 表执行 schema 迁移，处理两种历史遗留问题：

        1. 旧版表在 ``序号`` 上有 UNIQUE 约束
           → SQLite 不支持 DROP CONSTRAINT，需整表重建
           → 重建时同步补入 ``填写日期`` 列（若缺失）

        2. 旧版表缺少 ``填写日期`` 列（但无 UNIQUE 约束）
           → 直接 ALTER TABLE ADD COLUMN
        """
        # ── 获取当前列信息 ────────────────────────────────────
        col_rows = conn.execute("PRAGMA table_info(records)").fetchall()
        existing_cols = {row[1] for row in col_rows}  # row[1] = name

        # ── 检查 序号 是否带 UNIQUE 索引 ─────────────────────
        has_unique_seq = False
        for idx in conn.execute("PRAGMA index_list(records)").fetchall():
            if idx[2]:  # unique 标志位
                for col in conn.execute(
                    f"PRAGMA index_info('{idx[1]}')"
                ).fetchall():
                    if col[2] == "序号":
                        has_unique_seq = True
                        break

        if has_unique_seq:
            # ── 整表重建：移除 UNIQUE 约束 + 补齐新列 ─────────
            # SQLite 不支持 ALTER TABLE DROP CONSTRAINT，
            # 标准做法是：重命名旧表 → 创建新表 → 迁移数据 → 删旧表。
            logger.info("records 表迁移: 检测到 序号 UNIQUE 约束，开始整表重建...")

            # 只迁移新旧表都有的列（避免 SELECT 缺列报错）
            all_new_cols = [
                "id", "序号", "姓名/公司", "填写日期", "金额",
                "物品简介", "备注", "extra_fields",
                "匹配发票", "匹配发票金额", "是否匹配",
                "匹配方式", "组合金额", "备注分解金额", "未匹配金额",
                "updated_at",
                # 附件检查字段（从 invoices 迁移）
                "附件状态", "缺少类型", "匹配附件", "附件路径",
                "生成文件", "校验详情", "附件类别",
            ]
            copy_cols = [c for c in all_new_cols if c in existing_cols]
            col_str = ", ".join(f'"{c}"' for c in copy_cols)

            conn.execute("ALTER TABLE records RENAME TO _records_old")
            conn.execute(_RECORD_TABLE_SQL)          # 建新表（无 UNIQUE）
            if copy_cols:
                conn.execute(
                    f'INSERT INTO records ({col_str}) '
                    f'SELECT {col_str} FROM _records_old'
                )
            conn.execute("DROP TABLE _records_old")
            logger.info(
                "records 表迁移完成: 移除 序号 UNIQUE 约束，迁移 %d 列",
                len(copy_cols),
            )
            return

        # ── 无需重建，仅补加缺失列 ────────────────────────────
        if "填写日期" not in existing_cols:
            conn.execute(
                "ALTER TABLE records ADD COLUMN 填写日期 TEXT DEFAULT ''"
            )
            logger.info("records 表迁移: 已添加 填写日期 列")

        # 附件检查字段迁移：从 invoices 迁移而来的 7 个列
        for col in _ATTACHMENT_FIELDS:
            if col not in existing_cols:
                try:
                    conn.execute(
                        f'ALTER TABLE records ADD COLUMN "{col}" TEXT DEFAULT \'\''
                    )
                    existing_cols.add(col)
                    logger.info("records 表迁移: 已添加 %s 列（来自 invoices 迁移）", col)
                except sqlite3.Error as exc:
                    logger.warning("records 表添加列 %s 失败: %s", col, exc)

        # 浏览器报错列迁移
        if "浏览器报错" not in existing_cols:
            try:
                conn.execute(
                    "ALTER TABLE records ADD COLUMN 浏览器报错 TEXT DEFAULT ''"
                )
                existing_cols.add("浏览器报错")
                logger.info("records 表迁移: 已添加 浏览器报错 列")
            except sqlite3.Error as exc:
                logger.warning("records 表添加列 浏览器报错 失败: %s", exc)

    # ------------------------------------------------------------------
    # 动态列管理
    # ------------------------------------------------------------------

    def _ensure_columns(self, conn, col_names: Sequence[str]) -> None:
        """
        确保 records 表中存在指定的列，不存在则自动 ALTER TABLE ADD COLUMN。

        首次加载明细时会将 Excel 中的所有列都作为数据库列创建，
        而非存入 extra_fields JSON，以便后续查询和展示更加方便。

        Args:
            conn: 已打开的数据库连接
            col_names: 需要确保存在的列名列表
        """
        existing_cols = {
            row[1]
            for row in conn.execute("PRAGMA table_info(records)").fetchall()
        }
        for col in col_names:
            if col not in existing_cols:
                safe_col = f'"{col}"'
                try:
                    conn.execute(
                        f"ALTER TABLE records ADD COLUMN {safe_col} TEXT DEFAULT ''"
                    )
                    existing_cols.add(col)
                    logger.info("records 表动态扩展: 已添加 %s 列", col)
                except sqlite3.Error as exc:
                    logger.warning("records 表添加列 %s 失败: %s", col, exc)

    # ------------------------------------------------------------------
    # 明细数据接口
    # ------------------------------------------------------------------

    def upsert_records(self, records: list[dict]) -> int:
        """
        写入报销明细记录（纯 INSERT）。

        原始输入的 ``序号`` 会被丢弃，入库时从 ``MAX(序号)+1`` 开始
        按插入顺序重新生成连续编号（表为空时从 1 开始）。
        这样可避免 Excel 明细中序号重复或不连续带来的下游匹配问题。

        每条记录以 ``id``（auto-increment）作为唯一标识。
        调用方应在插入前自行处理重复逻辑（如先 delete_all 再批量插入）。

        明细中的所有列都会作为数据库实际列写入（不存在的列会自动创建），
        不再使用 ``extra_fields`` JSON 存储额外字段。

        Args:
            records: 明细记录列表

        Returns:
            成功插入的行数
        """
        if not records:
            return 0

        count = 0

        # 收集所有记录中出现的列名，提前一次性建好
        all_col_names: set[str] = set()
        for record in records:
            for k in record.keys():
                if k not in ("id", "updated_at", "序号"):
                    all_col_names.add(k)

        with self._connect() as conn:
            # 动态扩展表结构：确保所有列都存在
            self._ensure_columns(conn, list(all_col_names))

            # 获取扩展后的实际表列集合
            table_cols = {
                row[1]
                for row in conn.execute("PRAGMA table_info(records)").fetchall()
            }

            # 确定本批次起始序号：接续表内现有最大序号
            row = conn.execute("SELECT COALESCE(MAX(序号), 0) FROM records").fetchone()
            next_seq: int = (row[0] if row else 0) + 1

            for record in records:
                # 收集所有字段，原始序号一律丢弃（重新生成）
                row_data: dict = {}
                for k, v in record.items():
                    if k in ("id", "updated_at", "序号"):
                        continue
                    # 跳过 NaN 值
                    if isinstance(v, float) and pd.isna(v):
                        v = ""
                    # 只写入表中实际存在的列
                    if k in table_cols:
                        row_data[k] = v

                # 写入重新生成的序号
                row_data["序号"] = next_seq
                # extra_fields 保留为空 JSON（向后兼容）
                row_data["extra_fields"] = "{}"

                cols = list(row_data.keys())
                col_str = ", ".join(f'"{c}"' for c in cols)
                placeholders = ", ".join("?" for _ in cols)

                sql = f"INSERT INTO records ({col_str}) VALUES ({placeholders})"

                values = []
                for c in cols:
                    v = row_data.get(c, "")
                    if isinstance(v, (list, dict)):
                        v = json.dumps(v, ensure_ascii=False, default=str)
                    elif v is None:
                        v = ""
                    values.append(v)

                try:
                    conn.execute(sql, tuple(values))
                    next_seq += 1
                    count += 1
                except sqlite3.Error as exc:
                    logger.warning("记录写入失败 (新序号=%s): %s", next_seq, exc)

        logger.info("记录数据库: 插入 %d 条记录，序号 %d–%d",
                    count, next_seq - count, next_seq - 1)
        return count

    # ------------------------------------------------------------------
    # 匹配结果接口
    # ------------------------------------------------------------------

    def upsert_match_results(self, records: list[dict]) -> int:
        """
        写入 / 更新记录匹配结果。

        由于 ``序号`` 不唯一，改为 ``UPDATE WHERE 序号 = ?``，
        对所有具有相同序号的行统一写入匹配结果（重复序号共享同一匹配）。

        Args:
            records: 每项至少包含 ``序号`` 和匹配相关字段
        """
        if not records:
            return 0

        match_fields = [
            "匹配发票", "匹配发票金额", "是否匹配",
            "匹配方式", "组合金额", "备注分解金额", "未匹配金额",
        ]

        count = 0
        with self._connect() as conn:
            for record in records:
                seq = record.get("序号")
                if seq is None:
                    continue

                update_cols = []
                values = []
                for f in match_fields:
                    if f in record:
                        update_cols.append(f)
                        v = record[f]
                        if isinstance(v, (list, dict)):
                            v = json.dumps(v, ensure_ascii=False, default=str)
                        elif v is None:
                            v = ""
                        values.append(v)

                if not update_cols:
                    continue

                # UPDATE 所有序号匹配的行（兼容重复序号）
                set_str = ", ".join(f'"{c}" = ?' for c in update_cols)
                values.append(seq)
                sql = f"UPDATE records SET {set_str} WHERE 序号 = ?"

                try:
                    conn.execute(sql, tuple(values))
                    count += 1
                except sqlite3.Error as exc:
                    logger.warning("匹配结果写入失败 (序号=%s): %s", seq, exc)

        logger.info("记录数据库: 写入/更新 %d 条匹配结果", count)
        return count

    def upsert_category(self, records: list[dict]) -> int:
        """
        写入 / 更新记录的分类结果（``category`` 字段）。

        ``category`` 列在旧版 schema 中可能不存在，首次调用时自动执行
        ``ALTER TABLE ADD COLUMN`` 迁移，后续调用直接跳过。

        由于 ``序号`` 不唯一，对所有匹配序号的行统一写入相同类别
        （与 upsert_match_results 行为一致）。

        Args:
            records: 列表，每项包含 ``序号``(int) 和 ``category``(str)

        Returns:
            成功更新的行数（按序号计，非物理行数）
        """
        if not records:
            return 0

        # 确保 category 列存在（懒迁移）
        with self._connect() as conn:
            existing_cols = {
                row[1]
                for row in conn.execute("PRAGMA table_info(records)").fetchall()
            }
            if "category" not in existing_cols:
                conn.execute(
                    "ALTER TABLE records ADD COLUMN category TEXT DEFAULT ''"
                )
                logger.info("records 表迁移: 已添加 category 列")

        count = 0
        with self._connect() as conn:
            for record in records:
                seq = record.get("序号")
                category = record.get("category", "") or ""
                if seq is None:
                    continue
                try:
                    conn.execute(
                        "UPDATE records SET category = ? WHERE 序号 = ?",
                        (category, int(seq)),
                    )
                    count += 1
                except sqlite3.Error as exc:
                    logger.warning("分类结果写入失败 (序号=%s): %s", seq, exc)

        logger.info("记录数据库: 写入/更新 %d 条分类结果", count)
        return count

    # ------------------------------------------------------------------
    # 附件检查接口（从 InvoiceDatabase 迁移而来）
    # ------------------------------------------------------------------

    # 附件状态的严重度排序：值越大越严重，聚合时取最大值
    _ATT_STATUS_SEVERITY = {
        "": 0,
        "附件齐全": 1,
        "已自动生成": 2,
        "附件已修复": 2,
        "附件校验不通过": 3,
        "缺少附件": 4,
    }

    def _build_invoice_to_records_map(self) -> Dict[str, List[int]]:
        """
        构建 {发票文件名: [record_id, ...]} 映射。

        通过解析 records.匹配发票（逗号分隔的发票文件名列表）反向建表，
        供 checker 将按发票汇总的检查结果写回对应的报销记录。

        一张发票可能被多条记录匹配（虽少见但合法），一条记录也可能
        对应多张发票——两种关系都通过列表自然表达。
        """
        rows = self._fetchall(
            "SELECT id, 匹配发票 FROM records "
            "WHERE 匹配发票 IS NOT NULL AND 匹配发票 != ''"
        )
        mapping: Dict[str, List[int]] = {}
        for row in rows:
            rid = row.get("id")
            raw = row.get("匹配发票", "") or ""
            for name in raw.split(","):
                name = name.strip()
                if not name:
                    continue
                mapping.setdefault(name, []).append(rid)
        return mapping

    def upsert_attachment_check(
        self,
        results_by_invoice: List[Dict[str, Any]],
    ) -> int:
        """
        写入附件检查结果到 records 表。

        该方法替代原来 InvoiceDatabase.upsert_attachment_check 的作用。
        输入仍然是按「发票文件名」维度的检查结果列表（与 checker 内部
        处理一致），但会通过 records.匹配发票 反向查找对应的报销记录，
        并把结果聚合写入 records 对应的附件字段。

        Args:
            results_by_invoice: 每项包含：
                - 旧文件名 (必填): 发票文件名
                - 附件状态, 缺少类型, 匹配附件, 附件路径,
                  生成文件, 校验详情, 附件类别

        多发票→单记录时的聚合规则：
            - 附件状态: 取严重度最高的状态（缺少附件 > 校验不通过 > 其他）
            - 缺少类型/匹配附件/附件路径/生成文件: 逗号去重合并
            - 校验详情: 每条前缀带 `[发票名]`，用 `; ` 连接
            - 附件类别: 取第一个非空值

        Returns:
            受影响的 record id 数量
        """
        if not results_by_invoice:
            return 0

        # 确保所需列存在（对旧库做懒迁移）
        with self._connect() as conn:
            self._ensure_columns(conn, list(_ATTACHMENT_FIELDS))

        # 构建 invoice → record_ids 映射
        inv_to_rids = self._build_invoice_to_records_map()

        # 按 record_id 聚合结果
        rid_to_items: Dict[int, List[Dict[str, Any]]] = {}
        unmatched: List[str] = []
        for item in results_by_invoice:
            inv_name = item.get("旧文件名") or item.get("发票文件") or ""
            if not inv_name:
                continue
            rids = inv_to_rids.get(inv_name) or []
            if not rids:
                unmatched.append(inv_name)
                continue
            for rid in rids:
                rid_to_items.setdefault(rid, []).append(item)

        if unmatched:
            logger.warning(
                "附件检查: %d 张发票未能关联到任何记录（records.匹配发票 为空或不包含该文件名）: %s",
                len(unmatched),
                ", ".join(unmatched[:5]) + (" ..." if len(unmatched) > 5 else ""),
            )

        count = 0
        with self._connect() as conn:
            for rid, items in rid_to_items.items():
                agg = self._aggregate_attachment_items(items)
                set_parts = ", ".join(f'"{k}" = ?' for k in agg.keys())
                values = list(agg.values()) + [rid]
                try:
                    conn.execute(
                        f"UPDATE records SET {set_parts} WHERE id = ?",
                        tuple(values),
                    )
                    count += 1
                except sqlite3.Error as exc:
                    logger.warning("附件检查写入失败 (id=%d): %s", rid, exc)

        logger.info(
            "记录数据库: 附件检查写入 %d 条记录（覆盖 %d 张发票）",
            count, len(results_by_invoice),
        )
        return count

    @classmethod
    def _aggregate_attachment_items(
        cls, items: List[Dict[str, Any]],
    ) -> Dict[str, str]:
        """
        把一条记录对应的多张发票检查结果聚合成 records 表的一行字段。

        - 附件状态：取严重度最高
        - 缺少类型/匹配附件/附件路径/生成文件：逗号合并去重
        - 校验详情：每条按 `[发票名] 详情` 拼接，`; ` 分隔
        - 附件类别：首个非空
        """
        if not items:
            return {f: "" for f in _ATTACHMENT_FIELDS}

        worst_status = ""
        worst_score = -1
        for it in items:
            s = (it.get("附件状态") or "").strip()
            score = cls._ATT_STATUS_SEVERITY.get(s, 0)
            if score > worst_score:
                worst_score = score
                worst_status = s

        def _join_unique(key: str) -> str:
            seen: List[str] = []
            for it in items:
                raw = (it.get(key) or "").strip()
                if not raw:
                    continue
                for piece in raw.split(","):
                    piece = piece.strip()
                    if piece and piece not in seen:
                        seen.append(piece)
            return ",".join(seen)

        # 校验详情：多张发票时前缀带 [发票名]，单张时不带前缀
        detail_parts: List[str] = []
        for it in items:
            d = (it.get("校验详情") or "").strip()
            if not d:
                continue
            if len(items) > 1:
                inv = it.get("旧文件名") or it.get("发票文件") or ""
                detail_parts.append(f"[{inv}] {d}" if inv else d)
            else:
                detail_parts.append(d)
        detail = "; ".join(detail_parts)

        category = ""
        for it in items:
            c = (it.get("附件类别") or "").strip()
            if c:
                category = c
                break

        return {
            "附件状态": worst_status,
            "缺少类型": _join_unique("缺少类型"),
            "匹配附件": _join_unique("匹配附件"),
            "附件路径": _join_unique("附件路径"),
            "生成文件": _join_unique("生成文件"),
            "校验详情": detail,
            "附件类别": category,
        }

    def append_validation_detail(
        self, invoice_filename: str, reason: str,
    ) -> None:
        """
        追加校验错误信息到对应记录的 校验详情 字段（通过 匹配发票 反查）。

        与旧版 InvoiceDatabase.append_anomaly_reason 对应，但写入目标改为
        records.校验详情（而不是 invoices.标记原因），符合
        「checker 的检查报错输入到记录数据库校验详情」的要求。

        - 若已含相同 reason 则跳过，避免重复
        - 同一条记录被多张发票追加时，新旧原因用 `; ` 连接
        - 未能反查到 record 的发票会被静默忽略（已有 warning 从
          upsert_attachment_check 处产生）
        """
        if not invoice_filename or not reason:
            return

        inv_to_rids = self._build_invoice_to_records_map()
        rids = inv_to_rids.get(invoice_filename) or []
        if not rids:
            return

        # 确保列存在
        with self._connect() as conn:
            self._ensure_columns(conn, ["校验详情", "附件状态"])

        with self._connect() as conn:
            for rid in rids:
                row = conn.execute(
                    'SELECT "校验详情" FROM records WHERE id = ?', (rid,),
                ).fetchone()
                if row is None:
                    continue
                old = (row[0] or "").strip()
                if old:
                    if reason in old:
                        continue
                    new = f"{old}; {reason}"
                else:
                    new = reason
                try:
                    conn.execute(
                        'UPDATE records SET "校验详情" = ? WHERE id = ?',
                        (new, rid),
                    )
                except sqlite3.Error as exc:
                    logger.warning(
                        "追加校验详情失败 (id=%d): %s", rid, exc,
                    )

    def get_attachment_check(self, category: str = "") -> list[dict]:
        """读取附件检查结果（从 records 表）"""
        if category:
            return self._fetchall(
                'SELECT * FROM records WHERE 附件类别 = ? AND 附件状态 != ""',
                (category,),
            )
        return self._fetchall('SELECT * FROM records WHERE 附件状态 != ""')

    # ------------------------------------------------------------------
    # 浏览器报错接口
    # ------------------------------------------------------------------

    def upsert_browser_error(self, record_id: int, error_text: str, append: bool = True) -> bool:
        """
        写入 / 追加浏览器自动化报错信息到指定记录。

        Args:
            record_id: 记录主键 id
            error_text: 报错内容
            append: True=追加到已有内容后(用分号分隔), False=覆盖

        Returns:
            是否写入成功
        """
        if not error_text:
            return False

        # 确保列存在
        with self._connect() as conn:
            self._ensure_columns(conn, ["浏览器报错"])

        if append:
            existing = self.get_record_by_id(record_id)
            if existing:
                old = (existing.get("浏览器报错") or "").strip()
                if old:
                    if error_text in old:
                        return True
                    error_text = f"{old}; {error_text}"

        try:
            self._execute(
                'UPDATE records SET "浏览器报错" = ? WHERE id = ?',
                (error_text, record_id),
            )
            logger.info("记录数据库: 浏览器报错写入 id=%d", record_id)
            return True
        except sqlite3.Error as exc:
            logger.warning("浏览器报错写入失败 (id=%d): %s", record_id, exc)
            return False

    def upsert_browser_error_by_seq(self, seq: int, error_text: str, append: bool = True) -> int:
        """
        按序号写入浏览器报错（对所有匹配序号的记录写入）。

        Args:
            seq: 记录序号
            error_text: 报错内容
            append: True=追加, False=覆盖

        Returns:
            成功写入的记录数
        """
        rows = self.get_by_seq_all(seq)
        count = 0
        for row in rows:
            rid = row.get("id")
            if rid and self.upsert_browser_error(rid, error_text, append=append):
                count += 1
        return count

    # ------------------------------------------------------------------
    # 查询接口
    # ------------------------------------------------------------------

    def to_dataframe(self, where: str = "", params: tuple = ()) -> pd.DataFrame:
        """将记录表导出为 DataFrame，自动展开 extra_fields"""
        sql = "SELECT * FROM records"
        if where:
            sql += f" WHERE {where}"
        sql += " ORDER BY 序号"

        rows = self._fetchall(sql, params)
        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows)

        # 展开 extra_fields 到独立列
        if "extra_fields" in df.columns:
            extras = df["extra_fields"].apply(_json_loads_safe)
            extra_df = pd.json_normalize(extras)
            if not extra_df.empty:
                # 避免列名冲突
                for col in extra_df.columns:
                    if col not in df.columns:
                        df[col] = extra_df[col]
            df = df.drop(columns=["extra_fields"])

        # 移除内部字段
        for col in ("id", "updated_at"):
            if col in df.columns:
                df = df.drop(columns=[col])

        return df

    def get_by_seq(self, seq: int) -> Optional[dict]:
        """按序号查询记录（可能返回多行，取第一条）"""
        return self._fetchone("SELECT * FROM records WHERE 序号 = ? ORDER BY id", (seq,))

    def get_by_seq_all(self, seq: int) -> list[dict]:
        """按序号查询所有匹配记录（序号允许重复）"""
        return self._fetchall("SELECT * FROM records WHERE 序号 = ? ORDER BY id", (seq,))

    def get_all_seqs(self) -> list[int]:
        """获取所有记录序号（可能含重复）"""
        rows = self._fetchall("SELECT 序号 FROM records ORDER BY id")
        return [r["序号"] for r in rows]

    def get_all_ids(self) -> list[int]:
        """获取所有记录的主键 id（用于前端 diff 同步）"""
        rows = self._fetchall("SELECT id FROM records ORDER BY id")
        return [r["id"] for r in rows]

    def get_record_by_id(self, record_id: int) -> Optional[dict]:
        """按主键 id 查询单条记录"""
        return self._fetchone("SELECT * FROM records WHERE id = ?", (record_id,))

    def update_record_by_id(self, record_id: int, fields: dict) -> bool:
        """
        按主键 id 更新指定字段。

        所有字段都作为实际数据库列更新，不存在的列会自动创建。

        Args:
            record_id: 记录主键
            fields: 要更新的字段字典

        Returns:
            是否成功更新
        """
        if not fields:
            return False

        updates = {}
        for k, v in fields.items():
            if k in ("id", "updated_at"):
                continue
            updates[k] = v

        if not updates:
            return False

        # 动态扩展表结构：确保所有列都存在
        with self._connect() as conn:
            self._ensure_columns(conn, list(updates.keys()))

        set_parts = ", ".join(f'"{k}" = ?' for k in updates)
        values = list(updates.values())
        for i, v in enumerate(values):
            if isinstance(v, (list, dict)):
                values[i] = json.dumps(v, ensure_ascii=False, default=str)
            elif v is None:
                values[i] = ""

        values.append(record_id)
        sql = f"UPDATE records SET {set_parts} WHERE id = ?"

        try:
            self._execute(sql, tuple(values))
            logger.info("记录数据库: 更新 id=%d, 字段: %s", record_id, list(updates.keys()))
            return True
        except sqlite3.Error as exc:
            logger.warning("记录更新失败 (id=%d): %s", record_id, exc)
            return False

    def delete_record_by_id(self, record_id: int) -> bool:
        """按主键 id 删除单条记录"""
        try:
            self._execute("DELETE FROM records WHERE id = ?", (record_id,))
            logger.info("记录数据库: 删除 id=%d", record_id)
            return True
        except sqlite3.Error as exc:
            logger.warning("记录删除失败 (id=%d): %s", record_id, exc)
            return False

    def create_record(self, data: dict) -> Optional[int]:
        """
        创建一条新记录并返回其自增主键 id。

        前端新增行时调用，立即写入数据库并拿回 db_id，
        后续编辑单元格时才能正确 PUT /api/records/{db_id}。

        Args:
            data: 记录字段字典，如 {"序号": 12, "姓名/公司": "张三", ...}

        Returns:
            新记录的主键 id，失败返回 None
        """
        row_data: dict = {}

        for k, v in data.items():
            if k in ("id", "updated_at", "db_id"):
                continue
            if isinstance(v, float) and pd.isna(v):
                v = ""
            row_data[k] = v

        # extra_fields 保留为空 JSON（向后兼容）
        row_data["extra_fields"] = "{}"

        try:
            with self._connect() as conn:
                # 动态扩展表结构
                self._ensure_columns(conn, list(row_data.keys()))

                cols = list(row_data.keys())
                col_str = ", ".join(f'"{c}"' for c in cols)
                placeholders = ", ".join("?" for _ in cols)
                sql = f"INSERT INTO records ({col_str}) VALUES ({placeholders})"

                values = []
                for c in cols:
                    v = row_data.get(c, "")
                    if isinstance(v, (list, dict)):
                        v = json.dumps(v, ensure_ascii=False, default=str)
                    elif v is None:
                        v = ""
                    values.append(v)

                cursor = conn.execute(sql, tuple(values))
                new_id = cursor.lastrowid
            logger.info("记录数据库: 创建记录 id=%d, 序号=%s",
                        new_id, data.get("序号", "?"))
            return new_id
        except sqlite3.Error as exc:
            logger.warning("记录创建失败: %s", exc)
            return None

    def batch_upsert(self, rows: list[dict]) -> dict:
        """
        批量保存记录：已有 id 的行执行 UPDATE，无 id 的行执行 INSERT。

        前端点击「保存到数据库」时调用，一次性同步全部表格数据。

        Args:
            rows: 前端行对象列表，每行可能有 db_id（已存在）或没有（新增）

        Returns:
            {"inserted": int, "updated": int, "errors": int}
        """
        stats = {"inserted": 0, "updated": 0, "errors": 0}

        for row in rows:
            db_id = row.get("db_id")
            try:
                if db_id:
                    # UPDATE 已有记录
                    fields = {}
                    for k, v in row.items():
                        if k in ("id", "db_id", "updated_at"):
                            continue
                        # 前端「类别」→ DB「category」
                        if k == "类别":
                            k = "category"
                        fields[k] = v
                    if fields:
                        self.update_record_by_id(db_id, fields)
                        stats["updated"] += 1
                else:
                    # INSERT 新记录
                    data = {}
                    for k, v in row.items():
                        if k in ("id", "db_id", "updated_at"):
                            continue
                        if k == "类别":
                            k = "category"
                        data[k] = v
                    new_id = self.create_record(data)
                    if new_id:
                        stats["inserted"] += 1
                    else:
                        stats["errors"] += 1
            except Exception as exc:
                logger.warning("batch_upsert 行处理失败: %s", exc)
                stats["errors"] += 1

        logger.info(
            "记录数据库: 批量保存完成 — 插入 %d, 更新 %d, 失败 %d",
            stats["inserted"], stats["updated"], stats["errors"],
        )
        return stats

    # 关联发票时需要提取的字段（供前端提示词模板引用）
    # 注意：附件相关字段（匹配附件 / 附件路径 / 生成文件）已迁移到 records
    # 表，不再从 invoices 关联读取。此处只保留发票本体的 OCR / 分类信息。
    _INVOICE_JOIN_FIELDS = (
        "旧文件名", "category",
        "发票号码", "价税合计", "商品名称", "商品单价", "开票日期",
        "销售方名称", "发票类型", "full_path",
        "购方名称", "购方税号", "税额", "发票代码", "校验码",
        "匹配姓名", "匹配简介", "匹配金额",
    )

    def get_records_joined(self, invoice_db: "InvoiceDatabase") -> list[dict]:
        """
        获取所有记录，并关联 invoices 表中的发票详情。

        通过 records.匹配发票 与 invoices.旧文件名 进行关联。
        支持逗号分隔的多发票匹配（如 "a.pdf,b.pdf"），会聚合所有匹配发票的信息。

        关联的发票字段（动态读取 _INVOICE_JOIN_FIELDS 中的所有列，
        模板可通过 {{变量名}} 直接引用任意非空字段，无需改代码）：
            - 类别、发票号码、价税合计、商品名称、商品单价、开票日期、
              销售方名称、发票类型、购方名称、匹配姓名、匹配简介 等

        **附件字段已迁移到 records 表**（附件状态 / 缺少类型 / 匹配附件 /
        附件路径 / 生成文件 / 校验详情 / 附件类别），直接从 record 行读取，
        不再从 invoices 表 JOIN。

        Args:
            invoice_db: 发票数据库实例

        Returns:
            增强后的记录列表（含发票详情字段）
        """
        rows = self._fetchall("SELECT * FROM records ORDER BY 序号")
        if not rows:
            return []

        # 构建发票名 → 发票详情的映射（只含发票本体信息，不含附件）
        inv_map: Dict[str, Dict] = {}
        try:
            fields_sql = ", ".join(f'"{f}"' for f in self._INVOICE_JOIN_FIELDS)
            inv_rows = invoice_db._fetchall(f"SELECT {fields_sql} FROM invoices")
            for ir in inv_rows:
                name = ir.get("旧文件名", "")
                if name:
                    # 发票路径：优先用 full_path，为空则用 app/data/旧文件名
                    inv_full_path = ir.get("full_path", "") or ""
                    if not inv_full_path and name:
                        inv_full_path = f"app/data/{name}"
                    # 动态构建发票详情（排除元数据字段）
                    info = {"类别": ir.get("category", ""), "发票路径": inv_full_path}
                    _SKIP_JOIN = {"旧文件名", "category", "full_path"}
                    for fk, fv in ir.items():
                        if fk in _SKIP_JOIN:
                            continue
                        if fv is not None and str(fv).strip():
                            info[fk] = str(fv).strip()
                    inv_map[name] = info
        except Exception:
            pass

        result = []
        for row in rows:
            # 展开 extra_fields
            extra = _json_loads_safe(row.get("extra_fields", "{}"))
            if not isinstance(extra, dict):
                extra = {}

            record = {k: v for k, v in row.items() if k != "extra_fields"}
            record.update(extra)

            # 关联发票信息（支持逗号分隔的多发票匹配）
            matched_inv = row.get("匹配发票", "")
            inv_category = ""
            inv_invoice_paths = []
            inv_details: Dict[str, str] = {}

            if matched_inv:
                inv_names = [n.strip() for n in matched_inv.split(",") if n.strip()]
                for inv_name in inv_names:
                    if inv_name in inv_map:
                        inv_info = inv_map[inv_name]
                        # 类别取第一个非空值
                        if not inv_category and inv_info.get("类别"):
                            inv_category = inv_info["类别"]
                        # 聚合发票路径
                        inv_path = inv_info.get("发票路径", "")
                        if inv_path:
                            inv_invoice_paths.append(inv_path)
                        # 发票详情字段：动态取所有非空值（取第一个非空）
                        _SKIP_DETAIL = {"类别", "发票路径"}
                        for fk, fv in inv_info.items():
                            if fk in _SKIP_DETAIL:
                                continue
                            if fk not in inv_details and fv:
                                inv_details[fk] = fv

            # 类别优先级：发票关联 > records.category > extra_fields
            record_category = record.get("category", "") or ""
            extra_category = extra.get("类别", "") or ""
            record["类别"] = inv_category or record_category or extra_category

            # 附件字段：直接来自 records 表（已由 checker 写入），
            # 不再从 invoices JOIN。若记录本身没有则回退 extra_fields。
            for att_field in ("附件状态", "缺少类型", "匹配附件",
                              "附件路径", "生成文件", "校验详情", "附件类别"):
                if not record.get(att_field):
                    record[att_field] = extra.get(att_field, "")

            # 发票路径：聚合发票的完整路径
            if inv_invoice_paths:
                record["发票路径"] = ",".join(
                    dict.fromkeys(a for a in inv_invoice_paths if a)
                )
            else:
                record.setdefault("发票路径", extra.get("发票路径", ""))

            # 写入发票详情字段（不覆盖已有值）
            for fk, fv in inv_details.items():
                if not record.get(fk):
                    record[fk] = fv

            result.append(record)

        return result

    def delete_all(self) -> None:
        """清空记录表"""
        self._execute("DELETE FROM records")
        logger.info("已清空记录数据库")

    def count_records(self) -> int:
        return self.count("records")


# =========================================================================
# 工厂函数
# =========================================================================

def get_invoice_db(settings) -> InvoiceDatabase:
    """从 Settings 获取发票数据库实例"""
    db_path = settings.paths.output_dir / "invoices.db"
    return InvoiceDatabase(db_path)


def get_record_db(settings) -> RecordDatabase:
    """从 Settings 获取记录数据库实例"""
    db_path = settings.paths.output_dir / "records.db"
    return RecordDatabase(db_path)


# =========================================================================
# 工具函数
# =========================================================================

def _json_loads_safe(val) -> Any:
    """安全解析 JSON 字符串，解析失败则返回原值"""
    if not val or not isinstance(val, str):
        return val
    try:
        return json.loads(val)
    except (json.JSONDecodeError, TypeError):
        return val


def dataframe_to_records(df: pd.DataFrame) -> list[dict]:
    """将 DataFrame 转为 dict 列表，处理 NaN 值"""
    records = df.to_dict("records")
    for rec in records:
        for k, v in rec.items():
            if pd.isna(v) if isinstance(v, float) else False:
                rec[k] = ""
    return records