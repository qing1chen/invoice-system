"""
发票 OCR 识别模块

使用百度 OCR API 识别增值税发票、出租车票、铁路电子客票等。
支持 PDF 和常见图片格式，自动缓存已识别结果。

数据持久化：
    - 原版：保存到 output/识别结果.xlsx
    - 新版：写入发票数据库 (invoices.db) 的 invoices 表
"""

from __future__ import annotations

import ast
import base64
import io
import logging
import os
import re
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
import requests
from PIL import Image

from invoice_toolkit.config import OCRSettings, Settings
from invoice_toolkit.database import InvoiceDatabase, get_invoice_db

logger = logging.getLogger(__name__)

_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".gif"}
_AMOUNT_TOLERANCE = 0.001


class BaiduOCRClient:
    """百度 OCR API 客户端"""

    def __init__(self, settings: OCRSettings | None = None) -> None:
        self._settings = settings or OCRSettings.from_env()
        self._token: str | None = None

    def _ensure_credentials(self) -> None:
        if not self._settings.api_key or not self._settings.secret_key:
            raise ValueError(
                "百度 OCR API 凭据未配置。\n"
                "请在项目根目录的 .env 文件中设置：\n"
                "  BAIDU_OCR_API_KEY=你的AppID\n"
                "  BAIDU_OCR_SECRET_KEY=你的SecretKey\n"
                "或设置对应的系统环境变量。"
            )

    @property
    def token(self) -> str:
        if self._token is None:
            self._token = self._fetch_token()
        return self._token

    def recognize(self, api_url: str, image_path: str | Path) -> dict:
        with open(image_path, "rb") as f:
            image_b64 = base64.b64encode(f.read()).decode("utf-8")

        url = f"{api_url}?access_token={self.token}"
        resp = requests.post(
            url,
            data={"image": image_b64},
            headers={"User-Agent": "invoice-toolkit"},
            timeout=60,
        )
        resp.raise_for_status()
        return resp.json()

    def _fetch_token(self) -> str:
        self._ensure_credentials()
        params = {
            "grant_type": "client_credentials",
            "client_id": self._settings.api_key,
            "client_secret": self._settings.secret_key,
        }
        resp = requests.get(self._settings.token_url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if "access_token" not in data:
            raise RuntimeError(f"获取 OCR token 失败: {data}")
        return data["access_token"]


def _parse_vat_invoice(words_result: dict) -> dict:
    commodity_prices = [
        item.get("word", "")
        for item in (words_result.get("CommodityPrice") or [])
    ]
    commodity_names = [
        item.get("word", "")
        for item in (words_result.get("CommodityName") or [])
    ]
    return {
        "购方名称": words_result.get("PurchaserName", ""),
        "购方税号": words_result.get("PurchaserRegisterNum", ""),
        "价税合计": words_result.get("AmountInFiguers", ""),
        "商品单价": commodity_prices,
        "商品名称": commodity_names,
        "销售方名称": words_result.get("SellerName", ""),
        "发票类型": words_result.get("InvoiceType", ""),
        "发票号码": words_result.get("InvoiceNum", ""),
        "发票代码": words_result.get("InvoiceCode", ""),
        "开票日期": words_result.get("InvoiceDate", ""),
        "税额": words_result.get("TotalTax", ""),
        "校验码": words_result.get("CheckCode", ""),
    }


def _parse_special_receipt(words_result_list: list[dict]) -> dict:
    result: dict[str, str] = {}
    is_electronic_ticket = False
    is_non_tax_receipt = False

    for item in words_result_list:
        cleaned = item["words"].replace(")", "").replace("(", "")
        if "电子发票铁路电子客票" in cleaned:
            is_electronic_ticket = True
        if "中央非税" in cleaned or "非税收入票据" in cleaned:
            is_non_tax_receipt = True

        if is_electronic_ticket:
            if "购买方名称" in item["words"]:
                result["PurchaserName"] = item["words"].split(":")[-1].strip()
            if "统一社会信用代码" in item["words"]:
                result["PurchaserRegisterNum"] = item["words"].split(":")[-1].strip()
            if any(kw in item["words"] for kw in ("票价", "退票费", "改签费")):
                result["AmountInFiguers"] = (
                    item["words"].split(":")[-1].strip().replace("￥", "")
                )

        if is_non_tax_receipt:
            if "交款人统一社会信用代码" in item["words"]:
                result["PurchaserRegisterNum"] = item["words"].split(":")[-1].strip()
            elif "交款人" in item["words"] and "统一社会信用代码" not in item["words"]:
                name = item["words"].replace("交款人", "").strip().lstrip(":").strip()
                if name:
                    result["PurchaserName"] = name
            if "(小写)" in item["words"] or "（小写）" in item["words"]:
                amount = item["words"].split(")")[-1].strip()
                result["AmountInFiguers"] = amount.replace("￥", "").replace("¥", "")

    if is_non_tax_receipt and "PurchaserName" not in result:
        for i, item in enumerate(words_result_list):
            if item["words"] == "交款人":
                for next_item in words_result_list[i + 1 :]:
                    if abs(next_item["location"]["top"] - item["location"]["top"]) < 20:
                        if next_item["words"] not in ("交款人", "开票日期") and ":" not in next_item["words"]:
                            result["PurchaserName"] = next_item["words"].strip()
                            break
                break

    return result


class InvoiceOCRProcessor:
    """发票 OCR 处理器（数据库版）"""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or Settings.from_env()
        self._paths = self._settings.paths
        self._ocr = BaiduOCRClient(self._settings.ocr)

        # 获取发票数据库
        self._invoice_db: InvoiceDatabase = get_invoice_db(self._settings)

        self.result: List[Dict[str, Any]] = []
        self.hand_data: List[Dict[str, Any]] = []
        self.unrecognized_files: List[Dict[str, str]] = []

        if self._paths.hand_excel.exists():
            df = pd.read_excel(str(self._paths.hand_excel))
            self.hand_data = df.to_dict("records")

        self._process_all_invoices()
        self._check_unrecognized_files()

    def _process_all_invoices(self) -> None:
        self.result = []

        mirror_exists = self._paths.mirror_root.exists() and self._paths.image_root.exists()
        db_has_data = self._invoice_db.count("invoices") > 0

        if mirror_exists and db_has_data:
            self._load_from_db()
            return

        if not mirror_exists:
            logger.info("开始完整 OCR 处理流程")
            self._paths.mirror_root.mkdir(parents=True, exist_ok=True)
            self._paths.image_root.mkdir(parents=True, exist_ok=True)

            for category_dir in self._paths.source_root.iterdir():
                if category_dir.is_dir():
                    logger.info("正在处理类别: %s", category_dir.name)
                    self._process_category(
                        source_path=category_dir,
                        mirror_path=self._paths.mirror_root / category_dir.name,
                        images_path=self._paths.image_root / category_dir.name,
                        save_to_db=True,
                    )

            if self._invoice_db.count("invoices") > 0:
                self._load_from_db()
        else:
            logger.warning("目录状态不一致，请检查镜像目录和图片目录")

    def _load_from_db(self) -> None:
        """从数据库加载 OCR 结果"""
        try:
            df = self._invoice_db.get_ocr_dataframe()
            self.result = df.to_dict("records") if not df.empty else []
            for item in self.result:
                item.pop("匹配项", None)
            logger.info("从数据库加载了 %d 条记录", len(self.result))
        except Exception as exc:
            logger.error("读取 OCR 结果失败: %s", exc)

    def _process_category(
        self,
        source_path: Path,
        mirror_path: Path,
        images_path: Path,
        save_to_db: bool = True,
    ) -> List[Dict[str, Any]]:
        mirror_path.mkdir(parents=True, exist_ok=True)
        images_path.mkdir(parents=True, exist_ok=True)

        results: List[Dict[str, Any]] = []
        relative_path = str(source_path.relative_to(self._paths.source_root))
        person_name = Path(relative_path).parts[0]

        for entry in source_path.iterdir():
            if entry.is_dir():
                sub_results = self._process_category(
                    entry,
                    mirror_path / entry.name,
                    images_path / entry.name,
                    save_to_db=False,
                )
                results.extend(sub_results)
                continue

            ext = entry.suffix.lower()
            if ext not in _IMAGE_EXTENSIONS and ext != ".pdf":
                continue

            images = self._load_images(entry)
            if not images:
                continue

            for page_idx, image in enumerate(images):
                img_name = (
                    f"{entry.stem}_p{page_idx + 1}.jpg"
                    if len(images) > 1
                    else f"{entry.stem}.jpg"
                )
                img_path = images_path / img_name
                try:
                    image.save(str(img_path), format="JPEG", quality=95)
                except Exception as exc:
                    logger.warning("保存图片失败 %s: %s", img_name, exc)
                    continue
                finally:
                    image.close()

                record = self._ocr_single_image(str(img_path), relative_path, person_name, entry.name)
                if record:
                    results.append(record)

        if save_to_db and results:
            self._save_to_db(results)

        return results

    def _load_images(self, file_path: Path) -> List[Image.Image]:
        ext = file_path.suffix.lower()

        if ext == ".pdf":
            try:
                import fitz

                doc = None
                try:
                    doc = fitz.open(str(file_path))
                    images = []
                    for page in doc:
                        pix = page.get_pixmap(matrix=fitz.Matrix(2.0, 2.0))
                        img = Image.open(io.BytesIO(pix.tobytes("png")))
                        img_copy = img.copy()
                        img.close()
                        images.append(img_copy)
                    return images
                finally:
                    if doc is not None:
                        doc.close()
            except ImportError:
                logger.error("需要安装 PyMuPDF: pip install PyMuPDF")
                return []
            except Exception as exc:
                logger.warning("PDF 转图片失败 %s: %s", file_path.name, exc)
                return []
        else:
            try:
                with Image.open(str(file_path)) as img:
                    img_copy = img.copy()
                return [img_copy]
            except Exception as exc:
                logger.warning("打开图片失败 %s: %s", file_path.name, exc)
                return []

    def _ocr_single_image(
        self,
        image_path: str,
        relative_path: str,
        person_name: str,
        filename: str,
    ) -> Optional[Dict[str, Any]]:
        settings = self._settings.ocr

        raw_result = self._ocr.recognize(settings.vat_invoice_url, image_path)
        words_result = raw_result.get("words_result", {})

        if not words_result:
            raw_result = self._ocr.recognize(settings.receipt_url, image_path)
            words_list = raw_result.get("words_result", [])
            if words_list:
                words_result = _parse_special_receipt(words_list)

        if not words_result:
            logger.warning("OCR 未返回有效结果: %s", filename)
            return None

        parsed = _parse_vat_invoice(words_result)
        return {
            "相对路径": relative_path,
            "姓名/公司": person_name,
            "旧文件名": filename,
            "新文件名": "",
            **parsed,
        }

    def _save_to_db(self, records: List[Dict[str, Any]]) -> None:
        """将 OCR 结果写入发票数据库"""
        self._invoice_db.upsert_ocr_results(records)
        logger.info("已保存 %d 条 OCR 结果到数据库", len(records))

    def _check_unrecognized_files(self) -> None:
        self.unrecognized_files = []
        recognized_names = {item.get("旧文件名") for item in self.result if item.get("旧文件名")}

        for root, _dirs, files in os.walk(str(self._paths.source_root)):
            rel_path = os.path.relpath(root, str(self._paths.source_root))
            for filename in files:
                if filename not in recognized_names:
                    self.unrecognized_files.append(
                        {"相对路径": rel_path, "旧文件名": filename, "新文件名": ""}
                    )

        logger.info("发现 %d 个未识别的文件", len(self.unrecognized_files))

    def run_all_checks(self) -> None:
        """执行所有校验并记录异常标记"""
        self._anomalies: Dict[int, List[str]] = {}

        self._check_field("购方名称", lambda v: v != "山东大学")
        self._check_field("购方税号", lambda v: v != "12100000495570303U")
        # self._check_field("价税合计", lambda v: _safe_float(v) >= 20000)
        # self._check_commodity_prices()

        # 将校验结果写回数据库
        self._save_check_results()

    def _check_field(self, field: str, is_error: callable) -> None:
        for idx, item in enumerate(self.result):
            value = item.get(field, "")
            try:
                if is_error(value):
                    self._anomalies.setdefault(idx, []).append(field)
            except (ValueError, TypeError):
                self._anomalies.setdefault(idx, []).append(field)

    def _check_commodity_prices(self) -> None:
        for idx, item in enumerate(self.result):
            prices = item.get("商品单价", [])
            if isinstance(prices, str):
                try:
                    prices = ast.literal_eval(prices)
                except (SyntaxError, ValueError):
                    self._anomalies.setdefault(idx, []).append("商品单价")
                    continue

            for price in (prices or []):
                if _safe_float(price) >= 1000:
                    self._anomalies.setdefault(idx, []).append("商品单价")
                    break

    def _save_check_results(self) -> None:
        """将 OCR 结果（含异常标记）保存到数据库"""
        records_to_update = []
        for item in self.result:
            copy = dict(item)
            if "匹配项" in copy:
                copy["匹配项"] = str(copy["匹配项"]) if copy["匹配项"] else ""
            records_to_update.append(copy)

        self._invoice_db.upsert_ocr_results(records_to_update)

        # 将校验异常写入异常标记
        anomaly_reason_map = {
            "购方名称": "购方名称异常（非山东大学）",
            "购方税号": "购方税号异常（非山东大学税号）",
            "价税合计": "价税合计≥20000元",
            "商品单价": "商品单价≥1000元",
        }
        for idx, fields in self._anomalies.items():
            if idx >= len(self.result):
                continue
            filename = self.result[idx].get("旧文件名", "")
            if not filename:
                continue
            reasons = [anomaly_reason_map.get(f, f) for f in fields]
            reason_str = "; ".join(reasons)
            self._invoice_db.append_anomaly_reason(filename, reason_str)

        if self._anomalies:
            logger.info(
                "OCR 校验: %d 张发票存在异常，已写入异常标记",
                len(self._anomalies),
            )

        logger.info("OCR 结果已保存到数据库，共 %d 条记录", len(self.result))


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except (ValueError, TypeError):
        return 0.0