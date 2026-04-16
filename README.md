# Invoice System · 发票报销智能管理系统

<p align="center">
  <img src="https://img.shields.io/badge/python-3.10+-blue.svg" alt="Python">
  <img src="https://img.shields.io/badge/react-18+-61dafb.svg" alt="React">
  <img src="https://img.shields.io/badge/LangChain-0.3+-1c3c3c.svg" alt="LangChain">
  <img src="https://img.shields.io/badge/LangGraph-enabled-ff6b6b.svg" alt="LangGraph">
  <img src="https://img.shields.io/badge/MCP-Server-8b5cf6.svg" alt="MCP">
  <img src="https://img.shields.io/badge/license-MIT-green.svg" alt="License">
</p>

<p align="center">
  <b>一个基于 LLM Agent 的端到端发票识别、分类、匹配与报销管理工具</b><br>
  OCR · RAG · LangGraph Agent · MCP Server · 浏览器自动化 · React 前端
</p>

<p align="center">
  <a href="./README.md">中文</a> ·
  <a href="./README.en.md">English</a>
</p>

---

## ✨ 项目简介

**Invoice System** 是一个面向科研课题组 / 中小团队的发票报销全流程自动化工具。它把「收发票 → OCR 识别 → 与手工报销记录匹配 → 智能分类 → 按类归档 → 附件完整性检查 → 文件名规范化 → 政策问答」整条链路串起来,由一个 LangGraph ReAct Agent 统一编排,并通过 MCP (Model Context Protocol) Server 暴露给任何兼容 MCP 的客户端或自研前端。

本仓库采用 **monorepo** 结构,同时包含 Python 后端 (`invoice-toolkit/`) 和 React 前端 (`frontend/`),开箱即用。

## 🎯 核心特性

### 后端 `invoice-toolkit/` (Python)

- 🔍 **发票 OCR 识别** — 基于百度 OCR,支持增值税发票、电子票据、火车票、出租车票等多种类型
- 🎯 **智能匹配** — LLM 辅助的发票与 Excel 报销记录双向匹配,金额 / 日期 / 商品名多维度对齐
- 🏷️ **自动分类** — 基于物品简介、报销人、发票内容综合判断,支持出差 / 加班餐 / 快递 / 打印 / 打车 / 材料 / 论文和专利等类别
- ✅ **附件完整性检查** — 可配置的 Markdown 规则模板,驱动 LLM 检查每张发票的必需附件是否齐全,支持部分附件自动生成(如加班餐情况说明)
- 📏 **文件名规范化** — 自动识别并建议重命名为标准格式
- 💬 **报销政策 RAG 问答** — 基于 BGE 中文嵌入 + FAISS 向量检索,回答学校/机构报销政策问题
- 🤖 **LangGraph Agent** — `create_react_agent` 驱动,支持自然语言指令完成整条流水线
- 🌐 **浏览器自动化** — 基于 `browser-use`,可自动填报校内报销系统,支持手动登录 + Cookie 持久化
- 🔌 **MCP Server** — 标准 MCP 协议暴露所有能力,可被 Claude Desktop / Cursor / 自研前端直接调用

### 前端 `frontend/` (React)

- 📊 **仪表盘** — 文件总数、报销记录、待报销金额、成员概览
- 📁 **文件管理** — 成员目录树、拖拽上传、与后端实时同步
- 📋 **报销明细** — 在线表格编辑、按成员过滤
- 🤖 **智能填报** — 基于后端 Agent 的对话式报销助手
- ⚙️ **工具控制台** — 可视化调用所有 MCP 工具,带执行日志
- 👥 **成员管理 / 服务器配置** — 管理员专属视图

## 🏗️ 架构概览

```
┌──────────────────────────────────────────────────────────────────┐
│                    frontend/ · React + Vite                       │
│  LoginPage │ Dashboard │ FileManager │ TableEditor │ Agent Chat  │
└───────────────────────────┬──────────────────────────────────────┘
                            │ Streamable HTTP (MCP Protocol)
┌───────────────────────────▼──────────────────────────────────────┐
│              invoice-toolkit/ · MCP Server (FastMCP)              │
│   scan_invoice_directory · run_ocr · match · classify · move     │
│   check_attachments · check_filenames · query_policy · rebuild   │
└───────────────────────────┬──────────────────────────────────────┘
                            │
          ┌─────────────────┼─────────────────┬──────────────┐
          ▼                 ▼                 ▼              ▼
┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────┐
│ LangGraph    │  │ OCR Engine   │  │ RAG (FAISS+  │  │ Browser  │
│ ReAct Agent  │  │ (Baidu API)  │  │  BGE-zh)     │  │ Agent    │
└──────┬───────┘  └──────┬───────┘  └──────┬───────┘  └────┬─────┘
       │                 │                  │                │
       └─────────────────┴──────────────────┴────────────────┘
                            │
                 ┌──────────▼──────────┐
                 │  SQLite (双库)       │
                 │  invoices.db        │
                 │  records.db         │
                 └─────────────────────┘
```

