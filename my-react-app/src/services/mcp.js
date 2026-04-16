/**
 * MCP Server 通信层
 *
 * 封装了与 Invoice Toolkit MCP Server 的全部交互：
 *   - 会话初始化（initialize + notifications/initialized）
 *   - 工具调用（tools/call）
 *   - 工具列表查询（tools/list）
 *   - 响应解析（JSON / SSE 自适应）
 */

import { DEFAULT_MCP_URL } from "../constants";

// ─── 内部状态 ──────────────────────────────────────────────

let _mcpRequestId = 1;

const MCP_HEADERS = {
  "Content-Type": "application/json",
  "Accept": "text/event-stream, application/json",
};

const MCP_CLIENT_INFO = {
  name: "invoice-frontend",
  version: "1.0.0",
};

// ─── 私有方法 ──────────────────────────────────────────────

/**
 * 解析 MCP 响应 —— 自动区分 JSON 与 SSE 流
 */
function parseMcpResponse(res) {
  const ct = res.headers.get("content-type") || "";
  if (!ct.includes("text/event-stream")) return res.json();

  return res.text().then((text) => {
    let result = null;
    for (const line of text.split("\n")) {
      if (!line.startsWith("data: ")) continue;
      try {
        const parsed = JSON.parse(line.slice(6));
        if (parsed.result || parsed.id) result = parsed;
      } catch {
        /* 忽略非 JSON 行 */
      }
    }
    return result;
  });
}

/**
 * 建立 MCP 会话
 * 完成 initialize → notifications/initialized 握手，返回 headers 和 baseId
 */
async function initMcpSession(serverUrl) {
  const id = _mcpRequestId++;

  // 1. initialize
  const initRes = await fetch(`${serverUrl}/mcp`, {
    method: "POST",
    headers: MCP_HEADERS,
    body: JSON.stringify({
      jsonrpc: "2.0",
      id,
      method: "initialize",
      params: {
        protocolVersion: "2025-03-26",
        capabilities: {},
        clientInfo: MCP_CLIENT_INFO,
      },
    }),
  });
  if (!initRes.ok) {
    throw new Error(`MCP 初始化失败 (${initRes.status}): ${await initRes.text()}`);
  }

  const sessionId = initRes.headers.get("mcp-session-id") || null;
  await parseMcpResponse(initRes);

  // 2. notifications/initialized
  const headers = {
    ...MCP_HEADERS,
    ...(sessionId ? { "mcp-session-id": sessionId } : {}),
  };
  await fetch(`${serverUrl}/mcp`, {
    method: "POST",
    headers,
    body: JSON.stringify({
      jsonrpc: "2.0",
      method: "notifications/initialized",
    }),
  });

  return { headers, baseId: id };
}

// ─── 公共 API ──────────────────────────────────────────────

/**
 * 调用 MCP Server 上的指定工具
 *
 * @param {string} toolName  - MCP 工具名称，如 "invoice_run_ocr"
 * @param {object} args      - 传递给工具的参数
 * @param {string} serverUrl - MCP Server 地址
 * @returns {object} 工具执行结果
 */
export async function callMcpTool(toolName, args = {}, serverUrl = DEFAULT_MCP_URL) {
  const { headers, baseId } = await initMcpSession(serverUrl);

  const res = await fetch(`${serverUrl}/mcp`, {
    method: "POST",
    headers,
    body: JSON.stringify({
      jsonrpc: "2.0",
      id: baseId + 1,
      method: "tools/call",
      params: { name: toolName, arguments: args },
    }),
  });
  if (!res.ok) {
    throw new Error(`MCP 工具调用失败 (${res.status}): ${await res.text()}`);
  }

  const result = await parseMcpResponse(res);
  if (result?.error) {
    throw new Error(`MCP Error [${result.error.code}]: ${result.error.message}`);
  }
  return result?.result || result;
}

/**
 * 获取 MCP Server 上已注册的工具列表
 *
 * @param {string} serverUrl - MCP Server 地址
 * @returns {Array} 工具描述对象数组
 */
export async function listMcpTools(serverUrl = DEFAULT_MCP_URL) {
  const { headers, baseId } = await initMcpSession(serverUrl);

  const res = await fetch(`${serverUrl}/mcp`, {
    method: "POST",
    headers,
    body: JSON.stringify({
      jsonrpc: "2.0",
      id: baseId + 1,
      method: "tools/list",
      params: {},
    }),
  });
  if (!res.ok) {
    throw new Error(`tools/list 失败 (${res.status})`);
  }

  const result = await parseMcpResponse(res);
  return result?.result?.tools || [];
}

/**
 * 从 MCP 工具返回值中提取纯文本
 *
 * @param {*} result - callMcpTool 的返回值
 * @returns {string} 可读文本
 */
export function extractMcpText(result) {
  if (!result) return "(无返回内容)";
  if (typeof result === "string") return result;
  if (Array.isArray(result.content)) {
    return result.content
      .filter((c) => c.type === "text")
      .map((c) => c.text)
      .join("\n");
  }
  return result.text || JSON.stringify(result, null, 2);
}
