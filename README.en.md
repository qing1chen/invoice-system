# Invoice System

<p align="center">
  <img src="https://img.shields.io/badge/python-3.10+-blue.svg" alt="Python">
  <img src="https://img.shields.io/badge/react-18+-61dafb.svg" alt="React">
  <img src="https://img.shields.io/badge/LangChain-0.3+-1c3c3c.svg" alt="LangChain">
  <img src="https://img.shields.io/badge/LangGraph-enabled-ff6b6b.svg" alt="LangGraph">
  <img src="https://img.shields.io/badge/MCP-Server-8b5cf6.svg" alt="MCP">
  <img src="https://img.shields.io/badge/license-MIT-green.svg" alt="License">
</p>

<p align="center">
  <b>An end-to-end LLM-powered toolkit for invoice OCR, classification, matching, and reimbursement management</b><br>
  OCR · RAG · LangGraph Agent · MCP Server · Browser Automation · React UI
</p>

<p align="center">
  <a href="./README.md">中文</a> ·
  <a href="./README.en.md">English</a>
</p>

---

## ✨ Overview

**Invoice System** is a full-pipeline automation tool designed for research groups and small teams. It stitches together the entire reimbursement workflow — *receive invoice → OCR → match against handwritten records → auto-classify → sort into folders → check attachment completeness → normalize filenames → answer policy questions* — all orchestrated by a **LangGraph ReAct Agent** and exposed through a standard **MCP (Model Context Protocol) Server**, so it can be consumed by Claude Desktop, Cursor, or any custom front-end.

This is a **monorepo** containing both the Python backend (`invoice-toolkit/`) and the React frontend (`frontend/`).

## 🎯 Features

### Backend `invoice-toolkit/` (Python)

- 🔍 **Invoice OCR** — OCR integration supporting VAT invoices, e-receipts, train tickets, taxi receipts, and more
- 🎯 **Smart Matching** — LLM-assisted bidirectional matching between OCR'd invoices and Excel reimbursement records
- 🏷️ **Auto Classification** — Multi-signal classification across configurable categories: travel, overtime meals, delivery, printing, transit, materials, papers & patents
- ✅ **Attachment Completeness Check** — Markdown-based rule templates drive an LLM checker that validates each invoice's required attachments; supports auto-generation of missing documents
- 📏 **Filename Normalization** — Detect and suggest standardized filenames
- 💬 **Policy RAG Q&A** — FAISS + BGE-Chinese embeddings for answering institutional reimbursement policy questions
- 🤖 **LangGraph Agent** — Powered by `create_react_agent`, accepts natural-language instructions to run the whole pipeline
- 🌐 **Browser Automation** — `browser-use`-based agent that can auto-fill institutional reimbursement systems
- 🔌 **MCP Server** — Exposes all capabilities over the standard MCP protocol (stdio & Streamable HTTP)

### Frontend `frontend/` (React)

- 📊 **Dashboard** — File totals, record counts, pending amounts, member overview
- 📁 **File Manager** — Member folder tree, drag-and-drop upload, real-time sync
- 📋 **Reimbursement Ledger** — Inline spreadsheet editor with per-member filtering
- 🤖 **Smart Filing** — Conversational agent for reimbursement assistance
- ⚙️ **Tools Console** — Visual MCP tool invocation with execution logs
- 👥 **Members / Server Settings** — Admin-only views

## 🏗️ Architecture

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
                 │  SQLite (dual-db)    │
                 │  invoices.db        │
                 │  records.db         │
                 └─────────────────────┘
```

## 📁 Project Structure

```
invoice-system/                    ← Repository root
├── invoice-toolkit/               ← Python backend
│   ├── invoice_toolkit/           ← Main package
│   ├── skills/                    ← Rule & prompt templates
│   ├── data/                      ← Data (gitignored)
│   ├── output/                    ← Outputs (gitignored)
│   ├── cache/                     ← Cache (gitignored)
│   ├── model/                     ← Document templates
│   └── requirements.txt
├── frontend/                      ← React frontend
│   ├── src/components/
│   ├── package.json
│   └── .env.example
├── .env.example                   ← Backend env template
├── .gitignore
├── LICENSE                        ← MIT
├── README.md
├── README.en.md
└── CONTRIBUTING.md
```

## 🚀 Quick Start

### 1. Clone

```bash
git clone https://github.com/<your-username>/invoice-system.git
cd invoice-system
```

### 2. Backend setup

Requires Python **3.10+**.

```bash
cd invoice-toolkit

conda create -n invoice python=3.10 -y
conda activate invoice

pip install -r requirements.txt

# Optional: only needed for browser automation
playwright install chromium

