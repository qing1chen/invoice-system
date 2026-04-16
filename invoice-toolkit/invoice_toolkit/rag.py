"""
RAG 报销政策问答模块 — 混合检索 + Reranker 精排 + 增量索引 + 多轮对话上下文改写。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple

from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_community.document_loaders import Docx2txtLoader, TextLoader, PyPDFLoader
try:
    from langchain_text_splitters import RecursiveCharacterTextSplitter
except ImportError:
    from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_community.vectorstores import FAISS
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_core.documents import Document

from invoice_toolkit.config import Settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_QA_PROMPT = ChatPromptTemplate.from_messages([
    ("system", """\
你是山东大学经费报销政策咨询助手。根据检索到的政策文档片段回答问题。
规则：仅基于文档内容作答；无相关信息时明确告知；引用条款编号；关键数字务必准确；简洁明了。

检索到的相关政策内容：
{context}"""),
    ("human", "{question}"),
])

_REWRITE_PROMPT = ChatPromptTemplate.from_messages([
    ("system", """\
你是查询改写助手。根据对话历史将用户的后续问题改写为独立、完整的检索查询。
规则：
- 补全代词指代和省略的主语
- 保留关键实体和数字
- 输出仅包含改写后的查询，不要任何解释
- 如果问题已经完整独立，直接原样输出"""),
    ("human", """\
对话历史：
{history}

用户当前问题：{question}

改写后的独立查询："""),
])

_LOADERS = {
    ".docx": Docx2txtLoader,
    ".doc": Docx2txtLoader,
    ".txt": TextLoader,
    ".pdf": PyPDFLoader,
}

# ---------------------------------------------------------------------------
# BM25 稀疏检索器
# ---------------------------------------------------------------------------


class BM25Retriever:
    """基于 BM25 的关键词稀疏检索器，与 FAISS 向量检索互补。"""

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self._documents: List[Document] = []
        self._tokenized_corpus: List[List[str]] = []
        self._doc_len: List[int] = []
        self._avgdl: float = 0.0
        self._df: Dict[str, int] = {}   # document frequency
        self._n_docs: int = 0

    # -- tokenizer: 简单中文字符 + 英文单词分词 --
    @staticmethod
    def _tokenize(text: str) -> List[str]:
        # 中文按字切分 + 英文按单词切分，去除标点和空白
        tokens: List[str] = []
        for seg in re.findall(r'[\u4e00-\u9fff]|[a-zA-Z0-9]+', text.lower()):
            tokens.append(seg)
        return tokens

    def index(self, documents: List[Document]) -> None:
        """全量构建 BM25 索引。"""
        self._documents = list(documents)
        self._n_docs = len(documents)
        self._tokenized_corpus = [self._tokenize(d.page_content) for d in documents]
        self._doc_len = [len(tc) for tc in self._tokenized_corpus]
        self._avgdl = sum(self._doc_len) / max(self._n_docs, 1)

        self._df = {}
        for tc in self._tokenized_corpus:
            seen = set(tc)
            for token in seen:
                self._df[token] = self._df.get(token, 0) + 1

    def add_documents(self, documents: List[Document]) -> None:
        """增量添加文档到 BM25 索引。"""
        for doc in documents:
            tokens = self._tokenize(doc.page_content)
            self._documents.append(doc)
            self._tokenized_corpus.append(tokens)
            self._doc_len.append(len(tokens))
            seen = set(tokens)
            for token in seen:
                self._df[token] = self._df.get(token, 0) + 1

        self._n_docs = len(self._documents)
        self._avgdl = sum(self._doc_len) / max(self._n_docs, 1)

    def query(self, question: str, top_k: int = 10) -> List[Tuple[Document, float]]:
        """BM25 检索，返回 (document, score) 列表。"""
        if not self._documents:
            return []

        q_tokens = self._tokenize(question)
        import math
        scores = [0.0] * self._n_docs
        for token in q_tokens:
            if token not in self._df:
                continue
            df_t = self._df[token]
            idf = math.log((self._n_docs - df_t + 0.5) / (df_t + 0.5) + 1.0)
            for i, tc in enumerate(self._tokenized_corpus):
                tf = tc.count(token)
                dl = self._doc_len[i]
                numerator = tf * (self.k1 + 1)
                denominator = tf + self.k1 * (1 - self.b + self.b * dl / self._avgdl)
                scores[i] += idf * numerator / denominator

        ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)[:top_k]
        return [(self._documents[i], s) for i, s in ranked if s > 0]


# ---------------------------------------------------------------------------
# Reranker — 基于 LLM 的交叉注意力精排（可替换为 bge-reranker-v2 本地模型）
# ---------------------------------------------------------------------------


class LLMReranker:
    """
    Reranker 精排器。

    生产环境使用 bge-reranker-v2 等交叉编码器模型，此处提供 LLM-based
    相关性打分方案作为轻量替代，接口完全兼容，可一行切换为本地模型推理。
    """

    def __init__(self, llm: ChatOpenAI):
        self._llm = llm
        self._prompt = ChatPromptTemplate.from_messages([
            ("system", """\
