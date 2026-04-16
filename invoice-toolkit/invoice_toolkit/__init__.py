"""
invoice-toolkit: 发票识别、分类与报销记录匹配工具包（LangChain 版）
"""

__version__ = "3.0.0"

from invoice_toolkit.config import Settings
from invoice_toolkit.database import (
    InvoiceDatabase,
    RecordDatabase,
    get_invoice_db,
    get_record_db,
)

__all__ = [
    "Settings",
    "InvoiceDatabase",
    "RecordDatabase",
    "get_invoice_db",
    "get_record_db",
    "__version__",
]
