# Invoice Toolkit (发票识别、分类与报销记录匹配工具包)

基于 LangChain 的发票全流程自动化处理系统，覆盖 OCR 识别 → 智能匹配 → LLM 分类 → 文件归档 → 附件检查 → 文件名规范化。

## 架构

```
OCR (百度API) → 规则+LLM 匹配 → LLM 分类 → 文件移动 → 附件检查 → 文件名规范
     ↑                                                        ↑
  发票图片/PDF                                          模板自动生成(加班餐)
```

## 核心模块

| 模块 | 功能 |
|------|------|
| `ocr.py` | 百度 OCR API 识别增值税发票、出租车票等 |
| `matcher.py` | 规则精确匹配 + LLM 语义匹配，发票↔报销记录对应 |
| `classifier.py` | LLM 驱动的报销记录分类（出差/打车/加班餐/材料等） |
| `checker.py` | 附件完整性检查，支持自动生成加班餐情况说明 |
| `rag.py` | FAISS 向量检索 + LLM 问答，查询报销政策 |
| `agent.py` | LangGraph ReAct Agent，自然语言驱动全流程 |
| `browser_agent.py` | Playwright + LLM 浏览器自动化 |
| `mcp_server.py` | FastMCP Server，供 Claude Desktop 等客户端调用 |
| `database.py` | SQLite 持久化（替代 Excel） |

## 快速开始

```bash
# 安装依赖
pip install langchain langchain-openai langgraph faiss-cpu pandas openpyxl

# 配置 .env
SILICONFLOW_API_KEY=sk-xxx
BAIDU_OCR_API_KEY=xxx
BAIDU_OCR_SECRET_KEY=xxx

# 运行完整流程
python -m invoice_toolkit pipeline

# 启动 Agent 交互
python -m invoice_toolkit agent

# 启动 MCP Server
python -m invoice_toolkit.mcp_server
```

## 使用方式

- **CLI**: `python -m invoice_toolkit [match|classify|move|check|pipeline|agent|rag|clean]`
- **Agent 对话**: 自然语言指令，如"帮我扫描发票目录并执行完整流程"
- **MCP Server**: 集成到 Claude Desktop / Cursor 等 MCP 客户端
- **RAG 问答**: 查询山东大学经费报销政策

## 技术栈

LangChain + LangGraph | ChatOpenAI (SiliconFlow) | 百度 OCR | FAISS | SQLite | Playwright | FastMCP
