"""
大模型客户端封装（LangChain 版）

修复记录：
    - 新增 chat_json 的 JSON 修复逻辑：模型返回非法 JSON 时尝试自动修复
    - 新增重试机制：JSON 解析失败时用更低 temperature 重试一次
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict

from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import JsonOutputParser, StrOutputParser

from invoice_toolkit.config import LLMSettings

logger = logging.getLogger(__name__)


def _try_fix_json(raw: str) -> Dict[str, Any] | None:
    """
    尝试修复常见的 LLM JSON 格式错误：
    1. 去除 markdown 代码块标记 ```json ... ```
    2. 修复裸标识符（如 [action_navigate] → ["action_navigate"]）
    3. 修复单引号 → 双引号
    4. 去除尾部逗号
    """
    # 去掉 markdown 代码块
    text = re.sub(r"```(?:json)?\s*", "", raw).strip().rstrip("`")

    # 修复数组中的裸标识符：[action_navigate] → ["action_navigate"]
    # 匹配 [ 后跟不带引号的标识符 ]
    text = re.sub(
        r'\[\s*([a-zA-Z_][\w]*(?:\s*,\s*[a-zA-Z_][\w]*)*)\s*\]',
        lambda m: "[" + ", ".join(f'"{x.strip()}"' for x in m.group(1).split(",")) + "]",
        text,
    )

    # 单引号 → 双引号（简单场景）
    text = text.replace("'", '"')

    # 去除尾部逗号（如 {"a": 1,}）
    text = re.sub(r",\s*([}\]])", r"\1", text)

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


class LLMClient:
    """基于 LangChain 的大模型客户端"""

    def __init__(self, settings: LLMSettings | None = None) -> None:
        self._settings = settings or LLMSettings.from_env()
        self._llm: ChatOpenAI | None = None

    @property
    def llm(self) -> ChatOpenAI:
        if self._llm is None:
            if not self._settings.api_key:
                raise ValueError(
                    "LLM API key 未配置。\n"
                    "请在 .env 文件中设置 SILICONFLOW_API_KEY 或设置系统环境变量。"
                )
            self._llm = ChatOpenAI(
                api_key=self._settings.api_key,
                base_url=self._settings.base_url,
                model=self._settings.model_name,
                temperature=self._settings.temperature,
                max_retries=self._settings.max_retries,
            )
        return self._llm

    def chat(self, system_prompt: str, user_prompt: str, **kwargs) -> str:
        """发送聊天请求，返回纯文本响应。"""
        prompt = ChatPromptTemplate.from_messages([
            ("system", "{system_prompt}"),
            ("human", "{user_prompt}"),
        ])
        chain = prompt | self.llm | StrOutputParser()
        try:
            return chain.invoke({"system_prompt": system_prompt, "user_prompt": user_prompt})
        except Exception as exc:
            logger.error("LLM 调用失败: %s", exc)
            raise RuntimeError(f"LLM 调用失败: {exc}") from exc

    def chat_json(self, system_prompt: str, user_prompt: str, **kwargs) -> Dict[str, Any]:
        """
        调用 LLM 并解析返回的 JSON。

        修复逻辑：
        1. 先用 JsonOutputParser 正常解析
        2. 失败则取原始文本，用 _try_fix_json 尝试修复
        3. 仍然失败则用 temperature=0 重试一次
        """
        prompt = ChatPromptTemplate.from_messages([
            ("system", "{system_prompt}"),
            ("human", "{user_prompt}"),
        ])

        # ── 第一次尝试：标准 JSON 解析 ──
        try:
            chain = prompt | self.llm | JsonOutputParser()
            return chain.invoke({"system_prompt": system_prompt, "user_prompt": user_prompt})
        except Exception as first_err:
            logger.warning("JSON 标准解析失败，尝试修复: %s", first_err)

        # ── 第二次尝试：获取原始文本并手动修复 ──
        try:
            raw_chain = prompt | self.llm | StrOutputParser()
            raw_text = raw_chain.invoke({"system_prompt": system_prompt, "user_prompt": user_prompt})
            fixed = _try_fix_json(raw_text)
            if fixed is not None:
                logger.info("JSON 修复成功")
                return fixed
        except Exception as fix_err:
            logger.warning("JSON 修复也失败: %s", fix_err)

        # ── 第三次尝试：temperature=0 重新调用 ──
        try:
            logger.info("使用 temperature=0 重试 JSON 请求")
            strict_llm = self.llm.with_config({"temperature": 0.0})
            strict_prompt = system_prompt + "\n\n【重要】请只返回合法 JSON，不要包含任何其他文本、注释或 markdown 标记。"
            chain = prompt | strict_llm | JsonOutputParser()
            return chain.invoke({"system_prompt": strict_prompt, "user_prompt": user_prompt})
        except Exception as retry_err:
            logger.error("JSON 三次尝试均失败: %s", retry_err)
            raise ValueError(f"无法解析 LLM 返回的 JSON（已重试）: {retry_err}") from retry_err

    def build_chain(self, prompt_template: ChatPromptTemplate, *, output_json: bool = False):
        """构建 LangChain 可复用链。"""
        parser = JsonOutputParser() if output_json else StrOutputParser()
        return prompt_template | self.llm | parser

    # ─── Function Calling（供 Agent 编排器使用） ────────────

    def chat_with_tools(
        self,
        messages: list[Dict[str, Any]],
        tools: list[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """发送带工具定义的聊天请求，返回 LLM 的响应（可能包含 tool_calls）。

        使用 LangChain 的 bind_tools 机制，将 OpenAI 格式的 tools 定义
        绑定到 LLM，然后发送完整的消息历史。

        Args:
            messages: OpenAI 格式的消息列表，支持 role: system/user/assistant/tool
            tools: OpenAI 格式的工具定义列表，每个元素:
                {
                    "type": "function",
                    "function": {
                        "name": "...",
                        "description": "...",
                        "parameters": { JSON Schema }
                    }
                }

        Returns:
            dict: {
                "content": str | None,         # 文本回复（无工具调用时）
                "tool_calls": [                 # 工具调用列表（可能为空）
                    {
                        "id": "call_xxx",
                        "function": {
                            "name": "tool_name",
                            "arguments": '{"key": "value"}'  # JSON 字符串
                        }
                    }
                ]
            }
        """
        from langchain_core.messages import (
            SystemMessage, HumanMessage, AIMessage, ToolMessage
        )

        # ── 1. 转换 OpenAI 格式消息 → LangChain 消息对象 ──
        lc_messages = []
        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "") or ""

            if role == "system":
                lc_messages.append(SystemMessage(content=content))

            elif role == "user":
                lc_messages.append(HumanMessage(content=content))

            elif role == "assistant":
                # assistant 消息可能带 tool_calls
                tc = msg.get("tool_calls")
                if tc:
                    # 构建带 tool_calls 的 AIMessage
                    lc_tool_calls = []
                    for call in tc:
                        func = call.get("function", {})
                        args_str = func.get("arguments", "{}")
                        try:
                            args = json.loads(args_str) if isinstance(args_str, str) else args_str
                        except json.JSONDecodeError:
                            args = {}
                        lc_tool_calls.append({
                            "id": call.get("id", ""),
                            "name": func.get("name", ""),
                            "args": args,
                        })
                    lc_messages.append(AIMessage(
                        content=content,
                        tool_calls=lc_tool_calls,
                    ))
                else:
                    lc_messages.append(AIMessage(content=content))

            elif role == "tool":
                lc_messages.append(ToolMessage(
                    content=content,
                    tool_call_id=msg.get("tool_call_id", ""),
                ))

        # ── 2. 转换 OpenAI 格式工具定义 → LangChain 格式 ──
        lc_tools = []
        for tool in tools:
            func = tool.get("function", tool)
            lc_tools.append({
                "type": "function",
                "function": {
                    "name": func["name"],
                    "description": func.get("description", ""),
                    "parameters": func.get("parameters", {"type": "object", "properties": {}}),
                },
            })

        # ── 3. 绑定工具并调用 ──
        llm_with_tools = self.llm.bind_tools(lc_tools)

        try:
            response = llm_with_tools.invoke(lc_messages)
        except Exception as exc:
            logger.error("LLM chat_with_tools 调用失败: %s", exc)
            raise RuntimeError(f"LLM chat_with_tools 调用失败: {exc}") from exc

        # ── 4. 将 LangChain AIMessage → OpenAI 格式返回 ──
        result: Dict[str, Any] = {
            "content": response.content if response.content else None,
            "tool_calls": [],
        }

        if response.tool_calls:
            for tc in response.tool_calls:
                result["tool_calls"].append({
                    "id": tc.get("id", ""),
                    "function": {
                        "name": tc["name"],
                        "arguments": json.dumps(tc["args"], ensure_ascii=False),
                    },
                })

        return result
