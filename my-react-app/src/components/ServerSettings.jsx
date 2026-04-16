import { useState } from "react";
import { T } from "../theme";
import { TOOLS, MCP_TOOL_MAP } from "../constants";
import { listMcpTools } from "../services/mcp";
import { saveData } from "../services/storage";
import { Button, Badge, Card, Input, Spinner, PageHeader } from "./ui";

export default function ServerSettings({ mcpUrl, setMcpUrl }) {
  const [tempUrl, setTempUrl] = useState(mcpUrl);
  const [testStatus, setTestStatus] = useState(null);
  const [testResult, setTestResult] = useState("");
  const [toolsList, setToolsList] = useState([]);

  const handleTest = async () => {
    setTestStatus("testing"); setTestResult(""); setToolsList([]);
    try {
      const tools = await listMcpTools(tempUrl);
      setToolsList(tools);
      setTestStatus("success");
      setTestResult(`连接成功！发现 ${tools.length} 个 MCP 工具`);
    } catch (err) { setTestStatus("error"); setTestResult(`连接失败: ${err.message}`); }
  };

  const handleSave = () => { setMcpUrl(tempUrl); saveData("invoice-mcp-url", tempUrl); };

  const testColor = { success: T.success, error: T.danger, testing: T.warning }[testStatus];
  const testBg = { success: T.successGlow, error: T.dangerGlow, testing: `${T.warning}15` }[testStatus];

  return (
    <div style={{ animation: "fadeIn 0.4s ease" }}>
      <PageHeader title="服务器配置" subtitle="配置 MCP Server 连接参数" />

      <Card style={{ maxWidth: "600px" }}>
        <div style={{ marginBottom: "20px" }}>
          <label style={{ fontSize: "12px", fontWeight: 600, color: T.textSecondary, display: "block", marginBottom: "8px" }}>MCP Server URL (Streamable HTTP)</label>
          <div style={{ display: "flex", gap: "8px" }}>
            <Input value={tempUrl} onChange={setTempUrl} placeholder="http://localhost:8000" style={{ flex: 1 }} />
            <Button variant="secondary" onClick={handleTest} disabled={testStatus === "testing"}>
              {testStatus === "testing" ? <Spinner size={14} /> : "测试连接"}
            </Button>
            <Button onClick={handleSave}>保存</Button>
          </div>
        </div>

        {testStatus && <div style={{ padding: "12px 16px", borderRadius: T.radiusSm, background: testBg, color: testColor, fontSize: "13px", marginBottom: "16px" }}>{testResult}</div>}

        {toolsList.length > 0 && (
          <div>
            <div style={{ fontSize: "13px", fontWeight: 600, color: T.text, marginBottom: "12px" }}>已注册的 MCP 工具</div>
            <div style={{ display: "grid", gap: "6px" }}>
              {toolsList.map((tool, i) => (
                <div key={i} style={{ padding: "10px 14px", background: T.surface, borderRadius: T.radiusSm, border: `1px solid ${T.border}` }}>
                  <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                    <Badge color={T.accent}>{tool.name}</Badge>
                  </div>
                  {tool.description && (
                    <div style={{ fontSize: "12px", color: T.textMuted, marginTop: "6px", lineHeight: 1.5 }}>{tool.description}</div>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}

        {/* 启动说明 */}
        <div style={{ marginTop: "24px", padding: "16px", background: T.surface, borderRadius: T.radiusSm }}>
          <div style={{ fontSize: "13px", fontWeight: 600, color: T.text, marginBottom: "8px" }}>启动 MCP Server</div>
          <div style={{ fontSize: "12px", color: T.textMuted, lineHeight: 1.8, fontFamily: T.mono }}>
            <div style={{ marginBottom: "4px", color: T.textSecondary }}># 在项目目录下启动 HTTP 模式：</div>
            <div style={{
              padding: "10px 14px", background: T.bg, borderRadius: "4px",
              border: `1px solid ${T.border}`, marginBottom: "8px",
            }}>
              cd D:\Spyder_down\llm\invoice-toolkit<br/>
              conda activate llm<br/>
              python -m invoice_toolkit.mcp_server --transport http --port 8000
            </div>
            <div style={{ color: T.textSecondary, fontSize: "11px" }}>
              服务器启动后，前端会自动通过 Streamable HTTP 协议与 MCP Server 通信。
              <br/>每次工具调用都会经过 initialize → notifications/initialized → tools/call 完整握手流程。
            </div>
          </div>
        </div>

        {/* MCP 工具对应关系 */}
        <div style={{ marginTop: "16px", padding: "16px", background: T.surface, borderRadius: T.radiusSm }}>
          <div style={{ fontSize: "13px", fontWeight: 600, color: T.text, marginBottom: "8px" }}>MCP 工具对应关系</div>
          <div style={{ display: "grid", gap: "4px" }}>
            {TOOLS.map(tool => {
              const mapping = MCP_TOOL_MAP[tool.id];
              return (
                <div key={tool.id} style={{ display: "flex", alignItems: "center", gap: "8px", fontSize: "12px", padding: "4px 0" }}>
                  <span style={{ color: T.text, minWidth: "90px" }}>{tool.icon} {tool.name}</span>
                  <span style={{ color: T.textMuted }}>→</span>
                  <span style={{ fontFamily: T.mono, color: T.accentLight, fontSize: "11px" }}>{mapping?.mcpTool || "无对应"}</span>
                </div>
              );
            })}
          </div>
        </div>
      </Card>
    </div>
  );
}