## 📁 项目结构

```
invoice-system/                    ← 本仓库根目录
├── invoice-toolkit/               ← Python 后端
│   ├── invoice_toolkit/           ← 主包
│   │   ├── agent.py               ← LangGraph ReAct Agent
│   │   ├── agent_orchestrator.py  ← 工具编排
│   │   ├── browser_agent.py       ← 浏览器自动化
│   │   ├── browser_auth.py        ← SSO 登录
│   │   ├── checker.py             ← 附件 / 文件名检查
│   │   ├── classifier.py          ← 发票分类
│   │   ├── cli.py                 ← CLI 入口
│   │   ├── config.py              ← 配置管理
│   │   ├── database.py            ← SQLite 封装
│   │   ├── llm_client.py          ← LLM 客户端
│   │   ├── matcher.py             ← 发票匹配
│   │   ├── mcp_server.py          ← MCP Server
│   │   ├── ocr.py                 ← OCR 引擎
│   │   ├── prompt_loader.py
│   │   ├── rag.py                 ← RAG 问答
│   │   ├── rule_parser.py
│   │   └── tools.py               ← Agent 工具集
│   ├── skills/                    ← 规则与 Prompt 模板
│   │   └── attachment-checker/
│   │       └── templates/
│   │           ├── rules.md
│   │           └── prompts/
│   ├── data/                      ← 数据目录(不入库)
│   ├── output/                    ← 生成输出(不入库)
│   ├── cache/                     ← 缓存(不入库)
│   ├── model/                     ← 文档模板
│   └── requirements.txt
├── frontend/                      ← React 前端
│   ├── src/
│   │   └── components/
│   │       ├── Dashboard.jsx
│   │       ├── FileManager.jsx
│   │       ├── InvoiceApp.jsx
│   │       ├── LoginPage.jsx
│   │       ├── ReimbursementAgent.jsx
│   │       ├── ServerSettings.jsx
│   │       ├── Sidebar.jsx
│   │       ├── TableEditor.jsx
│   │       └── ToolsConsole.jsx
│   ├── package.json
│   └── .env.example
├── .env.example                   ← 后端环境变量模板
├── .gitignore
├── LICENSE                        ← MIT
├── README.md                      ← 你正在看的文件
├── README.en.md
└── CONTRIBUTING.md
```

## 🚀 快速开始

### 1. 克隆项目

```bash
git clone https://github.com/<your-username>/invoice-system.git
cd invoice-system
```

### 2. 后端安装

需要 Python **3.10+**,推荐使用 conda 或 venv 隔离环境。

```bash
cd invoice-toolkit

# 创建虚拟环境
conda create -n invoice python=3.10 -y
conda activate invoice

# 安装依赖
pip install -r requirements.txt

# 安装 Playwright 浏览器(可选,仅浏览器自动化需要)
playwright install chromium

cd ..  # 回到仓库根
```

### 3. 配置后端环境变量

**根目录**创建 `.env` (后端代码会自动从根目录或 `invoice-toolkit/` 加载):

```bash
cp .env.example .env
```

```dotenv
# ─── LLM (硅基流动 / 或任何 OpenAI 兼容 API) ───
SILICONFLOW_API_KEY=sk-xxxxxxxx
LLM_BASE_URL=https://api.siliconflow.cn/v1
LLM_MODEL_NAME=deepseek-ai/DeepSeek-V3

# ─── 百度 OCR ───
BAIDU_OCR_API_KEY=your_api_key
BAIDU_OCR_SECRET_KEY=your_secret_key

# ─── RAG ───
RAG_EMBEDDING_MODEL=BAAI/bge-large-zh-v1.5

# ─── 课题组成员名单(英文逗号分隔) ───
NAME_LIST=张三,李四,王五
```

> 💡 **API Key 获取:**
> - 硅基流动: <https://siliconflow.cn>
> - 百度 OCR: <https://ai.baidu.com/tech/ocr>

