"""
LangChain Agent 编排模块（LangGraph 版）

修复记录：
    - langchain.agents.create_agent 不存在，改用 langgraph.prebuilt.create_react_agent
    - create_react_agent 返回的是 CompiledGraph，invoke 签名与原 create_agent 不同
    - 需要安装: pip install langgraph
"""

from __future__ import annotations

import logging
from typing import Optional

from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent          # ← 修复：正确的导入

from invoice_toolkit.config import Settings
from invoice_toolkit.tools import ALL_TOOLS, set_settings

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
你是发票处理助手，负责帮助用户完成发票识别、分类、匹配和检查工作。
你还能回答山东大学经费报销政策相关问题。

可用工具：
1. scan_invoice_directory — 扫描发票目录
2. run_ocr_recognition — OCR 识别
3. run_invoice_matching — 发票与报销记录匹配（含 OCR）
4. run_invoice_classification — 发票分类
5. run_file_move — 按分类移动文件
6. check_attachments — 附件完整性检查
7. clean_project_data — 清理数据
8. check_invoice_filenames — 文件名规范检查
9. query_reimbursement_policy — 报销政策问答（RAG）
10. rebuild_rag_index — 重建向量索引

标准流程：OCR → 匹配 → 分类 → 移动 → 附件检查 → 文件名检查
每步完成后汇报结果；涉及文件操作先预览再确认。

【重要】你的响应必须是严格合法的 JSON。所有字符串值必须用双引号，禁止使用裸标识符。"""


class InvoiceAgent:
    """发票处理 Agent"""

    def __init__(self, settings: Settings | None = None, *, verbose: bool = False) -> None:
        self._settings = settings or Settings.from_env()
        self._settings.paths.ensure_dirs()
        set_settings(self._settings)

        cfg = self._settings.llm
        if not cfg.api_key:
            raise ValueError("LLM API key 未配置，请设置 SILICONFLOW_API_KEY")

        self._llm = ChatOpenAI(
            api_key=cfg.api_key,
            base_url=cfg.base_url,
            model=cfg.model_name,
            temperature=cfg.temperature,
            max_retries=cfg.max_retries,
        )

        # ── 修复：使用 langgraph 的 create_react_agent ──
        # 参数签名：create_react_agent(model, tools, *, prompt=None, ...)
        # 返回 CompiledGraph，invoke 输入为 {"messages": [...]}
        self._agent = create_react_agent(
            model=self._llm,
            tools=ALL_TOOLS,
            prompt=_SYSTEM_PROMPT,             # ← 修复：system_prompt → prompt
        )

    def run(self, instruction: str) -> str:
        try:
            result = self._agent.invoke(
                {"messages": [("user", instruction)]}   # ← langgraph 接受 tuple 格式
            )
            return self._extract_response(result)
        except Exception as exc:
            logger.error("Agent 执行失败: %s", exc)
            return f"执行失败: {exc}"

    def chat(self, message: str, chat_history: list | None = None) -> tuple[str, list]:
        history = chat_history or []
        messages = list(history) + [("user", message)]
        try:
            result = self._agent.invoke({"messages": messages})
            response = self._extract_response(result)
            updated = result.get("messages", messages)
            return response, list(updated) if not isinstance(updated, list) else updated
        except Exception as exc:
            logger.error("Agent 对话失败: %s", exc)
            response = f"处理失败: {exc}"
            return response, list(history) + [
                ("user", message),
                ("assistant", response),
            ]

    def interactive_session(self) -> None:
        print(f"{'=' * 60}\n  发票处理 Agent (输入 'quit' 退出)\n{'=' * 60}\n")
        print("示例: 帮我扫描发票目录 / 执行完整流程 / 对发票进行分类\n")
        history: list = []
        while True:
            try:
                user_input = input("你: ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n再见！")
                break
            if not user_input:
                continue
            if user_input.lower() in ("quit", "exit", "q", "退出"):
                print("再见！")
                break
            response, history = self.chat(user_input, history)
            print(f"\nAgent: {response}\n")

    @staticmethod
    def _extract_response(result: dict) -> str:
        messages = result.get("messages", [])
        if not messages:
            return "Agent 未返回结果"
        last = messages[-1]
        # langgraph 返回的是 BaseMessage 对象
        if hasattr(last, "content"):
            content = last.content
            # content 可能是 str 或 list[dict]（含 tool_call 时）
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                # 拼接所有 text 类型的块
                parts = [c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"]
                return "\n".join(parts) if parts else str(content)
            return str(content)
        return last.get("content", str(last)) if isinstance(last, dict) else str(last)


def build_invoice_agent(settings=None, *, verbose=False) -> InvoiceAgent:
    return InvoiceAgent(settings=settings, verbose=verbose)