cd ..  # back to repo root
```

### 3. Configure backend env

Create `.env` at the **repo root** (the backend will load it automatically):

```bash
cp .env.example .env
```

```dotenv
# ─── LLM (SiliconFlow, or any OpenAI-compatible API) ───
SILICONFLOW_API_KEY=sk-xxxxxxxx
LLM_BASE_URL=https://api.siliconflow.cn/v1
LLM_MODEL_NAME=deepseek-ai/DeepSeek-V3

# ─── Baidu OCR ───
BAIDU_OCR_API_KEY=your_api_key
BAIDU_OCR_SECRET_KEY=your_secret_key

# ─── RAG ───
RAG_EMBEDDING_MODEL=BAAI/bge-large-zh-v1.5

# ─── Team member list (comma-separated) ───
NAME_LIST=Alice,Bob,Charlie
```

> 💡 **Get API keys:**
> - SiliconFlow: <https://siliconflow.cn>
> - Baidu OCR: <https://ai.baidu.com/tech/ocr>
>
> You can swap `SILICONFLOW_API_KEY` / `LLM_BASE_URL` for any OpenAI-compatible provider.

See [.env.example](./.env.example) for the full list of options.

### 4. Prepare data directory

```
invoice-toolkit/data/
├── 课题组成员文件/          # One subfolder per member
│   ├── Alice/
│   ├── Bob/
│   └── Charlie/
└── 明细.xlsx              # Handwritten reimbursement ledger
```

### 5. Run the backend

**Interactive CLI:**

```bash
cd invoice-toolkit
python -m invoice_toolkit.cli
# or run the full pipeline at once
python -m invoice_toolkit.cli pipeline
```

**MCP Server (for frontend / Claude Desktop):**

```bash
cd invoice-toolkit
python -m invoice_toolkit.mcp_server --transport http --port 8000
```

**Claude Desktop integration:**

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

### 6. Run the frontend

```bash
cd frontend
cp .env.example .env       # Optional: customize MCP URL, admin password
npm install
npm run dev
```

Open <http://localhost:5173>. It will connect to `http://localhost:8000` by default.

## 📖 Usage

### CLI commands

Run from `invoice-toolkit/`:

| Command | Description |
|---------|-------------|
| `python -m invoice_toolkit.cli match` | OCR + match invoices with records |
| `python -m invoice_toolkit.cli classify` | LLM classification |
| `python -m invoice_toolkit.cli move` | Move files by category |
| `python -m invoice_toolkit.cli check` | Attachment completeness check |
| `python -m invoice_toolkit.cli check-names` | Filename convention check |
| `python -m invoice_toolkit.cli pipeline` | Run the full pipeline |
| `python -m invoice_toolkit.cli agent` | Start the conversational agent |
| `python -m invoice_toolkit.cli rag` | Q&A on reimbursement policies |
| `python -m invoice_toolkit.cli clean` | Wipe all generated data |

### Agent chat example

```
You:   Scan the invoice directory and run the full pipeline.
Agent: Sure — I'll run OCR, matching, classification, filing,
       and attachment checks in order...

You:   What documents do I need for a travel reimbursement?
Agent: According to the institutional travel reimbursement
       policy, you'll need...
```

## 🛠️ Tech Stack

**Backend**
- Python 3.10+ · LangChain 0.3 · LangGraph · FastMCP
- Baidu OCR · BGE-zh embeddings · FAISS
- SQLite · pandas · openpyxl
- browser-use · Playwright

**Frontend**
- React 18 · Vite · MCP Streamable HTTP Client

## 🗺️ Roadmap

- [ ] Support more OCR providers (Aliyun, Tencent Cloud)
- [ ] Support more LLM providers (Claude, GPT-4, Gemini)
- [ ] Docker Compose one-click deployment
- [ ] Multi-tenant support
- [ ] Mobile responsive UI
- [ ] Internationalization (i18n)

## 🤝 Contributing

Contributions are welcome! See [CONTRIBUTING.md](./CONTRIBUTING.md).

## ⚠️ Disclaimer

- Institution-specific code (e.g. "Shandong University reimbursement system") is included only as a reference implementation of browser automation. Use against any specific institution's system must comply with that institution's policies.
- OCR / LLM results are not guaranteed to be 100% accurate. For any financial-impacting workflow, always review manually.
- Keep your API keys and login credentials safe — **never commit `.env` or `cookies.json` to a public repo**.

## 📄 License

Released under the [MIT License](./LICENSE).

## 🙏 Acknowledgements

- [LangChain](https://github.com/langchain-ai/langchain) & [LangGraph](https://github.com/langchain-ai/langgraph)
- [FastMCP](https://github.com/jlowin/fastmcp)
- [browser-use](https://github.com/browser-use/browser-use)
- [BAAI/bge-large-zh](https://huggingface.co/BAAI/bge-large-zh-v1.5)

---

<p align="center">
  If this project helps you, please consider giving it a ⭐️!
</p>
