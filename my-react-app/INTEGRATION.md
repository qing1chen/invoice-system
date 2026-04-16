# Invoice Toolkit 前端 — MCP Server 集成说明

## 改动概要

将前端从 **模拟数据 (setTimeout mock)** 改为 **真实调用 MCP Server** (Streamable HTTP 协议)。

## 核心变更

### 1. 新增 MCP 通信层 (`callMcpTool` / `listMcpTools`)

前端通过 Streamable HTTP 协议与 MCP Server 通信，每次调用完整握手流程：

```
initialize → notifications/initialized → tools/call
```

支持 JSON 响应和 SSE 流式响应两种模式。

### 2. 工具映射表 (`MCP_TOOL_MAP`)

前端 `toolId` 与后端 `mcp_server.py` 中的 `@mcp.tool` 一一对应：

| 前端 toolId    | MCP 工具名                        | 默认参数                              |
|---------------|-----------------------------------|---------------------------------------|
| `scan`        | `invoice_scan_directory`          | `{}`                                  |
| `ocr`         | `invoice_run_ocr`                 | `{}`                                  |
| `match`       | `invoice_run_matching`            | `{}`                                  |
| `classify`    | `invoice_run_classification`      | `{}`                                  |
| `move`        | `invoice_run_file_move`           | `{ confirm: false }`                  |
| `check`       | `invoice_check_attachments`       | `{}`                                  |
| `checkNames`  | `invoice_check_filenames`         | `{ dry_run: true }`                   |
| `pipeline`    | `invoice_run_pipeline`            | `{ skip_ocr: false, confirm_move: false }` |
| `rag`         | `invoice_query_policy`            | `{ question: "..." }`                |
| `clean`       | `invoice_clean_data`              | `{ confirm: false }`                  |
| `rebuildRag`  | `invoice_rebuild_rag_index`       | `{}`                                  |

### 3. `ToolsConsole` 组件重写

- **不再使用 `setTimeout` 模拟** — 所有操作直接调用 MCP Server
- **服务器状态检测** — 启动时自动检测 MCP Server 连接，显示在线/离线状态
- **工具可用性检查** — 对比 `tools/list` 返回的工具列表，标记每个工具是否在服务端注册
- **危险操作确认** — `move` / `pipeline` / `clean` 等破坏性工具弹出确认对话框
- **错误处理** — MCP 调用失败时在日志面板显示红色错误信息

### 4. 新增「服务器配置」页面 (`ServerSettings`)

管理员可以：
- 修改 MCP Server URL（默认 `http://localhost:8000`）
- 测试连接并查看可用工具列表
- 查看启动命令和工具映射关系

### 5. `extractMcpText` 工具函数

统一处理 MCP 工具返回的各种格式：
- `{ content: [{ type: "text", text: "..." }] }` — 标准 MCP 返回
- 纯字符串
- JSON 对象（fallback 为 JSON.stringify）

## 使用方式

### 1. 启动 MCP Server（HTTP 模式）

```bash
cd D:\Spyder_down\llm\invoice-toolkit
conda activate llm
python -m invoice_toolkit.mcp_server --transport http --port 8000
```

### 2. 启动前端

```bash
cd D:\Spyder_down\llm\react\my-react-app
npm install
npm run dev
```

### 3. 在前端配置连接

1. 以管理员登录（密码 `admin123`）
2. 点击侧边栏「服务器配置」
3. 确认 MCP Server URL 为 `http://localhost:8000`
4. 点击「测试连接」验证
5. 进入「工具控制台」使用所有功能

## CORS 注意事项

前端直接通过浏览器 `fetch` 调用 MCP Server，需要服务端允许跨域。
如果遇到 CORS 错误，需要在 `mcp_server.py` 中添加 CORS 中间件：

```python
# 在 mcp_server.py 的 HTTP 模式中添加
from starlette.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 生产环境请限制域名
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["mcp-session-id"],
)
```
