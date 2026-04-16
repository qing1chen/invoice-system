import { useState, useEffect, useCallback, useRef } from "react";
import { T, LOG_COLORS } from "../theme";
import { TOOLS, MCP_TOOL_MAP } from "../constants";
import { callMcpTool, listMcpTools, extractMcpText } from "../services/mcp";
import { Button, Badge, Card, Input, Spinner } from "./ui";

export default function ToolsConsole({ mcpUrl }) {
  const [running, setRunning] = useState(null);
  const [logs, setLogs] = useState([]);
  const [ragQuestion, setRagQuestion] = useState("");
  const [serverStatus, setServerStatus] = useState("unknown");
  const [availableTools, setAvailableTools] = useState([]);
  const [confirmDialog, setConfirmDialog] = useState(null);
  const [toolArgs, setToolArgs] = useState({});
  const logsEndRef = useRef(null);

  const checkServerStatus = useCallback(async () => {
    try {
      setAvailableTools(await listMcpTools(mcpUrl));
      setServerStatus("online");
    } catch {
      setServerStatus("offline");
      setAvailableTools([]);
    }
  }, [mcpUrl]);

  useEffect(() => { checkServerStatus(); }, [checkServerStatus]);
  useEffect(() => { logsEndRef.current?.scrollIntoView({ behavior: "smooth" }); }, [logs]);

  const addLog = (type, text) => setLogs(prev => [...prev, { time: new Date().toLocaleTimeString("zh-CN"), type, text }]);

  const executeTool = useCallback(async (toolId, extraArgs = {}) => {
    const mapping = MCP_TOOL_MAP[toolId];
    if (!mapping) { addLog("error", `未找到工具映射: ${toolId}`); return; }

    const tool = TOOLS.find(t => t.id === toolId);
    const base = mapping.defaultArgs || {};
    const custom = toolArgs[toolId] || {};
    const args = {
      ...base, ...custom, ...extraArgs,
      ...(base.params || custom.params || extraArgs.params ? {
        params: { ...(base.params || {}), ...(custom.params || {}), ...(extraArgs.params || {}) }
      } : {}),
    };
    if (toolId === "rag") {
      if (!ragQuestion.trim()) return;
      args.params = { question: ragQuestion.trim() };
    }

    setRunning(toolId);
    addLog("info", `▶ 开始执行: ${tool.name}\n  MCP 工具: ${mapping.mcpTool}\n  参数: ${JSON.stringify(args)}`);

    try {
      const text = extractMcpText(await callMcpTool(mapping.mcpTool, args, mcpUrl));
      const isErr = /^(Error|错误)[:\s：]/.test(text) || text.includes("validation error");
      addLog(isErr ? "error" : "success", `${isErr ? "✗" : "✓"} ${tool.name} 执行${isErr ? "出错" : "完成"}:\n${text}`);
      if (toolId === "rag") setRagQuestion("");
    } catch (err) {
      addLog("error", `✗ ${tool.name} 执行失败:\n${err.message}`);
    } finally { setRunning(null); }
  }, [mcpUrl, ragQuestion, toolArgs]);

  const runTool = (toolId) => {
    const tool = TOOLS.find(t => t.id === toolId);
    if (tool?.danger) { setConfirmDialog({ toolId, toolName: tool.name }); return; }
    executeTool(toolId);
  };

  const confirmAndRun = (confirmed) => {
    if (confirmed && confirmDialog) {
      const confirmArgs = { move: { params: { confirm: true } }, clean: { params: { confirm: true } }, pipeline: { params: { confirm_move: true } } };
      executeTool(confirmDialog.toolId, confirmArgs[confirmDialog.toolId] || {});
    }
    setConfirmDialog(null);
  };

  const statusColor = { online: T.success, offline: T.danger, unknown: T.warning }[serverStatus];
  const statusGlow = { online: T.successGlow, offline: T.dangerGlow, unknown: `${T.warning}15` }[serverStatus];

  return (
    <div style={{ animation: "fadeIn 0.4s ease" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "6px" }}>
        <h2 style={{ fontSize: "20px", fontWeight: 700, color: T.text }}>工具控制台</h2>
        <div onClick={checkServerStatus} title="点击刷新连接状态"
          style={{
            display: "flex", alignItems: "center", gap: "6px",
            padding: "6px 14px", borderRadius: "20px", cursor: "pointer",
            background: statusGlow, border: `1px solid ${statusColor}40`, transition: "all 0.2s",
          }}>
          <div style={{ width: "8px", height: "8px", borderRadius: "50%", background: statusColor, animation: serverStatus === "online" ? "pulse 2s infinite" : "none" }} />
          <span style={{ fontSize: "12px", fontWeight: 500, color: statusColor }}>
            {serverStatus === "online" ? `MCP 已连接 (${availableTools.length} 工具)` : serverStatus === "offline" ? "MCP 未连接" : "检测中..."}
          </span>
        </div>
      </div>
      <p style={{ fontSize: "13px", color: T.textMuted, marginBottom: "24px" }}>调用 Invoice Toolkit MCP Server · {mcpUrl}</p>

      {/* 确认对话框 */}
      {confirmDialog && (
        <div style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.6)", zIndex: 1000, display: "flex", alignItems: "center", justifyContent: "center", animation: "fadeIn 0.2s ease" }}>
          <Card style={{ maxWidth: "400px", padding: "28px", textAlign: "center" }}>
            <div style={{ fontSize: "40px", marginBottom: "16px" }}>⚠️</div>
            <div style={{ fontSize: "16px", fontWeight: 600, color: T.text, marginBottom: "8px" }}>确认执行「{confirmDialog.toolName}」？</div>
            <div style={{ fontSize: "13px", color: T.textMuted, marginBottom: "24px" }}>此操作可能修改文件系统或删除数据，请确认。</div>
            <div style={{ display: "flex", gap: "12px", justifyContent: "center" }}>
              <Button variant="secondary" onClick={() => confirmAndRun(false)}>取消</Button>
              <Button variant="danger" onClick={() => confirmAndRun(true)}>确认执行</Button>
            </div>
          </Card>
        </div>
      )}

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "16px" }}>
        {/* 工具网格 */}
        <div>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "10px" }}>
            {TOOLS.filter(t => t.id !== "rag").map((tool, i) => {
              const mcpAvailable = availableTools.some(t => t.name === MCP_TOOL_MAP[tool.id]?.mcpTool);
              const isDisabled = running || serverStatus === "offline";
              return (
                <Card key={tool.id} hover onClick={() => !isDisabled && runTool(tool.id)}
                  style={{ padding: "16px", cursor: isDisabled ? "not-allowed" : "pointer", opacity: isDisabled && running !== tool.id ? 0.5 : 1, animation: `fadeInUp 0.3s ease ${i * 0.04}s both` }}>
                  <div style={{ display: "flex", alignItems: "center", gap: "10px", marginBottom: "8px" }}>
                    <span style={{ fontSize: "22px" }}>{tool.icon}</span>
                    <span style={{ fontSize: "13px", fontWeight: 600, color: T.text }}>{tool.name}</span>
                    {running === tool.id && <Spinner size={14} />}
                  </div>
                  <div style={{ fontSize: "11px", color: T.textMuted, lineHeight: 1.5 }}>{tool.desc}</div>
                  <div style={{ marginTop: "8px", display: "flex", gap: "4px", flexWrap: "wrap" }}>
                    {tool.danger && <Badge color={T.warning}>⚠ 需确认</Badge>}
                    {serverStatus === "online" && <Badge color={mcpAvailable ? T.success : T.textMuted}>{mcpAvailable ? "✓ 可用" : "✗ 未注册"}</Badge>}
                  </div>
                </Card>
              );
            })}
          </div>

          {/* RAG */}
          <Card style={{ marginTop: "10px", padding: "16px" }}>
            <div style={{ display: "flex", alignItems: "center", gap: "10px", marginBottom: "10px" }}>
              <span style={{ fontSize: "22px" }}>💬</span>
              <span style={{ fontSize: "13px", fontWeight: 600, color: T.text }}>报销政策智能问答 (RAG)</span>
              {serverStatus === "online" && (
                <Badge color={availableTools.some(t => t.name === "invoice_query_policy") ? T.success : T.textMuted}>
                  {availableTools.some(t => t.name === "invoice_query_policy") ? "✓ 可用" : "✗ 未注册"}
                </Badge>
              )}
            </div>
            <div style={{ display: "flex", gap: "8px" }}>
              <Input value={ragQuestion} onChange={setRagQuestion} placeholder="例：出差报销需要哪些材料？票据丢失怎么办？"
                style={{ flex: 1, fontSize: "13px" }} onKeyDown={e => e.key === "Enter" && runTool("rag")} />
              <Button onClick={() => runTool("rag")} disabled={!ragQuestion.trim() || running === "rag" || serverStatus === "offline"}>
                {running === "rag" ? <Spinner size={14} /> : "提问"}
              </Button>
            </div>
          </Card>
        </div>

        {/* 日志面板 */}
        <Card style={{ padding: "0", display: "flex", flexDirection: "column", maxHeight: "calc(100vh - 200px)" }}>
          <div style={{ padding: "12px 16px", borderBottom: `1px solid ${T.border}`, display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <span style={{ fontSize: "13px", fontWeight: 600, color: T.text }}>执行日志</span>
            <button onClick={() => setLogs([])} style={{ background: "none", border: "none", color: T.textMuted, cursor: "pointer", fontSize: "12px", fontFamily: T.font }}>清空</button>
          </div>
          <div style={{ flex: 1, overflowY: "auto", padding: "12px 16px" }}>
            {!logs.length ? (
              <div style={{ textAlign: "center", padding: "40px 0", color: T.textMuted, fontSize: "13px" }}>
                {serverStatus === "offline" ? "⚠ MCP Server 未连接，请先启动服务器" : "点击工具卡片开始执行"}
              </div>
            ) : logs.map((log, i) => (
              <div key={i} style={{ marginBottom: "12px", animation: "fadeIn 0.3s ease", borderLeft: `2px solid ${LOG_COLORS[log.type] || T.info}`, paddingLeft: "12px" }}>
                <div style={{ fontSize: "10px", color: T.textMuted, fontFamily: T.mono, marginBottom: "4px" }}>{log.time}</div>
                <div style={{ fontSize: "12px", fontFamily: T.mono, color: LOG_COLORS[log.type] || T.text, whiteSpace: "pre-wrap", lineHeight: 1.6 }}>{log.text}</div>
              </div>
            ))}
            <div ref={logsEndRef} />
          </div>
        </Card>
      </div>
    </div>
  );
}
