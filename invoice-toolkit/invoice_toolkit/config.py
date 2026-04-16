"""
配置管理模块 — 通过环境变量或 .env 文件加载设置。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar, List


def _load_dotenv() -> None:
    """从 .env 文件加载环境变量（不覆盖已有值）。"""
    for base in (Path.cwd(), Path(__file__).resolve().parent.parent):
        env_file = base / ".env"
        if not env_file.is_file():
            continue
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().strip("'\"")
            if key and key not in os.environ:
                os.environ[key] = value
        return


_load_dotenv()


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default)


# ─── 默认课题组成员名单（仅作为环境变量缺失时的兜底）────────
_DEFAULT_NAME_LIST: List[str] = [
    "李四"
]

def _load_name_list() -> List[str]:
    """从环境变量 NAME_LIST 加载成员名单（英文逗号分隔），缺失时使用默认值。"""
    raw = _env("NAME_LIST", "")
    if not raw.strip():
        return list(_DEFAULT_NAME_LIST)
    # 同时兼容中文逗号、换行
    for sep in ("，", "\n", ";", "；"):
        raw = raw.replace(sep, ",")
    names = [n.strip() for n in raw.split(",") if n.strip()]
    return names or list(_DEFAULT_NAME_LIST)


@dataclass(frozen=True)
class LLMSettings:
    """大模型 API 连接配置"""
    api_key: str = ""
    base_url: str = "https://api.siliconflow.cn/v1"
    model_name: str = "deepseek-ai/DeepSeek-V3"
    temperature: float = 0.1
    max_retries: int = 3

    @classmethod
    def from_env(cls) -> LLMSettings:
        return cls(
            api_key=_env("SILICONFLOW_API_KEY"),
            base_url=_env("LLM_BASE_URL", cls.base_url),
            model_name=_env("LLM_MODEL_NAME", cls.model_name),
            temperature=float(_env("LLM_TEMPERATURE", str(cls.temperature))),
            max_retries=int(_env("LLM_MAX_RETRIES", str(cls.max_retries))),
        )


@dataclass(frozen=True)
class OCRSettings:
    """百度 OCR API 配置"""
    api_key: str = ""
    secret_key: str = ""
    token_url: str = "https://aip.baidubce.com/oauth/2.0/token"
    receipt_url: str = "https://aip.baidubce.com/rest/2.0/ocr/v1/receipt"
    vat_invoice_url: str = "https://aip.baidubce.com/rest/2.0/ocr/v1/vat_invoice"

    @classmethod
    def from_env(cls) -> OCRSettings:
        return cls(api_key=_env("BAIDU_OCR_API_KEY"), secret_key=_env("BAIDU_OCR_SECRET_KEY"))


@dataclass(frozen=True)
class RAGSettings:
    """RAG 向量检索问答配置"""
    embedding_model: str = "BAAI/bge-large-zh-v1.5"
    chunk_size: int = 500
    chunk_overlap: int = 100
    top_k: int = 4

    @classmethod
    def from_env(cls) -> RAGSettings:
        return cls(
            embedding_model=_env("RAG_EMBEDDING_MODEL", cls.embedding_model),
            chunk_size=int(_env("RAG_CHUNK_SIZE", str(cls.chunk_size))),
            chunk_overlap=int(_env("RAG_CHUNK_OVERLAP", str(cls.chunk_overlap))),
            top_k=int(_env("RAG_TOP_K", str(cls.top_k))),
        )


@dataclass(frozen=True)
class BrowserSettings:
    """浏览器自动化配置（独立于主 LLM）"""
    model_name: str = "Qwen/Qwen2.5-VL-72B-Instruct"
    headless: bool = False          # 默认有头，配合手动登录 + noVNC
    use_vision: bool = True         # 千问 VL 支持视觉
    max_steps: int = 30
    max_failures: int = 10
    timeout: int = 1000

    # LLM 调用限流（控制费用）
    llm_call_interval: float = 2.0  # 两次 LLM 调用之间最短间隔（秒）
    max_llm_calls: int = 30         # 单次任务最大 LLM 调用次数

    # 认证配置（密码不会发送到 LLM）
    sdu_username: str = ""
    sdu_password: str = ""
    cookie_file: str = "data/cookies.json"

    # 认证模式: manual（手动登录优先）| auto（自动登录优先）| cookie_only
    auth_mode: str = "manual"

    # 手动登录超时（秒）
    manual_login_timeout: int = 300

    @classmethod
    def from_env(cls) -> BrowserSettings:
        return cls(
            model_name=_env("BROWSER_MODEL_NAME", cls.model_name),
            headless=_env("BROWSER_HEADLESS", str(cls.headless)).lower() in ("true", "1", "yes"),
            use_vision=_env("BROWSER_USE_VISION", str(cls.use_vision)).lower() in ("true", "1", "yes"),
            max_steps=int(_env("BROWSER_MAX_STEPS", str(cls.max_steps))),
            max_failures=int(_env("BROWSER_MAX_FAILURES", str(cls.max_failures))),
            timeout=int(_env("BROWSER_TIMEOUT", str(cls.timeout))),
            llm_call_interval=float(_env("LLM_CALL_INTERVAL", str(cls.llm_call_interval))),
            max_llm_calls=int(_env("MAX_LLM_CALLS", str(cls.max_llm_calls))),
            sdu_username=_env("SDU_USERNAME"),
            sdu_password=_env("SDU_PASSWORD"),
            cookie_file=_env("COOKIE_FILE", "data/cookies.json"),
            auth_mode=_env("AUTH_MODE", "manual"),
            manual_login_timeout=int(_env("MANUAL_LOGIN_TIMEOUT", "300")),
        )


@dataclass
class PathSettings:
    """项目路径配置"""
    project_root: Path = field(default_factory=lambda: Path.cwd())

    # 一级目录
    @property
    def data_dir(self) -> Path: return self.project_root / "data"
    @property
    def output_dir(self) -> Path: return self.project_root / "output"
    @property
    def cache_dir(self) -> Path: return self.project_root / "cache"
    @property
    def model_dir(self) -> Path: return self.project_root / "model"

    # 数据目录
    @property
    def source_root(self) -> Path: return self.data_dir / "课题组成员文件"
    @property
    def hand_excel(self) -> Path: return self.data_dir / "明细.xlsx"

    # 缓存目录
    @property
    def mirror_root(self) -> Path: return self.cache_dir / "课题组成员文件-镜像"
    @property
    def image_root(self) -> Path: return self.cache_dir / "课题组成员文件-镜像-图片"
    @property
    def rag_index_dir(self) -> Path: return self.cache_dir / "rag_index"

    # 输出目录
    @property
    def invoice_root(self) -> Path: return self.output_dir / "发票"
    @property
    def ocr_excel(self) -> Path: return self.output_dir / "识别结果.xlsx"
    @property
    def classifier_excel(self) -> Path: return self.output_dir / "分类结果.xlsx"
    @property
    def matcher_invoice_excel(self) -> Path: return self.output_dir / "发票匹配结果.xlsx"
    @property
    def matcher_hand_excel(self) -> Path: return self.output_dir / "记录匹配结果.xlsx"
    @property
    def checker_excel(self) -> Path: return self.output_dir / "附件检查结果.xlsx"

    # 模板相关
    @property
    def overtime_meal_template(self) -> Path: return self.model_dir / "加班餐情况说明模版.doc"
    @property
    def overtime_meal_output_dir(self) -> Path: return self.output_dir / "发票" / "加班餐"

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.source_root, self.output_dir, self.cache_dir):
            d.mkdir(parents=True, exist_ok=True)


@dataclass
class Settings:
    """全局配置入口"""
    CATEGORIES: ClassVar[List[str]] = [
        "出差", "加班餐", "快递", "打印", "打车", "材料", "论文和专利",
    ]
    # 课题组成员名单：从环境变量 NAME_LIST 加载（英文逗号分隔）
    NAME_LIST: ClassVar[List[str]] = _load_name_list()

    batch_size: int = 20
    llm: LLMSettings = field(default_factory=LLMSettings)
    ocr: OCRSettings = field(default_factory=OCRSettings)
    rag: RAGSettings = field(default_factory=RAGSettings)
    browser: BrowserSettings = field(default_factory=BrowserSettings)
    paths: PathSettings = field(default_factory=PathSettings)

    @classmethod
    def from_env(cls, project_root: Path | None = None) -> Settings:
        # 每次 from_env 时刷新 NAME_LIST，便于运行期热更新
        cls.NAME_LIST = _load_name_list()
        root = project_root or Path(_env("PROJECT_ROOT", str(Path.cwd())))
        return cls(
            batch_size=int(_env("BATCH_SIZE", "20")),
            llm=LLMSettings.from_env(),
            ocr=OCRSettings.from_env(),
            rag=RAGSettings.from_env(),
            browser=BrowserSettings.from_env(),
            paths=PathSettings(project_root=root),
        )
