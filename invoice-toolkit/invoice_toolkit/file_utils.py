"""
文件工具模块 — 目录扫描、文件移动、清理等通用操作。
"""

from __future__ import annotations

import gc
import logging
import os
import shutil
import stat
import time
from pathlib import Path
from typing import Callable, Iterable, List, Union

import pandas as pd

from invoice_toolkit.config import Settings

logger = logging.getLogger(__name__)


# =========================================================================
# 目录扫描
# =========================================================================

def build_flat_file_table(root_path: str | Path) -> pd.DataFrame:
    """递归扫描目录，构建文件清单表。"""
    root_path = str(root_path)
    records = [
        {"full_path": os.path.join(root, f), "name": f, "parent": os.path.relpath(root, root_path)}
        for root, _, files in os.walk(root_path)
        for f in files
    ]
    return pd.DataFrame(records)


# =========================================================================
# 文件移动
# =========================================================================

def move_files_to_categories(
    df: pd.DataFrame,
    target_root: str | Path,
    *,
    dry_run: bool = True,
) -> None:
    """将文件按分类移动到目标目录。"""
    target_root = str(target_root)

    for _, row in df.iterrows():
        dst_dir = os.path.join(target_root, row["category"])
        dst = os.path.join(dst_dir, row["name"])

        if os.path.exists(dst):
            base, ext = os.path.splitext(row["name"])
            person = row["parent"].split(os.sep)[0] if row["parent"] != "." else ""
            dst = os.path.join(dst_dir, f"{person}_{base}{ext}" if person else f"dup_{base}{ext}")

        if dry_run:
            logger.info("[预览] %s -> %s/", row["name"], row["category"])
        else:
            os.makedirs(dst_dir, exist_ok=True)
            shutil.copy2(row["full_path"], dst)
            logger.info("[完成] %s -> %s/", row["name"], row["category"])


# =========================================================================
# 输出
# =========================================================================

def save_dataframe(df: pd.DataFrame, output_path: str | Path) -> None:
    df.to_excel(str(output_path), index=False)
    logger.info("结果已保存: %s", output_path)


def print_classification_summary(df: pd.DataFrame) -> None:
    categories = Settings.CATEGORIES + ["未分类"]
    print("\n=== 分类统计 ===")
    print(df["category"].value_counts().to_string())
    print("\n=== 分类详情 ===")
    for cat in categories:
        subset = df[df["category"] == cat]
        if len(subset) > 0:
            print(f"\n【{cat}】({len(subset)} 个文件)")
            for _, row in subset.iterrows():
                print(f"  {row['name']}\t← {row['parent']}")


# =========================================================================
# DataFrame 辅助函数
# =========================================================================

def drop_columns(df: pd.DataFrame, labels: Union[str, Iterable[str]]) -> pd.DataFrame:
    if isinstance(labels, str):
        labels = [labels]
    existing = [col for col in labels if col in df.columns]
    return df.drop(columns=existing) if existing else df


def filter_by_column(
    df: pd.DataFrame,
    column: str,
    condition: Union[Callable, Iterable, int, float, str],
) -> pd.DataFrame:
    if column not in df.columns:
        return df
    series = df[column]
    if callable(condition):
        mask = condition(series)
    elif isinstance(condition, Iterable) and not isinstance(condition, (str, bytes)):
        mask = series.isin(condition)
    else:
        mask = series == condition
    return df[mask]


def get_column_values(df: pd.DataFrame, column: str, *, dropna: bool = True, unique: bool = True) -> List:
    if column not in df.columns:
        return []
    series = df[column].dropna() if dropna else df[column]
    return series.unique().tolist() if unique else series.tolist()


# =========================================================================
# 文件删除 & 清理
# =========================================================================

def _safe_delete_files(directory: Path, label: str, *, dry_run: bool, failed: list[Path]) -> int:
    """删除目录下所有文件，跳过被占用的文件。"""
    if not directory.exists():
        return 0

    gc.collect()
    count = 0
    action = "预览删除" if dry_run else "删除"

    for file in directory.rglob("*"):
        if not file.is_file():
            continue
        logger.info("[%s] %s: %s", action, label, file)
        if dry_run:
            count += 1
            continue
        for attempt in range(1, 4):
            try:
                os.chmod(file, stat.S_IWRITE)
                file.unlink()
                count += 1
                break
            except PermissionError:
                gc.collect()
                if attempt < 3:
                    time.sleep(attempt)
            except OSError as e:
                logger.warning("[跳过] 删除失败 (%s): %s", e, file)
                failed.append(file)
                break
        else:
            logger.warning("[跳过] 文件被占用: %s", file)
            failed.append(file)
    return count