你是一个文档相关性评估器。给定一个问题和一段文档片段，评估文档与问题的相关程度。
仅输出一个 0-10 的整数分数，不要任何解释。
10 = 完全相关且直接回答问题
0 = 完全不相关"""),
            ("human", "问题：{question}\n\n文档片段：{passage}\n\n相关性分数："),
        ])
        self._chain = self._prompt | self._llm | StrOutputParser()

    def rerank(
        self,
        question: str,
        doc_score_pairs: List[Tuple[Document, float]],
        top_k: int = 4,
    ) -> List[Tuple[Document, float]]:
        """对候选文档重新打分排序。"""
        if not doc_score_pairs:
            return []

        scored: List[Tuple[Document, float]] = []
        for doc, _original_score in doc_score_pairs:
            try:
                raw = self._chain.invoke({
                    "question": question,
                    "passage": doc.page_content[:500],
                })
                # 提取数字
                nums = re.findall(r'\d+', raw.strip())
                score = min(int(nums[0]), 10) if nums else 0
            except Exception:
                score = 0
            scored.append((doc, float(score)))

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _load_documents(doc_paths: List[Path]) -> List[Document]:
    all_docs: List[Document] = []
    for path in doc_paths:
        loader_cls = _LOADERS.get(path.suffix.lower())
        if not loader_cls:
            logger.warning("不支持的文档格式: %s", path)
            continue
        try:
            loader = (
                loader_cls(str(path), encoding="utf-8")
                if loader_cls == TextLoader
                else loader_cls(str(path))
            )
            docs = loader.load()
            for doc in docs:
                doc.metadata["source"] = path.name
            all_docs.extend(docs)
            logger.info("已加载: %s (%d 片段)", path.name, len(docs))
        except Exception as exc:
            logger.error("加载失败 %s: %s", path, exc)
    return all_docs


def _file_fingerprint(path: Path) -> str:
    """单文件指纹（名称 + mtime + size）。"""
    return f"{path.name}:{path.stat().st_mtime_ns}:{path.stat().st_size}"


def _docs_hash(paths: List[Path]) -> str:
    h = hashlib.md5()
    for p in sorted(paths):
        if p.exists():
            h.update(_file_fingerprint(p).encode())
    return h.hexdigest()


def _format_docs_with_tracing(docs: List[Document]) -> str:
    """格式化文档并附加 Chunk 溯源信息（来源文件 + chunk 序号 + 页码）。"""
    parts: List[str] = []
    for i, d in enumerate(docs, 1):
        source = d.metadata.get("source", "未知")
        page = d.metadata.get("page", None)
        chunk_id = d.metadata.get("chunk_id", "?")
        header = f"[片段{i} | 来源: {source} | chunk#{chunk_id}"
        if page is not None:
            header += f" | 页码: {page}"
        header += "]"
        parts.append(f"{header}\n{d.page_content}")
    return "\n\n---\n\n".join(parts)


# ---------------------------------------------------------------------------
# 对话历史
# ---------------------------------------------------------------------------


@dataclass
class ConversationTurn:
    question: str
    answer: str


@dataclass
class ConversationHistory:
    """多轮对话上下文管理。"""
    turns: List[ConversationTurn] = field(default_factory=list)
    max_turns: int = 5

    def add(self, question: str, answer: str) -> None:
        self.turns.append(ConversationTurn(question=question, answer=answer))
        if len(self.turns) > self.max_turns:
            self.turns = self.turns[-self.max_turns:]

    def format_history(self) -> str:
        if not self.turns:
            return "（无历史对话）"
        lines: List[str] = []
        for t in self.turns:
            lines.append(f"用户: {t.question}")
            lines.append(f"助手: {t.answer[:150]}...")
        return "\n".join(lines)

    def clear(self) -> None:
        self.turns.clear()


# ---------------------------------------------------------------------------
# 核心引擎
# ---------------------------------------------------------------------------


class ReimbursementQA:
    """
    报销政策 RAG 问答引擎

    特性:
    - 混合检索: FAISS 向量语义检索 + BM25 关键词稀疏检索双路召回
    - Reranker 精排: LLM-based 相关性重排序（接口兼容 bge-reranker-v2）
    - 增量索引: 支持热更新，新增文档无需全量重建
    - 多轮对话: 基于 LLM 的上下文感知查询改写
    - Chunk 溯源: 每个检索片段附带来源文件、chunk 编号、页码定位
    """

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        doc_paths: List[Path] | None = None,
        chunk_size: int = 500,
        chunk_overlap: int = 100,
        top_k: int = 4,
        bm25_candidates: int = 10,
        vector_candidates: int = 10,
        enable_reranker: bool = True,
        enable_context_rewrite: bool = True,
    ):
        self._settings = settings or Settings.from_env()
        self._chunk_size = chunk_size
        self._chunk_overlap = chunk_overlap
        self._top_k = top_k
        self._bm25_candidates = bm25_candidates
        self._vector_candidates = vector_candidates
        self._enable_reranker = enable_reranker
        self._enable_context_rewrite = enable_context_rewrite

        self._doc_paths = doc_paths or self._discover_docs()
        self._index_dir = self._settings.paths.cache_dir / "rag_index"
        self._hash_file = self._index_dir / "docs_hash.json"
        self._indexed_files_record = self._index_dir / "indexed_files.json"

        self._embeddings: OpenAIEmbeddings | None = None
        self._vectorstore: FAISS | None = None
        self._bm25: BM25Retriever = BM25Retriever()
        self._reranker: LLMReranker | None = None
        self._llm: ChatOpenAI | None = None
        self._conversation: ConversationHistory = ConversationHistory()

        # 已索引文件指纹 {filename: fingerprint}
        self._indexed_fingerprints: Dict[str, str] = {}
        # 全部 chunks 的引用（用于 BM25 同步）
        self._all_chunks: List[Document] = []

    def _discover_docs(self) -> List[Path]:
        model_dir = self._settings.paths.model_dir
        if not model_dir.exists():
            return []
        supported = {".docx", ".doc", ".txt", ".pdf"}
        docs = [
            f for f in model_dir.iterdir()
            if f.is_file()
            and f.suffix.lower() in supported
            and not f.name.startswith("~")
            and "模版" not in f.name
            and "模板" not in f.name
        ]
        logger.info("发现 %d 个政策文档", len(docs))
        return docs

    @property
    def embeddings(self) -> OpenAIEmbeddings:
        if self._embeddings is None:
            cfg = self._settings.llm
            self._embeddings = OpenAIEmbeddings(
                api_key=cfg.api_key,
                base_url=cfg.base_url,
                model=self._settings.rag.embedding_model,
            )
        return self._embeddings

    @property
    def llm(self) -> ChatOpenAI:
        if self._llm is None:
            cfg = self._settings.llm
            self._llm = ChatOpenAI(
                api_key=cfg.api_key,
                base_url=cfg.base_url,
                model=cfg.model_name,
                temperature=0.1,
                max_retries=cfg.max_retries,
            )
        return self._llm

    @property
    def reranker(self) -> LLMReranker:
        if self._reranker is None:
            self._reranker = LLMReranker(self.llm)
        return self._reranker

    # ------------------------------------------------------------------
    # 索引管理
    # ------------------------------------------------------------------

    def _load_indexed_fingerprints(self) -> Dict[str, str]:
        """加载已索引文件的指纹记录。"""
        if self._indexed_files_record.exists():
            try:
                return json.loads(self._indexed_files_record.read_text("utf-8"))
            except Exception:
                pass
        return {}

    def _save_indexed_fingerprints(self) -> None:
        self._indexed_files_record.write_text(
            json.dumps(self._indexed_fingerprints, ensure_ascii=False, indent=2),
            "utf-8",
        )

    def _needs_rebuild(self) -> bool:
        if not (self._index_dir / "index.faiss").exists():
            return True
        if not self._hash_file.exists():
            return True
        try:
            stored = json.loads(self._hash_file.read_text("utf-8"))["hash"]
            return stored != _docs_hash(self._doc_paths)
        except Exception:
            return True

    def _split_documents(self, raw_docs: List[Document]) -> List[Document]:
        """切分文档并为每个 chunk 分配唯一 chunk_id。"""
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=self._chunk_size,
            chunk_overlap=self._chunk_overlap,
            separators=["\n\n", "\n", "。", "；", "，", " ", ""],
        )
        chunks = splitter.split_documents(raw_docs)
        # 分配 chunk_id
        base = len(self._all_chunks)
        for i, chunk in enumerate(chunks):
            chunk.metadata["chunk_id"] = base + i
        return chunks

    def build_index(self, force: bool = False) -> int:
        """全量构建向量索引 + BM25 索引。"""
        if not force and not self._needs_rebuild():
            logger.info("向量索引缓存有效，从磁盘加载")
            self._vectorstore = FAISS.load_local(
                str(self._index_dir),
                self.embeddings,
                allow_dangerous_deserialization=True,
            )
            self._indexed_fingerprints = self._load_indexed_fingerprints()
            # 重建 BM25（BM25 不持久化，每次从 FAISS docstore 恢复）
            self._all_chunks = list(self._vectorstore.docstore._dict.values())
            self._bm25.index(self._all_chunks)
            return len(self._all_chunks)

        if not self._doc_paths:
            raise FileNotFoundError("未找到政策文档，请放入 model/ 目录。")

        raw_docs = _load_documents(self._doc_paths)
        if not raw_docs:
            raise ValueError("所有文档加载失败")

        self._all_chunks = []
        chunks = self._split_documents(raw_docs)
        self._all_chunks = chunks

        # FAISS 向量索引
        self._vectorstore = FAISS.from_documents(chunks, self.embeddings)
        # BM25 稀疏索引
        self._bm25.index(chunks)

        # 持久化
        self._index_dir.mkdir(parents=True, exist_ok=True)
        self._vectorstore.save_local(str(self._index_dir))
        self._hash_file.write_text(
            json.dumps({"hash": _docs_hash(self._doc_paths)}), "utf-8"
        )
        # 记录已索引文件指纹
        self._indexed_fingerprints = {
            p.name: _file_fingerprint(p) for p in self._doc_paths if p.exists()
        }
        self._save_indexed_fingerprints()

        logger.info("索引已构建: %d chunks (FAISS + BM25)", len(chunks))
        return len(chunks)

    def rebuild(self) -> int:
        """强制全量重建索引。"""
        return self.build_index(force=True)

    def add_documents(self, new_paths: List[Path]) -> int:
        """
        增量索引热更新 — 仅对新增/变更文档构建索引并合并，无需全量重建。

        Returns:
            新增的 chunk 数量
        """
        self._ensure_ready()

        # 过滤出真正需要索引的文件（新增或内容变更）
        to_index: List[Path] = []
        for p in new_paths:
            if not p.exists():
                logger.warning("文件不存在: %s", p)
                continue
            fp = _file_fingerprint(p)
            if self._indexed_fingerprints.get(p.name) != fp:
                to_index.append(p)
            else:
                logger.info("文件未变更，跳过: %s", p.name)

        if not to_index:
            logger.info("无需增量更新")
            return 0

        raw_docs = _load_documents(to_index)
        if not raw_docs:
            return 0

        new_chunks = self._split_documents(raw_docs)

        # 增量合并到 FAISS
        new_vs = FAISS.from_documents(new_chunks, self.embeddings)
        self._vectorstore.merge_from(new_vs)

        # 增量合并到 BM25
        self._bm25.add_documents(new_chunks)
        self._all_chunks.extend(new_chunks)

        # 更新文件指纹
        for p in to_index:
            self._indexed_fingerprints[p.name] = _file_fingerprint(p)
            if p not in self._doc_paths:
                self._doc_paths.append(p)

        # 持久化
        self._vectorstore.save_local(str(self._index_dir))
        self._hash_file.write_text(
            json.dumps({"hash": _docs_hash(self._doc_paths)}), "utf-8"
        )
        self._save_indexed_fingerprints()

        logger.info("增量索引完成: 新增 %d chunks，总计 %d chunks", len(new_chunks), len(self._all_chunks))
        return len(new_chunks)

    # ------------------------------------------------------------------
    # 多轮对话 — 上下文感知查询改写
    # ------------------------------------------------------------------

    def _rewrite_query(self, question: str) -> str:
        """根据对话历史改写当前问题为独立查询。"""
        if not self._enable_context_rewrite or not self._conversation.turns:
            return question
        try:
            rewritten = (
                _REWRITE_PROMPT | self.llm | StrOutputParser()
            ).invoke({
                "history": self._conversation.format_history(),
                "question": question,
            })
            rewritten = rewritten.strip()
            if rewritten:
                logger.debug("查询改写: '%s' -> '%s'", question, rewritten)
                return rewritten
        except Exception as exc:
            logger.warning("查询改写失败，使用原始问题: %s", exc)
        return question

    # ------------------------------------------------------------------
    # 混合检索 + Reranker 精排
    # ------------------------------------------------------------------

    def _hybrid_retrieve(self, query: str) -> List[Tuple[Document, float]]:
        """
        混合检索：FAISS 向量语义召回 + BM25 关键词召回，去重合并后
        经 Reranker 精排，返回 top_k 个最相关片段。
        """
        # 1) FAISS 向量检索
        vector_results = self._vectorstore.similarity_search_with_score(
            query, k=self._vector_candidates
        )
        # 2) BM25 关键词检索
        bm25_results = self._bm25.query(query, top_k=self._bm25_candidates)

        # 3) 去重合并（以 page_content hash 去重）
        seen_hashes: set = set()
        merged: List[Tuple[Document, float]] = []

        for doc, score in vector_results:
            h = hashlib.md5(doc.page_content.encode()).hexdigest()
            if h not in seen_hashes:
                seen_hashes.add(h)
                merged.append((doc, score))

        for doc, score in bm25_results:
            h = hashlib.md5(doc.page_content.encode()).hexdigest()
            if h not in seen_hashes:
                seen_hashes.add(h)
                merged.append((doc, score))

        # 4) Reranker 精排
        if self._enable_reranker and merged:
            merged = self.reranker.rerank(query, merged, top_k=self._top_k)
        else:
            merged = merged[: self._top_k]

        return merged

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    def _ensure_ready(self) -> None:
        if self._vectorstore is None:
            self.build_index()

    def query(self, question: str) -> str:
        """单轮查询（无对话上下文）。"""
        self._ensure_ready()

        # 混合检索
        doc_scores = self._hybrid_retrieve(question)
        if not doc_scores:
            return "未检索到相关政策文档，请尝试换个问法。"

        context = _format_docs_with_tracing([d for d, _ in doc_scores])
        try:
            answer = (
                _QA_PROMPT | self.llm | StrOutputParser()
            ).invoke({"context": context, "question": question})
            return answer
        except Exception as exc:
            raise RuntimeError(f"查询失败: {exc}") from exc

    def query_with_sources(self, question: str) -> Dict[str, Any]:
        """
        带溯源的查询，返回答案 + 检索片段详情（来源文件、chunk 编号、
        页码、相关性分数）。
        """
        self._ensure_ready()

        # 多轮对话上下文改写
        rewritten = self._rewrite_query(question)

        # 混合检索 + Reranker 精排
        doc_scores = self._hybrid_retrieve(rewritten)

        # Chunk 溯源定位
        sources = []
        for doc, score in doc_scores:
            sources.append({
                "content": doc.page_content[:200] + ("..." if len(doc.page_content) > 200 else ""),
                "source": doc.metadata.get("source", "未知"),
                "chunk_id": doc.metadata.get("chunk_id", "?"),
                "page": doc.metadata.get("page", None),
                "score": round(float(score), 4),
            })

        context = _format_docs_with_tracing([d for d, _ in doc_scores])
        answer = (
            _QA_PROMPT | self.llm | StrOutputParser()
        ).invoke({"context": context, "question": rewritten})

        # 记录对话历史
        self._conversation.add(question, answer)

        return {
            "answer": answer,
            "sources": sources,
            "rewritten_query": rewritten if rewritten != question else None,
        }

    def clear_conversation(self) -> None:
        """清空对话历史。"""
        self._conversation.clear()

    # ------------------------------------------------------------------
    # 交互式会话
    # ------------------------------------------------------------------

    def interactive_session(self) -> None:
        print(f"{'=' * 60}")
        print("  山东大学经费报销政策咨询助手")
        print("  (输入 'quit' 退出 | 'clear' 清空对话历史)")
        print(f"{'=' * 60}")
        self._ensure_ready()
        print(f"\n已加载政策文档，索引 {len(self._all_chunks)} 个片段。")
        print("支持多轮对话，示例: 出差报销需要哪些材料？\n")

        while True:
            try:
                q = input("你: ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n再见！")
                break
            if not q:
                continue
            if q.lower() in ("quit", "exit", "q", "退出"):
                print("再见！")
                break
            if q.lower() in ("clear", "清空"):
                self.clear_conversation()
                print("对话历史已清空。\n")
                continue

            try:
                result = self.query_with_sources(q)

                # 如果进行了查询改写，显示改写结果
                if result.get("rewritten_query"):
                    print(f"  🔄 改写查询: {result['rewritten_query']}")

                print(f"\n助手: {result['answer']}")

                # 溯源信息
                seen = set()
                for s in result["sources"]:
                    key = f"{s['source']}#chunk{s['chunk_id']}"
                    if key not in seen:
                        page_info = f" (p.{s['page']})" if s.get("page") is not None else ""
                        print(f"  📎 {s['source']} chunk#{s['chunk_id']}{page_info}  [相关性: {s['score']}]")
                        seen.add(key)
                print()
            except Exception as exc:
                print(f"\n查询失败: {exc}\n")

    # ------------------------------------------------------------------
    # 信息查询
    # ------------------------------------------------------------------

    def get_index_info(self) -> Dict[str, Any]:
        info = {
            "doc_paths": [str(p) for p in self._doc_paths],
            "doc_count": len(self._doc_paths),
            "index_dir": str(self._index_dir),
            "index_exists": (self._index_dir / "index.faiss").exists(),
            "chunk_size": self._chunk_size,
            "chunk_overlap": self._chunk_overlap,
            "top_k": self._top_k,
            "retrieval_mode": "hybrid (FAISS + BM25)",
            "reranker_enabled": self._enable_reranker,
            "context_rewrite_enabled": self._enable_context_rewrite,
            "conversation_turns": len(self._conversation.turns),
        }
        if self._vectorstore:
            info["chunk_count"] = len(self._vectorstore.docstore._dict)
        if self._indexed_fingerprints:
            info["indexed_files"] = list(self._indexed_fingerprints.keys())
        return info


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def build_reimbursement_qa(settings=None, **kwargs) -> ReimbursementQA:
    """创建 ReimbursementQA 实例的工厂函数。"""
    return ReimbursementQA(settings=settings, **kwargs)