完整配置项见 [.env.example](./.env.example)。

### 4. 准备数据目录

```
invoice-toolkit/data/
├── 课题组成员文件/          # 每个成员一个子文件夹,放发票原件
│   ├── 张三/
│   ├── 李四/
│   └── 王五/
└── 明细.xlsx              # 手工填写的报销明细表
```

### 5. 启动后端

**CLI 交互模式:**

```bash
cd invoice-toolkit
python -m invoice_toolkit.cli
# 或执行完整流水线
python -m invoice_toolkit.cli pipeline
```

**MCP Server 模式(供前端或 Claude Desktop 连接):**

```bash
cd invoice-toolkit
python -m invoice_toolkit.mcp_server --transport http --port 8000
```

**与 Claude Desktop 集成:**

```json
{
  "mcpServers": {
    "invoice-toolkit": {
      "command": "python",
      "args": ["-m", "invoice_toolkit.mcp_server", "--transport", "stdio"],
      "cwd": "/path/to/invoice-system/invoice-toolkit"
    }
  }
}
```

### 6. 启动前端

```bash
cd frontend
cp .env.example .env       # 可选:自定义 MCP 地址、管理员密码
npm install
npm run dev
```

打开浏览器访问 <http://localhost:5173>,默认连接本地 MCP Server (`http://localhost:8000`)。

## 📖 使用方式

### 命令行

在 `invoice-toolkit/` 目录下执行:

| 命令 | 作用 |
|------|------|
| `python -m invoice_toolkit.cli match` | OCR 识别 + 发票与报销记录匹配 |
| `python -m invoice_toolkit.cli classify` | LLM 分类 |
| `python -m invoice_toolkit.cli move` | 按类别移动文件 |
| `python -m invoice_toolkit.cli check` | 附件完整性检查 |
| `python -m invoice_toolkit.cli check-names` | 文件名规范检查 |
| `python -m invoice_toolkit.cli pipeline` | 一键执行完整流程 |
| `python -m invoice_toolkit.cli agent` | 进入 Agent 对话模式 |
| `python -m invoice_toolkit.cli rag` | 报销政策问答 |
| `python -m invoice_toolkit.cli clean` | 清理所有生成数据 |

### Agent 对话示例

```
你: 帮我扫描发票目录并执行完整流程
Agent: 好的,我将依次执行 OCR、匹配、分类、移动、附件检查...

你: 出差报销需要提交哪些材料?
Agent: 根据山东大学差旅费报销管理办法,需要...
```

## 🛠️ 技术栈

**后端**
- Python 3.10+ · LangChain 0.3 · LangGraph · FastMCP
- 百度 OCR · BGE-zh 嵌入 · FAISS 向量库
- SQLite · pandas · openpyxl
- browser-use · Playwright

**前端**
- React 18 · Vite · MCP Streamable HTTP Client

## 🗺️ Roadmap

- [ ] 支持更多 OCR 引擎(阿里云、腾讯云)
- [ ] 支持更多 LLM 提供商(Claude、GPT-4、Gemini)
- [ ] Docker Compose 一键部署
- [ ] 多租户支持
- [ ] 移动端适配
- [ ] 国际化 (i18n)

## 🤝 贡献指南

欢迎任何形式的贡献!详细规范见 [CONTRIBUTING.md](./CONTRIBUTING.md)。

## ⚠️ 免责声明

- 本项目中涉及的「山东大学报销系统」相关代码仅作为浏览器自动化的示例实现,任何针对具体机构系统的使用需遵守该机构的相关规定
- 本项目不保证 OCR / LLM 结果的 100% 正确性,涉及财务的场景请务必人工复核
- 请妥善保管 API Key 和登录凭证,**切勿将 `.env` 或 cookies 文件提交到公共仓库**

## 📄 License

本项目基于 [MIT License](./LICENSE) 开源。

## 🙏 致谢

- [LangChain](https://github.com/langchain-ai/langchain) & [LangGraph](https://github.com/langchain-ai/langgraph)
- [FastMCP](https://github.com/jlowin/fastmcp)
- [browser-use](https://github.com/browser-use/browser-use)
- [百度智能云 OCR](https://ai.baidu.com/tech/ocr)
- [BAAI/bge-large-zh](https://huggingface.co/BAAI/bge-large-zh-v1.5)

---

<p align="center">
  如果这个项目对你有帮助,欢迎 ⭐️ Star!
</p>
