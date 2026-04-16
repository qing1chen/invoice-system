/**
 * 数据同步服务层
 *
 * 封装前端与 MCP Server 之间的数据同步逻辑：
 *   - 从后端 data/课题组成员文件/ 拉取文件列表
 *   - 从后端 data/明细.xlsx 拉取报销明细
 *   - 将前端编辑的报销明细回写到后端 明细.xlsx
 *
 * 降级策略：MCP 不可用时回退到 localStorage 本地数据
 */

import { callMcpTool, extractMcpText } from "./mcp";

// ─── 文件列表同步 ──────────────────────────────────────────

/**
 * 从 MCP Server 拉取课题组成员目录下的真实文件列表
 *
 * @param {string} serverUrl  - MCP Server 地址
 * @param {string} [member]   - 可选，指定成员姓名
 * @returns {Promise<{files: Array, error?: string}>}
 */
export async function fetchMemberFiles(serverUrl, member = null) {
  try {
    const args = member ? { params: { member } } : { params: {} };
    const result = await callMcpTool("invoice_list_member_files", args, serverUrl);
    const text = extractMcpText(result);
    const data = JSON.parse(text);
    return { files: data.files || [], error: data.error || null };
  } catch (err) {
    return { files: [], error: err.message };
  }
}

// ─── 报销明细同步 ──────────────────────────────────────────

/**
 * 从 MCP Server 读取 data/明细.xlsx 中的报销记录
 *
 * @param {string} serverUrl - MCP Server 地址
 * @returns {Promise<{rows: Array, columns: Array, error?: string}>}
 */
export async function fetchTableData(serverUrl) {
  try {
    const result = await callMcpTool("invoice_read_table", {}, serverUrl);
    const text = extractMcpText(result);
    const data = JSON.parse(text);
    return {
      rows: data.rows || [],
      columns: data.columns || [],
      error: data.error || null,
    };
  } catch (err) {
    return { rows: [], columns: [], error: err.message };
  }
}

/**
 * 将前端编辑的报销明细数据保存到后端 data/明细.xlsx
 *
 * @param {string} serverUrl - MCP Server 地址
 * @param {Array}  rows      - 报销记录数组
 * @returns {Promise<{success: boolean, message: string}>}
 */
export async function saveTableData(serverUrl, rows) {
  try {
    // 移除前端内部 id 字段后传给后端
    const cleanRows = rows.map(({ id, ...rest }) => rest);
    const result = await callMcpTool(
      "invoice_save_table",
      { params: { rows: JSON.stringify(cleanRows) } },
      serverUrl,
    );
    const text = extractMcpText(result);
    const isErr = text.startsWith("错误");
    return { success: !isErr, message: text };
  } catch (err) {
    return { success: false, message: err.message };
  }
}
