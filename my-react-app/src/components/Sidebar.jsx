/**
 * Sidebar — 左侧导航栏（增加「智能填报」入口）
 * 文件位置：src/components/Sidebar.jsx
 *
 * 改动：tabs 数组中增加了 { id: "agent", icon: "🤖", label: "智能填报" }
 */
import { T } from "../theme";

export default function Sidebar({ user, activeTab, onTabChange, onLogout }) {
  const isAdmin = user.role === "admin";

  const tabs = [
    { id: "dashboard", icon: "📊", label: "概览" },
    { id: "files",     icon: "📁", label: isAdmin ? "文件管理" : "我的文件" },
    { id: "table",     icon: "📋", label: "报销明细" },
    { id: "agent",     icon: "🤖", label: "智能填报" },   // ← 新增
    ...(isAdmin
      ? [
          { id: "tools",    icon: "⚙️", label: "工具控制台" },
          { id: "members",  icon: "👥", label: "成员管理" },
          { id: "settings", icon: "🔧", label: "服务器配置" },
        ]
      : []),
  ];

  return (
    <div style={{ width: "220px", minHeight: "100vh", background: T.surface, borderRight: `1px solid ${T.border}`, display: "flex", flexDirection: "column", flexShrink: 0 }}>
      <div style={{ padding: "20px 16px", borderBottom: `1px solid ${T.border}` }}>
        <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
          <div style={{ width: "36px", height: "36px", borderRadius: "10px", background: `linear-gradient(135deg, ${T.accent}, ${T.accentLight})`, display: "flex", alignItems: "center", justifyContent: "center", fontSize: "18px", flexShrink: 0 }}>📑</div>
          <div>
            <div style={{ fontSize: "14px", fontWeight: 600, color: T.text }}>发票管理</div>
            <div style={{ fontSize: "11px", color: T.textMuted }}>Invoice Toolkit</div>
          </div>
        </div>
      </div>
      <nav style={{ flex: 1, padding: "12px 8px" }}>
        {tabs.map((tab) => {
          const active = activeTab === tab.id;
          return (
            <button key={tab.id} onClick={() => onTabChange(tab.id)}
              style={{ display: "flex", alignItems: "center", gap: "10px", width: "100%", padding: "10px 12px", border: "none", borderRadius: T.radiusSm, fontFamily: T.font, fontSize: "13px",
                background: active ? T.accentGlow : "transparent", color: active ? T.accentLight : T.textSecondary, fontWeight: active ? 600 : 400, cursor: "pointer", transition: "all 0.15s", marginBottom: "2px", textAlign: "left" }}
              onMouseEnter={(e) => { if (!active) e.currentTarget.style.background = T.surfaceHover; }}
              onMouseLeave={(e) => { if (!active) e.currentTarget.style.background = "transparent"; }}>
              <span style={{ fontSize: "16px" }}>{tab.icon}</span>{tab.label}
            </button>
          );
        })}
      </nav>
      <div style={{ padding: "16px", borderTop: `1px solid ${T.border}`, display: "flex", alignItems: "center", gap: "10px" }}>
        <div style={{ width: "34px", height: "34px", borderRadius: "50%", background: isAdmin ? `linear-gradient(135deg, ${T.warning}, ${T.danger})` : `linear-gradient(135deg, ${T.info}, ${T.accent})`, display: "flex", alignItems: "center", justifyContent: "center", fontSize: "14px", color: "#fff", fontWeight: 600, flexShrink: 0 }}>{user.name[0]}</div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: "13px", fontWeight: 500, color: T.text, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{user.name}</div>
          <div style={{ fontSize: "11px", color: T.textMuted }}>{isAdmin ? "管理员" : "课题组成员"}</div>
        </div>
        <button onClick={onLogout} title="退出登录" style={{ background: "none", border: "none", color: T.textMuted, cursor: "pointer", fontSize: "16px", padding: "4px" }}>⏻</button>
      </div>
    </div>
  );
}