def _rebuild_source_tree(source_root: Path, name_list: list[str], *, dry_run: bool) -> int:
    """删除 source_root 下的整个目录结构，并根据 name_list 重建空的成员文件夹。

    返回（计划）创建的成员文件夹数量。
    """
    action = "预览重建" if dry_run else "重建"

    if dry_run:
        logger.info(
            "[%s] 源目录树: %s  →  将按 %d 个成员重新创建子文件夹",
            action, source_root, len(name_list),
        )
        return len(name_list)

    # 1. 删除整棵源目录树（含所有子目录）
    if source_root.exists():
        try:
            shutil.rmtree(source_root)
            logger.info("[%s] 已删除源目录树: %s", action, source_root)
        except OSError as exc:
            logger.warning("删除源目录树失败: %s — 改为逐项清理", exc)
            # 兜底：自底向上删空目录
            for d in sorted(source_root.rglob("*"), reverse=True):
                if d.is_dir():
                    try:
                        d.rmdir()
                    except OSError:
                        pass

    # 2. 重建根目录 + 每个成员的子文件夹
    source_root.mkdir(parents=True, exist_ok=True)
    create_name_folders(source_root, name_list)
    logger.info("[%s] 已按 NAME_LIST 创建 %d 个成员文件夹于 %s", action, len(name_list), source_root)
    return len(name_list)


def clean_project_data(settings: Settings, *, dry_run: bool = False) -> dict:
    """清理项目生成的数据，恢复到初始状态。

    源目录 (data/课题组成员文件/) 会被整棵删除，然后根据 settings.NAME_LIST
    重新生成空的成员子文件夹。
    """
    gc.collect()
    paths = settings.paths
    failed: list[Path] = []
    stats: dict = {
        "source_files": 0,
        "output_files": 0,
        "cache_items": 0,
        "db_cleaned": False,
        "name_folders": 0,
        "failed": failed,
    }
    action = "预览删除" if dry_run else "删除"

    # ── 1. 源目录：先统计待删除文件数，再整棵重建 ─────────────
    stats["source_files"] = _safe_delete_files(
        paths.source_root, "源文件", dry_run=dry_run, failed=failed
    )
    stats["name_folders"] = _rebuild_source_tree(
        paths.source_root, list(settings.NAME_LIST), dry_run=dry_run
    )

    # ── 2. 数据库：先关闭再删文件，避免 SQLite 锁 ─────────────
    if not dry_run:
        try:
            from invoice_toolkit.database import get_invoice_db, get_record_db
            for db in (get_invoice_db(settings), get_record_db(settings)):
                db.delete_all()
                db.close()
            # 重置单例缓存
            from invoice_toolkit import database as _db_mod
            for attr in ("_invoice_db", "_record_db"):
                if hasattr(_db_mod, attr):
                    setattr(_db_mod, attr, None)
            stats["db_cleaned"] = True
            logger.info("[%s] 已清空数据库", action)
        except Exception as exc:
            logger.warning("清空数据库失败: %s", exc)
    else:
        logger.info("[%s] 数据库: invoices.db, records.db", action)

    # ── 3. 输出目录 ───────────────────────────────────────────
    stats["output_files"] = _safe_delete_files(
        paths.output_dir, "输出文件", dry_run=dry_run, failed=failed
    )

    # ── 4. 缓存目录 ───────────────────────────────────────────
    cache = paths.cache_dir
    if cache.exists():
        stats["cache_items"] = sum(1 for _ in cache.rglob("*"))
        logger.info("[%s] 缓存目录: %s (%d 个条目)", action, cache, stats["cache_items"])
        if not dry_run:
            try:
                shutil.rmtree(cache)
            except OSError:
                _safe_delete_files(cache, "缓存文件", dry_run=False, failed=failed)
                for d in sorted(cache.rglob("*"), reverse=True):
                    if d.is_dir():
                        try: d.rmdir()
                        except OSError: pass
            cache.mkdir(parents=True, exist_ok=True)

    return stats


def create_name_folders(base_dir: Path, name_list: list[str]) -> None:
    for name in name_list:
        (base_dir / name).mkdir(parents=True, exist_ok=True)
