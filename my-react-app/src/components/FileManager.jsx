import { useState, useRef, useCallback } from "react";
import { T } from "../theme";
import { NAME_LIST } from "../constants";
import { fileIcon } from "../utils/helpers";
import { saveData } from "../services/storage";
import { fetchMemberFiles } from "../services/data";
import { Button, Card, Badge, EmptyState, PageHeader, Spinner } from "./ui";

/**
 * 文件管理页面
 *
 * 功能：
 *   - 左侧成员目录树（管理员可见全部成员）
 *   - 右侧文件列表 + 拖拽上传区
 *   - 支持从 MCP 后端（data/课题组成员文件/）实时加载文件
 *   - 支持文件上传（模拟）和删除（本地 overlay）
 *
 * @param {object}   user       - 当前用户
 * @param {Array}    files      - 文件列表（全局状态）
 * @param {function} setFiles   - 更新文件列表
 * @param {string}   mcpUrl     - MCP Server 地址
 * @param {string}   dataSource - 数据来源 "mcp" | "local"
 * @param {function} onRefresh  - 手动刷新远程数据
 */
export default function FileManager({ user, files, setFiles, mcpUrl, dataSource, onRefresh }) {
  const isAdmin = user.role === "admin";
  const [selectedFolder, setSelectedFolder] = useState(
    isAdmin ? null : user.name
  );
  const [dragOver, setDragOver] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const fileInputRef = useRef(null);

  const folders = isAdmin ? NAME_LIST : [user.name];
  const visibleFiles = selectedFolder
    ? files.filter((f) => f.owner === selectedFolder)
    : files;
  const canEdit = isAdmin || selectedFolder === user.name;

  /**
   * 手动从 MCP 刷新文件列表
   */
  const handleSync = useCallback(async () => {
    setSyncing(true);
    try {
      const result = await fetchMemberFiles(mcpUrl);
      if (!result.error && result.files.length > 0) {
        setFiles(result.files);
        saveData("invoice-files", result.files);
      } else if (onRefresh) {
        await onRefresh();
      }
    } finally {
      setSyncing(false);
    }
  }, [mcpUrl, setFiles, onRefresh]);

  const handleUpload = (fileList) => {
    const owner = selectedFolder || user.name;
    const now = new Date().toLocaleString("zh-CN");
    const newFiles = Array.from(fileList).map((f) => ({
      id: `f_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`,
      name: f.name,
      size: f.size,
      owner,
      category: "未分类",
      uploadTime: now,
    }));
    const updated = [...files, ...newFiles];
    setFiles(updated);
    saveData("invoice-files", updated);
  };

  const handleDelete = (fileId) => {
    const updated = files.filter((f) => f.id !== fileId);
    setFiles(updated);
    saveData("invoice-files", updated);
  };

  return (
    <div style={{ animation: "fadeIn 0.4s ease" }}>
      <PageHeader
        title={isAdmin ? "文件管理" : "我的文件"}
        subtitle={
          isAdmin
            ? `管理所有成员的发票文件${dataSource === "mcp" ? "（已连接后端目录）" : "（本地模式）"}`
            : "管理您的发票文件"
        }
      >
        {/* 同步按钮 */}
        <Button variant="secondary" onClick={handleSync} disabled={syncing}>
          {syncing ? <Spinner size={14} /> : "🔄"} 同步后端
        </Button>

        {canEdit && (
          <Button onClick={() => fileInputRef.current?.click()}>
            ＋ 上传文件
          </Button>
        )}
        <input
          ref={fileInputRef}
          type="file"
          multiple
          style={{ display: "none" }}
          onChange={(e) => {
            if (e.target.files.length) handleUpload(e.target.files);
            e.target.value = "";
          }}
        />
      </PageHeader>

      {/* 数据来源提示条 */}
      {dataSource === "mcp" && (
        <div style={{
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
        }}>
          <span style={{ width: "6px", height: "6px", borderRadius: "50%", background: T.success }} />
          数据来源：MCP 后端 · data/课题组成员文件/
        </div>
      )}

      <div style={{ display: "flex", gap: "16px" }}>
        {/* 左侧成员目录 */}
        {isAdmin && (
          <Card
            style={{
              width: "200px",
              padding: "12px",
              flexShrink: 0,
              maxHeight: "calc(100vh - 180px)",
              overflowY: "auto",
            }}
          >
            <div
              style={{
                fontSize: "12px",
                fontWeight: 600,
                color: T.textMuted,
                padding: "4px 8px",
                marginBottom: "8px",
                textTransform: "uppercase",
                letterSpacing: "0.5px",
              }}
            >
              课题组成员
            </div>
            {[
              { name: "📂 全部文件", key: null, count: files.length },
              ...folders.map((name) => ({
                name,
                key: name,
                count: files.filter((f) => f.owner === name).length,
              })),
            ].map((item) => {
              const active = selectedFolder === item.key;
              return (
                <button
                  key={item.key ?? "__all"}
                  onClick={() => setSelectedFolder(item.key)}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: "8px",
                    width: "100%",
                    padding: item.key === null ? "8px 10px" : "7px 10px",
                    border: "none",
                    borderRadius: T.radiusSm,
                    background: active ? T.accentGlow : "transparent",
                    color: active ? T.accentLight : T.textSecondary,
                    fontFamily: T.font,
                    fontSize: item.key === null ? "13px" : "12px",
                    fontWeight: active ? 600 : 400,
                    cursor: "pointer",
                    textAlign: "left",
                    marginBottom: item.key === null ? "4px" : "1px",
                  }}
                >
                  {item.key !== null && (
                    <span
                      style={{
                        width: "20px",
                        height: "20px",
                        borderRadius: "50%",
                        background: `${T.accent}25`,
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "center",
                        fontSize: "10px",
                        flexShrink: 0,
                      }}
                    >
                      {item.name[0]}
                    </span>
                  )}
                  {item.name}
                  {item.count > 0 && (
                    <span
                      style={{
                        marginLeft: "auto",
                        fontSize: item.key === null ? "11px" : "10px",
                        opacity: item.key === null ? 0.6 : 0.5,
                      }}
                    >
                      {item.count}
                    </span>
                  )}
                </button>
              );
            })}
          </Card>
        )}

        {/* 右侧文件区 */}
        <div style={{ flex: 1 }}>
          {/* 拖拽上传区 */}
          {canEdit && (
            <div
              onDragOver={(e) => {
                e.preventDefault();
                setDragOver(true);
              }}
              onDragLeave={() => setDragOver(false)}
              onDrop={(e) => {
                e.preventDefault();
                setDragOver(false);
                if (e.dataTransfer.files.length)
                  handleUpload(e.dataTransfer.files);
              }}
              style={{
                border: `2px dashed ${dragOver ? T.accent : T.border}`,
                borderRadius: T.radius,
                padding: "28px",
                textAlign: "center",
                marginBottom: "16px",
                transition: "all 0.2s",
                background: dragOver ? T.accentGlow : "transparent",
              }}
            >
              <div style={{ fontSize: "28px", marginBottom: "8px" }}>📥</div>
              <div
                style={{
                  fontSize: "14px",
                  color: dragOver ? T.accentLight : T.textSecondary,
                  fontWeight: 500,
                }}
              >
                拖拽文件到此处上传
              </div>
              <div
                style={{ fontSize: "12px", color: T.textMuted, marginTop: "4px" }}
              >
                {selectedFolder
                  ? `上传到：${selectedFolder}`
                  : "请先选择成员文件夹"}
              </div>
            </div>
          )}

          {/* 文件列表 */}
          {!visibleFiles.length ? (
            <EmptyState
              icon="📂"
              title="文件夹为空"
              subtitle={
                canEdit ? "拖拽或点击上传添加文件，或点击「同步后端」拉取服务器文件" : "此文件夹暂无文件"
              }
            />
          ) : (
            <div style={{ display: "grid", gap: "6px" }}>
              {visibleFiles.map((f, i) => (
                <div
                  key={f.id}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: "12px",
                    padding: "12px 16px",
                    background: T.card,
                    borderRadius: T.radiusSm,
                    border: `1px solid ${T.border}`,
                    animation: `fadeInUp 0.3s ease ${i * 0.03}s both`,
                  }}
                >
                  <span style={{ fontSize: "22px" }}>{fileIcon(f.name)}</span>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div
                      style={{
                        fontSize: "13px",
                        fontWeight: 500,
                        color: T.text,
                      }}
                    >
                      {f.name}
                    </div>
                    <div
                      style={{
                        fontSize: "11px",
                        color: T.textMuted,
                        marginTop: "2px",
                      }}
                    >
                      {f.owner} · {f.uploadTime}
                      {f.size ? ` · ${(f.size / 1024).toFixed(1)}KB` : ""}
                    </div>
                  </div>
                  <Badge>{f.category}</Badge>
                  {/* 后端文件标记 */}
                  {f.fullPath && (
                    <span style={{ fontSize: "10px", color: T.textMuted, opacity: 0.6 }} title={f.fullPath}>🔗</span>
                  )}
                  {(isAdmin || f.owner === user.name) && !f.fullPath && (
                    <button
                      onClick={() => handleDelete(f.id)}
                      style={{
                        background: "none",
                        border: "none",
                        color: T.textMuted,
                        cursor: "pointer",
                        fontSize: "14px",
                        padding: "4px 8px",
                        borderRadius: T.radiusSm,
                        transition: "all 0.15s",
                      }}
                      onMouseEnter={(e) => {
                        e.target.style.color = T.danger;
                        e.target.style.background = T.dangerGlow;
                      }}
                      onMouseLeave={(e) => {
                        e.target.style.color = T.textMuted;
                        e.target.style.background = "none";
                      }}
                    >
                      ✕
                    </button>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* 底部统计 */}
      <div style={{ marginTop: "12px", fontSize: "12px", color: T.textMuted, display: "flex", justifyContent: "space-between" }}>
        <span>共 {visibleFiles.length} 个文件（总计 {files.length} 个）</span>
        <span>
          后端文件: {files.filter(f => f.fullPath).length} · 本地文件: {files.filter(f => !f.fullPath).length}
        </span>
      </div>
    </div>
  );
}
