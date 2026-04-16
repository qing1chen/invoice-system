import { useState, useCallback, useRef } from "react";
import { T, STATUS_COLORS } from "../theme";
import { NAME_LIST } from "../constants";
import { saveData } from "../services/storage";
import { fetchTableData } from "../services/data";
import { Button, Badge, Card, EmptyState, PageHeader, CellEditor, Spinner } from "./ui";

// ─── 列定义 ───────────────────────────────────────────────
// 基础列（始终显示且固定在最前面，按此顺序排列）
const BASE_COLUMNS = [
  "序号",
  "姓名/公司",
  "填写日期",
  "金额",
  "物品简介",
  "备注",
  "类别",
  "匹配发票",
  "匹配附件",
];

// 隐藏列（内部字段，不在表格中显示）
const HIDDEN_COLUMNS = new Set([
  "id", "db_id", "updated_at", "extra_fields", "category",
  "发票路径", "附件路径",
  "匹配发票金额", "是否匹配", "匹配方式", "组合金额", "备注分解金额", "未匹配金额",
  // 发票详情字段（由后端 get_records_joined 从 invoices 表关联而来，仅内部使用）
  "发票号码", "价税合计", "商品名称", "开票日期", "销售方名称", "发票类型",
]);

// 只读列（不可编辑）
const READONLY_COLS = new Set(["序号", "匹配发票", "匹配附件"]);

// 管理员额外可编辑列
const ADMIN_EXTRA_COLS = new Set(["姓名/公司"]);

/**
 * 从数据中动态推导完整列列表：
 * 基础列（固定顺序）+ 数据中出现的额外列（按首次出现顺序）
 */
function deriveColumns(tableData) {
  const baseSet = new Set(BASE_COLUMNS);
  const extraCols = [];
  const seen = new Set();

  for (const row of tableData) {
    for (const key of Object.keys(row)) {
      if (!baseSet.has(key) && !HIDDEN_COLUMNS.has(key) && !seen.has(key)) {
        seen.add(key);
        extraCols.push(key);
      }
    }
  }

  return [...BASE_COLUMNS, ...extraCols];
}

/**
 * 报销明细表格编辑器
 *
 * 功能：
 *   - 列名与 records 数据库对齐（填写日期、物品简介）
 *   - 优先从 records 数据库同步，无数据时降级到 明细.xlsx
 *   - 新增「类别」「匹配发票」「匹配附件」列，来源于数据库
 *   - 内联编辑后自动回写 records 数据库（单条更新）
 *   - 修改类别时同时更新 records.db 和 invoices.db（双库同步）
 *   - 新增行立即写入数据库（拿回 db_id）
 *   - 批量保存到数据库（替代旧版保存到 xlsx）
 *
 * @param {object}   user         - 当前用户
 * @param {Array}    tableData    - 报销记录数组
 * @param {function} setTableData - 更新记录
 * @param {string}   mcpUrl       - MCP Server 地址
 * @param {string}   dataSource   - 数据来源 "db" | "mcp" | "local"
 * @param {function} onRefresh    - 手动刷新远程数据
 */
