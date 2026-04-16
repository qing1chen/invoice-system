/**
 * InvoiceApp — 主应用（已增加智能填报）
 * 文件位置：src/components/InvoiceApp.jsx
 *
 * 改动（仅 3 处，用 ← 标注）：
 *   1. import ReimbursementAgent
 *   2. activeTab === "agent" 渲染 ReimbursementAgent
 *   3. 传入 mcpUrl
 */
import { useState, useEffect, useCallback } from "react";
import { T } from "../theme";
import { DEFAULT_MCP_URL } from "../constants";
import { loadData, saveData } from "../services/storage";
import { fetchMemberFiles, fetchTableData } from "../services/data";
import { generateDefaultRows, injectStyles } from "../utils/helpers";
import { Spinner } from "./ui";
import LoginPage from "./LoginPage";
import Sidebar from "./Sidebar";
import Dashboard from "./Dashboard";
import FileManager from "./FileManager";
import TableEditor from "./TableEditor";
import ToolsConsole from "./ToolsConsole";
import MembersPanel from "./MembersPanel";
import ServerSettings from "./ServerSettings";
import ReimbursementAgent from "./ReimbursementAgent";  // ← 新增

export default function InvoiceApp() {
  const [user, setUser] = useState(null);
  const [activeTab, setActiveTab] = useState("dashboard");
  const [files, setFiles] = useState([]);
  const [tableData, setTableData] = useState([]);
  const [loading, setLoading] = useState(true);
  const [mcpUrl, setMcpUrl] = useState(DEFAULT_MCP_URL);
  const [dataSource, setDataSource] = useState("local");

  const loadRemoteData = useCallback(async (serverUrl) => {
    const fileResult = await fetchMemberFiles(serverUrl);
    if (!fileResult.error && fileResult.files.length > 0) {
      setFiles(fileResult.files);
      saveData("invoice-files", fileResult.files);
    } else {
      setFiles(loadData("invoice-files", []));
    }
    const tableResult = await fetchTableData(serverUrl);
    if (!tableResult.error && tableResult.rows.length > 0) {
      setTableData(tableResult.rows);
      saveData("invoice-table", tableResult.rows);
      setDataSource("mcp");
    } else {
      const localTable = loadData("invoice-table", null);
      setTableData(localTable || generateDefaultRows());
      setDataSource("local");
    }
  }, []);

  useEffect(() => {
    injectStyles();
    const savedUser = loadData("invoice-user", null);
    const savedMcpUrl = loadData("invoice-mcp-url", DEFAULT_MCP_URL);
    if (savedUser) setUser(savedUser);
    setMcpUrl(savedMcpUrl);
    setFiles(loadData("invoice-files", []));
    setTableData(loadData("invoice-table", null) || generateDefaultRows());
    setLoading(false);
    loadRemoteData(savedMcpUrl);
  }, [loadRemoteData]);

  const refreshRemoteData = useCallback(() => loadRemoteData(mcpUrl), [mcpUrl, loadRemoteData]);
  const handleLogin = (u) => { setUser(u); saveData("invoice-user", u); setActiveTab("dashboard"); loadRemoteData(mcpUrl); };
  const handleLogout = () => { setUser(null); saveData("invoice-user", null); setActiveTab("dashboard"); };

  if (loading) {
    return (
      <div style={{ minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center", background: T.bg, fontFamily: T.font }}>
        <Spinner size={32} />
      </div>
    );
  }
  if (!user) return <LoginPage onLogin={handleLogin} />;

  return (
    <div style={{ display: "flex", minHeight: "100vh", background: T.bg, fontFamily: T.font }}>
      <Sidebar user={user} activeTab={activeTab} onTabChange={setActiveTab} onLogout={handleLogout} />
      <main style={{ flex: 1, padding: "28px 32px", overflowY: "auto", minHeight: "100vh" }}>
        {activeTab === "dashboard" && <Dashboard user={user} files={files} tableData={tableData} />}
        {activeTab === "files" && <FileManager user={user} files={files} setFiles={setFiles} mcpUrl={mcpUrl} dataSource={dataSource} onRefresh={refreshRemoteData} />}
        {activeTab === "table" && <TableEditor user={user} tableData={tableData} setTableData={setTableData} mcpUrl={mcpUrl} dataSource={dataSource} onRefresh={refreshRemoteData} />}
        {/* ← 新增：智能填报 */}
        {activeTab === "agent" && <ReimbursementAgent tableData={tableData} user={user} mcpUrl={mcpUrl} />}
        {activeTab === "tools" && user.role === "admin" && <ToolsConsole mcpUrl={mcpUrl} />}
        {activeTab === "members" && user.role === "admin" && <MembersPanel files={files} tableData={tableData} />}
        {activeTab === "settings" && user.role === "admin" && <ServerSettings mcpUrl={mcpUrl} setMcpUrl={setMcpUrl} />}
      </main>
    </div>
  );
}