export default function TableEditor({
  user,
  tableData,
  setTableData,
  mcpUrl,
  dataSource,
  onRefresh,
}) {
  const isAdmin = user.role === "admin";
  const [editingCell, setEditingCell] = useState(null);
  const [editValue, setEditValue] = useState("");
  const [filterName, setFilterName] = useState(isAdmin ? "" : user.name);
  const [sortCol, setSortCol] = useState(null);
  const [sortDir, setSortDir] = useState("asc");
  const [saving, setSaving] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [saveMsg, setSaveMsg] = useState(null);
  const [dbUpdating, setDbUpdating] = useState(new Set());
  const msgTimer = useRef(null);

  // ─── 筛选 + 排序 ────────────────────────────────────────
  let data = filterName
    ? tableData.filter((r) => r["姓名/公司"] === filterName)
    : tableData;

  if (sortCol) {
    data = [...data].sort((a, b) => {
      const av = a[sortCol] ?? "";
      const bv = b[sortCol] ?? "";
      const cmp =
        typeof av === "number" && typeof bv === "number"
          ? av - bv
          : String(av).localeCompare(String(bv), "zh");
      return sortDir === "asc" ? cmp : -cmp;
    });
  }

  // ─── 动态列 ────────────────────────────────────────────
  const columns = deriveColumns(tableData);

  // ─── 编辑权限 ───────────────────────────────────────────
  const editableCols = columns.filter((col) => {
    if (READONLY_COLS.has(col)) return false;
    if (!isAdmin && ADMIN_EXTRA_COLS.has(col)) return false;
    return true;
  });

  const canEditRow = (row) => isAdmin || row["姓名/公司"] === user.name;

  // ─── 提示消息 ───────────────────────────────────────────
  const showMsg = (type, text, duration = 4000) => {
    setSaveMsg({ type, text });
    if (msgTimer.current) clearTimeout(msgTimer.current);
    msgTimer.current = setTimeout(() => setSaveMsg(null), duration);
  };

  // ─── 单条编辑提交 ──────────────────────────────────────
  const commitEdit = async (rowId, col) => {
    const newVal = col === "金额" ? parseFloat(editValue) || 0 : editValue;
    const updated = tableData.map((r) =>
      r.id === rowId ? { ...r, [col]: newVal } : r
    );
    setTableData(updated);
    saveData("invoice-table", updated);
    setEditingCell(null);

    // 回写到 records 数据库
    const row = updated.find((r) => r.id === rowId);
    if (row && mcpUrl) {
      setDbUpdating((prev) => new Set(prev).add(rowId));
      try {
        await updateRecordInDB(mcpUrl, row);
      } catch (err) {
        console.error("数据库更新失败:", err);
        showMsg("error", `数据库更新失败: ${err.message}`);
      } finally {
        setDbUpdating((prev) => {
          const next = new Set(prev);
          next.delete(rowId);
          return next;
        });
      }
    }
  };

  // ─── 批量操作 ──────────────────────────────────────────
  const updateTable = (updated) => {
    setTableData(updated);
    saveData("invoice-table", updated);
  };

  const addRow = async () => {
    // 用 max(序号)+1 而非 length+1：
    // 删行后 length 缩小，length+1 可能与已有序号重复；
    // 虽然 DB 层已移除 UNIQUE 约束，但前端层序号仍保持自增有序，避免视觉混乱。
    const maxSeq = tableData.reduce(
      (m, r) => Math.max(m, Number(r["序号"]) || 0),
      0
    );
    const newRow = {
      id: `r_${Date.now()}`,
      序号: maxSeq + 1,
      "姓名/公司": isAdmin ? "" : user.name,
      填写日期: new Date().toISOString().slice(0, 10),
      金额: 0,
      物品简介: "",
      备注: "",
      类别: "",
      匹配发票: "",
      匹配附件: "",
    };

    // 立即写入数据库，拿回 db_id
    if (mcpUrl) {
      try {
        const dbId = await createRecordInDB(mcpUrl, newRow);
        if (dbId) {
          newRow.db_id = dbId;
          newRow.id = `db_${dbId}`;
        }
      } catch (err) {
        console.warn("新增行写入数据库失败（将在「保存到数据库」时重试）:", err);
      }
    }

    updateTable([...tableData, newRow]);
  };

  const deleteRow = async (row) => {
    updateTable(tableData.filter((r) => r.id !== row.id));
    // 同时从数据库删除
    if (mcpUrl && row.db_id) {
      try {
        await deleteRecordFromDB(mcpUrl, row.db_id);
      } catch (err) {
        console.error("数据库删除失败:", err);
      }
    }
  };

  // ─── 排序 ──────────────────────────────────────────────
  const handleSort = (col) => {
    if (sortCol === col) setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    else {
      setSortCol(col);
      setSortDir("asc");
    }
  };

  // ─── 保存到数据库 ───────────────────────────────────────
  const handleSaveToBackend = useCallback(async () => {
    setSaving(true);
    setSaveMsg(null);
    try {
      const baseUrl = mcpUrl.replace(/\/mcp\/?$/, "").replace(/\/$/, "");

      // 构建批量保存的行数据（透传所有字段）
      const rows = tableData.map((r) => {
        const row = { db_id: r.db_id || null };
        for (const [k, v] of Object.entries(r)) {
          if (k === "id" || k === "db_id") continue;
          // 前端「类别」→ DB「category」
          if (k === "类别") {
            row["类别"] = v || "";
            continue;
          }
          row[k] = v ?? "";
        }
        return row;
      });

      const res = await fetch(`${baseUrl}/api/records/batch`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ rows }),
      });

      if (!res.ok) {
        const errText = await res.text().catch(() => "");
        throw new Error(`HTTP ${res.status}: ${errText}`);
      }

      const result = await res.json();

      // 保存后必须重新同步：
      // batch_upsert 会插入新行并分配新 db_id；
      // 若不刷新，新增行的 db_id 仍是 undefined，
      // 之后编辑单元格时 PUT /api/records/undefined 会静默失败。
      try {
        const dbResult = await fetchRecordsFromDB(mcpUrl);
        if (!dbResult.error && dbResult.rows.length > 0) {
          setTableData(dbResult.rows);
          saveData("invoice-table", dbResult.rows);
        }
      } catch (_) {
        // 刷新失败不影响保存成功提示
      }

      showMsg(result.success ? "success" : "error", result.message);
    } catch (err) {
      showMsg("error", `保存到数据库失败: ${err.message}`);
    } finally {
      setSaving(false);
    }
  }, [mcpUrl, tableData, setTableData]);

  // ─── 从后端同步（优先 records 数据库，降级 明细.xlsx）──
  const handleSyncFromBackend = useCallback(async () => {
    setSyncing(true);
    try {
      // 1) 优先尝试从 records 数据库拉取
      let loaded = false;
      try {
        const dbResult = await fetchRecordsFromDB(mcpUrl);
        if (!dbResult.error && dbResult.rows.length > 0) {
          setTableData(dbResult.rows);
          saveData("invoice-table", dbResult.rows);
          showMsg("success", `已从记录数据库同步 ${dbResult.rows.length} 条数据`);
          loaded = true;
        }
      } catch (err) {
        console.warn("记录数据库同步失败，降级到 xlsx:", err);
      }

      // 2) 降级：从明细.xlsx 拉取
      if (!loaded) {
        try {
          const result = await fetchTableData(mcpUrl);
          if (!result.error && result.rows.length > 0) {
            setTableData(result.rows);
            saveData("invoice-table", result.rows);
            showMsg("success", `已从 明细.xlsx 同步 ${result.rows.length} 条数据`);
            loaded = true;
          }
        } catch (err) {
          console.warn("xlsx 同步也失败:", err);
        }
      }

      // 3) 使用传入的 onRefresh 回退
      if (!loaded && onRefresh) {
        await onRefresh();
      }

      if (!loaded) {
        showMsg("error", "同步失败：无法连接数据库或 xlsx");
      }
    } finally {
      setSyncing(false);
    }
  }, [mcpUrl, setTableData, onRefresh]);

  // ─── 数据来源标签 ──────────────────────────────────────
  const sourceLabel =
    dataSource === "db"
      ? "records 数据库"
      : dataSource === "mcp"
      ? "MCP 后端 · 明细.xlsx"
      : "本地缓存";

  // ─── 渲染 ──────────────────────────────────────────────
  return (
    <div style={{ animation: "fadeIn 0.4s ease" }}>
      <PageHeader
        title="报销明细"
        subtitle={
          isAdmin
            ? `管理所有成员的报销记录（${sourceLabel}）`
            : "编辑您的报销记录"
        }
      >
        {isAdmin && (
          <select
            value={filterName}
            onChange={(e) => setFilterName(e.target.value)}
            style={{
              padding: "8px 12px",
              background: T.surface,
              border: `1px solid ${T.border}`,
              borderRadius: T.radiusSm,
              color: T.text,
              fontFamily: T.font,
              fontSize: "13px",
              outline: "none",
            }}
          >
            <option value="">全部成员</option>
            {NAME_LIST.map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
        )}

        {/* 同步：优先数据库 */}
        <Button
          variant="secondary"
          onClick={handleSyncFromBackend}
          disabled={syncing}
        >
          {syncing ? <Spinner size={14} /> : "🔄"} 同步
        </Button>

        {/* 保存到数据库 */}
        <Button
          variant="success"
          onClick={handleSaveToBackend}
          disabled={saving}
        >
          {saving ? <Spinner size={14} /> : "💾"} 保存到数据库
        </Button>

        <Button onClick={addRow}>＋ 新增记录</Button>
      </PageHeader>

      {/* 操作结果提示 */}
      {saveMsg && (
        <div
          style={{
            padding: "8px 14px",
            marginBottom: "12px",
            borderRadius: T.radiusSm,
            background:
              saveMsg.type === "success"
                ? `${T.success}15`
                : `${T.danger}15`,
            border: `1px solid ${
              saveMsg.type === "success" ? T.success : T.danger
            }30`,
            fontSize: "12px",
            color: saveMsg.type === "success" ? T.success : T.danger,
          }}
        >
          {saveMsg.text}
        </div>
      )}

      {/* 数据来源提示条 */}
      {(dataSource === "db" || dataSource === "mcp") && (
        <div
          style={{
            padding: "8px 14px",
            marginBottom: "12px",
            borderRadius: T.radiusSm,
            background: `${T.success}15`,
            border: `1px solid ${T.success}30`,
            fontSize: "12px",
            color: T.success,
            display: "flex",
            alignItems: "center",
            gap: "6px",
          }}
        >
          <span
            style={{
              width: "6px",
              height: "6px",
              borderRadius: "50%",
              background: T.success,
              flexShrink: 0,
            }}
          />
          数据来源：{sourceLabel} · 编辑单元格后自动回写数据库 ·
          点击「保存到数据库」批量同步
        </div>
      )}

      {/* 表格主体 */}
      <Card style={{ padding: "0", overflow: "hidden" }}>
        <div style={{ overflowX: "auto" }}>
          <table
            style={{
              width: "100%",
              borderCollapse: "collapse",
              fontFamily: T.font,
              fontSize: "13px",
            }}
          >
            <thead>
              <tr style={{ background: T.surface }}>
                {columns.map((col) => (
                  <th
                    key={col}
                    onClick={() => handleSort(col)}
                    style={{
                      padding: "12px 14px",
                      textAlign: "left",
                      fontWeight: 600,
                      color: T.textSecondary,
                      fontSize: "12px",
                      borderBottom: `1px solid ${T.border}`,
                      cursor: "pointer",
                      userSelect: "none",
                      whiteSpace: "nowrap",
                    }}
                  >
                    {col}
                    {sortCol === col && (
                      <span style={{ marginLeft: "4px" }}>
                        {sortDir === "asc" ? "↑" : "↓"}
                      </span>
                    )}
                  </th>
                ))}
                <th
                  style={{
                    padding: "12px 14px",
                    textAlign: "center",
                    fontWeight: 600,
                    color: T.textSecondary,
                    fontSize: "12px",
                    borderBottom: `1px solid ${T.border}`,
                    width: "60px",
                  }}
                >
                  操作
                </th>
              </tr>
            </thead>
            <tbody>
              {data.map((row, ri) => {
                const isRowUpdating = dbUpdating.has(row.id);
                return (
                  <tr
                    key={row.id}
                    style={{
                      borderBottom: `1px solid ${T.border}`,
                      animation: `fadeIn 0.2s ease ${ri * 0.02}s both`,
                      opacity: isRowUpdating ? 0.6 : 1,
                      transition: "opacity 0.2s",
                    }}
                    onMouseEnter={(e) =>
                      (e.currentTarget.style.background = T.surfaceHover)
                    }
                    onMouseLeave={(e) =>
                      (e.currentTarget.style.background = "transparent")
                    }
                  >
                    {columns.map((col) => {
                      const cellId = `${row.id}-${col}`;
                      const isEditing = editingCell === cellId;
                      const editable =
                        canEditRow(row) && editableCols.includes(col);
                      const val = row[col] ?? "";

                      return (
                        <td
                          key={col}
                          onClick={() => {
                            if (editable && !isEditing) {
                              setEditingCell(cellId);
                              setEditValue(String(val));
                            }
                          }}
                          style={{
                            padding: isEditing ? "6px 8px" : "10px 14px",
                            color: T.text,
                            cursor: editable ? "text" : "default",
                            whiteSpace: col === "匹配发票" || col === "匹配附件" ? "normal" : "nowrap",
                            maxWidth: col === "匹配发票" || col === "匹配附件" ? "320px" : "200px",
                            overflow: "hidden",
                            textOverflow: col === "匹配发票" || col === "匹配附件" ? "unset" : "ellipsis",
                          }}
                          title={String(val)}
                        >
                          {isEditing ? (
                            <CellEditor
                              col={col}
                              value={editValue}
                              onChange={setEditValue}
                              onCommit={() => commitEdit(row.id, col)}
                            />
                          ) : col === "金额" ? (
                            <span
                              style={{
                                fontFamily: T.mono,
                                fontWeight: 500,
                              }}
                            >
                              ¥{parseFloat(val || 0).toFixed(2)}
                            </span>
                          ) : col === "匹配发票" || col === "匹配附件" ? (
                            <MatchBadge value={val} />
                          ) : col === "类别" ? (
                            <Badge
                              color={
                                val
                                  ? STATUS_COLORS[val] || T.primary || "#6366f1"
                                  : T.textMuted
                              }
                            >
                              {val || "—"}
                            </Badge>
                          ) : (
                            <span>{val || "—"}</span>
                          )}
                        </td>
                      );
                    })}
                    <td style={{ padding: "10px 14px", textAlign: "center" }}>
                      {canEditRow(row) && (
                        <button
                          onClick={() => deleteRow(row)}
                          style={{
                            background: "none",
                            border: "none",
                            color: T.textMuted,
                            cursor: "pointer",
                            fontSize: "13px",
                            padding: "4px 8px",
                            borderRadius: T.radiusSm,
                          }}
                          onMouseEnter={(e) =>
                            (e.target.style.color = T.danger)
                          }
                          onMouseLeave={(e) =>
                            (e.target.style.color = T.textMuted)
                          }
                          title="删除"
                        >
                          🗑
                        </button>
                      )}
                      {isRowUpdating && (
                        <Spinner size={12} />
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        {!data.length && (
          <EmptyState
            icon="📋"
            title="暂无记录"
            subtitle="点击「新增记录」添加报销条目，或点击「同步」从数据库加载"
          />
        )}
      </Card>

      {/* 底部统计 */}
      <div
        style={{
          marginTop: "12px",
          fontSize: "12px",
          color: T.textMuted,
          display: "flex",
          justifyContent: "space-between",
        }}
      >
        <span>共 {data.length} 条记录</span>
        <span>
          总金额：
          <span
            style={{
              fontFamily: T.mono,
              color: T.warning,
              fontWeight: 600,
            }}
          >
            ¥
            {data
              .reduce((s, r) => s + (parseFloat(r.金额) || 0), 0)
              .toFixed(2)}
          </span>
        </span>
      </div>
    </div>
  );
}

// ─── 匹配状态小标签 ─────────────────────────────────────
function MatchBadge({ value }) {
  if (!value) return <span style={{ color: T.textMuted }}>—</span>;

  // 拆分逗号分隔的多个文件名，逐个显示
  const items = value.split(",").map((s) => s.trim()).filter(Boolean);

  return (
    <span
      style={{
        display: "flex",
        flexDirection: "column",
        gap: "4px",
        alignItems: "flex-start",
      }}
    >
      {items.map((item, i) => {
        const isMatched = item && item !== "未匹配";
        return (
          <span
            key={i}
            style={{
              display: "inline-block",
              maxWidth: "300px",
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
              fontSize: "12px",
              padding: "2px 8px",
              borderRadius: "4px",
              background: isMatched ? `${T.success}18` : `${T.warning}18`,
              color: isMatched ? T.success : T.warning,
              border: `1px solid ${isMatched ? T.success : T.warning}30`,
            }}
            title={item}
          >
            {item}
          </span>
        );
      })}
    </span>
  );
}

// =========================================================================
// 数据库交互函数（通过 MCP Server）
// =========================================================================

/**
 * 从 records 数据库拉取全部记录，并关联 invoices 中的
 * 类别、匹配发票、匹配附件 信息。
 *
 * 调用后端 REST API: GET /api/records
 *
 * @param {string} mcpUrl - MCP 后端地址（如 http://localhost:8000）
 * @returns {{ rows: Array, error: string|null }}
 */
async function fetchRecordsFromDB(mcpUrl) {
  // 构建 REST 端点 URL（与 MCP 同域）
  const baseUrl = mcpUrl.replace(/\/mcp\/?$/, "").replace(/\/$/, "");
  const res = await fetch(`${baseUrl}/api/records`, { method: "GET" });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const json = await res.json();

  if (json.error) throw new Error(json.error);

  // 将 DB 行映射为前端行格式
  // 保留后端返回的所有字段，确保明细中的动态列都能显示
  const rows = (json.records || []).map((rec, idx) => {
    const row = {
      id: rec.id ? `db_${rec.id}` : `r_${idx}`,
      db_id: rec.id,
    };

    // 将后端返回的所有字段透传到前端行
    for (const [key, value] of Object.entries(rec)) {
      if (key === "id" || key === "updated_at" || key === "extra_fields") continue;
      // category → 类别（前端显示名）
      if (key === "category") {
        if (!row["类别"]) row["类别"] = value ?? "";
        continue;
      }
      row[key] = value ?? "";
    }

    // 确保基础字段存在（即使后端未返回）
    row["序号"] = row["序号"] ?? idx + 1;
    row["姓名/公司"] = row["姓名/公司"] ?? "";
    row["填写日期"] = row["填写日期"] ?? "";
    row["金额"] = parseFloat(row["金额"]) || 0;
    row["物品简介"] = row["物品简介"] ?? "";
    row["备注"] = row["备注"] ?? "";
    row["类别"] = row["类别"] ?? "";
    row["匹配发票"] = row["匹配发票"] ?? "";
    row["匹配附件"] = row["匹配附件"] ?? "";

    return row;
  });

  return { rows, error: null };
}

/**
 * 更新 records 数据库中的单条记录。
 * 编辑单元格后自动调用。
 *
 * 调用后端 REST API: PUT /api/records/{id}
 *
 * @param {string} mcpUrl
 * @param {object} row  - 前端行对象（需含 db_id）
 */
async function updateRecordInDB(mcpUrl, row) {
  const baseUrl = mcpUrl.replace(/\/mcp\/?$/, "").replace(/\/$/, "");

  // 透传所有字段（排除前端内部字段）
  const payload = {};
  for (const [k, v] of Object.entries(row)) {
    if (k === "id" || k === "db_id" || k === "updated_at") continue;
    // 前端「类别」→ DB「category」
    if (k === "类别") {
      payload["category"] = v || "";
      continue;
    }
    payload[k] = v ?? "";
  }

  const res = await fetch(`${baseUrl}/api/records/${row.db_id}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  if (!res.ok) {
    const errText = await res.text().catch(() => "");
    throw new Error(`数据库更新失败 (HTTP ${res.status}): ${errText}`);
  }
  return res.json();
}

/**
 * 从 records 数据库删除一条记录。
 *
 * 调用后端 REST API: DELETE /api/records/{id}
 *
 * @param {string} mcpUrl
 * @param {number} dbId  - records 表主键 id
 */
async function deleteRecordFromDB(mcpUrl, dbId) {
  const baseUrl = mcpUrl.replace(/\/mcp\/?$/, "").replace(/\/$/, "");
  const res = await fetch(`${baseUrl}/api/records/${dbId}`, {
    method: "DELETE",
  });
  if (!res.ok) {
    throw new Error(`数据库删除失败 (HTTP ${res.status})`);
  }
  return res.json();
}

/**
 * 在 records 数据库创建一条新记录，返回其主键 db_id。
 *
 * 调用后端 REST API: POST /api/records
 *
 * @param {string} mcpUrl
 * @param {object} row  - 前端行对象
 * @returns {number|null} 新记录的数据库 id
 */
async function createRecordInDB(mcpUrl, row) {
  const baseUrl = mcpUrl.replace(/\/mcp\/?$/, "").replace(/\/$/, "");

  // 透传所有字段（排除前端内部字段）
  const payload = {};
  for (const [k, v] of Object.entries(row)) {
    if (k === "id" || k === "db_id" || k === "updated_at") continue;
    if (k === "类别") {
      payload["category"] = v || "";
      continue;
    }
    payload[k] = v ?? "";
  }

  const res = await fetch(`${baseUrl}/api/records`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  if (!res.ok) {
    const errText = await res.text().catch(() => "");
    throw new Error(`数据库创建失败 (HTTP ${res.status}): ${errText}`);
  }

  const json = await res.json();
  return json.db_id || null;
}