import { useState } from "react";
import { T } from "../theme";
import { NAME_LIST, ADMIN_USER } from "../constants";
import { Button, Card, Input } from "./ui";

/**
 * 登录页面
 *
 * 提供两种角色登录方式：
 *   - 课题组成员：从名单中选择姓名
 *   - 管理员：输入管理员密码
 *
 * @param {function} onLogin - 登录成功回调，传入 { name, role }
 */
export default function LoginPage({ onLogin }) {
  const [selectedUser, setSelectedUser] = useState("");
  const [password, setPassword] = useState("");
  const [isAdmin, setIsAdmin] = useState(false);
  const [error, setError] = useState("");
  const [search, setSearch] = useState("");

  const filtered = NAME_LIST.filter((n) => n.includes(search));

  const handleLogin = () => {
    if (isAdmin) {
      if (password === ADMIN_USER.password) {
        onLogin({ name: ADMIN_USER.name, role: "admin" });
      } else {
        setError("管理员密码错误");
      }
    } else if (selectedUser) {
      onLogin({ name: selectedUser, role: "user" });
    } else {
      setError("请选择您的姓名");
    }
  };

  return (
    <div
      style={{
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: `radial-gradient(ellipse at 30% 20%, rgba(99,102,241,0.08) 0%, transparent 60%),
                     radial-gradient(ellipse at 70% 80%, rgba(34,197,94,0.05) 0%, transparent 60%), ${T.bg}`,
        fontFamily: T.font,
      }}
    >
      <div
        style={{
          width: "100%",
          maxWidth: "440px",
          padding: "20px",
          animation: "fadeInUp 0.6s ease",
        }}
      >
        {/* 品牌标识 */}
        <div style={{ textAlign: "center", marginBottom: "36px" }}>
          <div
            style={{
              width: "64px",
              height: "64px",
              margin: "0 auto 16px",
              background: `linear-gradient(135deg, ${T.accent}, ${T.accentLight})`,
              borderRadius: "16px",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              fontSize: "28px",
              boxShadow: `0 8px 30px ${T.accentGlow}`,
            }}
          >
            📑
          </div>
          <h1 style={{ fontSize: "22px", fontWeight: 700, color: T.text }}>
            发票报销管理系统
          </h1>
          <p
            style={{ fontSize: "13px", color: T.textMuted, marginTop: "6px" }}
          >
            Invoice Toolkit · MCP Server · 山东大学
          </p>
        </div>

        <Card style={{ padding: "28px" }}>
          {/* 角色切换 */}
          <div
            style={{
              display: "flex",
              background: T.surface,
              borderRadius: T.radiusSm,
              padding: "3px",
              marginBottom: "24px",
              border: `1px solid ${T.border}`,
            }}
          >
            {[false, true].map((admin) => (
              <button
                key={String(admin)}
                onClick={() => {
                  setIsAdmin(admin);
                  setError("");
                }}
                style={{
                  flex: 1,
                  padding: "9px",
                  border: "none",
                  borderRadius: "4px",
                  background: isAdmin === admin ? T.accent : "transparent",
                  color: isAdmin === admin ? "#fff" : T.textSecondary,
                  fontFamily: T.font,
                  fontSize: "13px",
                  fontWeight: 500,
                  cursor: "pointer",
                  transition: "all 0.2s",
                }}
              >
                {admin ? "🔒 管理员" : "👤 课题组成员"}
              </button>
            ))}
          </div>

          {isAdmin ? (
            <div>
              <label
                style={{
                  fontSize: "12px",
                  fontWeight: 500,
                  color: T.textSecondary,
                  display: "block",
                  marginBottom: "8px",
                }}
              >
                管理员密码
              </label>
              <Input
                type="password"
                value={password}
                onChange={(v) => {
                  setPassword(v);
                  setError("");
                }}
                placeholder="请输入管理员密码"
                onKeyDown={(e) => e.key === "Enter" && handleLogin()}
              />
            </div>
          ) : (
            <div>
              <label
                style={{
                  fontSize: "12px",
                  fontWeight: 500,
                  color: T.textSecondary,
                  display: "block",
                  marginBottom: "8px",
                }}
              >
                选择您的姓名
              </label>
              <Input
                value={search}
                onChange={setSearch}
                placeholder="搜索姓名..."
                style={{ marginBottom: "12px" }}
              />
              <div
                style={{
                  maxHeight: "220px",
                  overflowY: "auto",
                  display: "grid",
                  gridTemplateColumns: "1fr 1fr 1fr",
                  gap: "6px",
                }}
              >
                {filtered.map((name) => (
                  <button
                    key={name}
                    onClick={() => {
                      setSelectedUser(name);
                      setError("");
                    }}
                    style={{
                      padding: "8px 4px",
                      border: `1px solid ${selectedUser === name ? T.accent : T.border}`,
                      borderRadius: T.radiusSm,
                      fontSize: "13px",
                      fontFamily: T.font,
                      background:
                        selectedUser === name ? T.accentGlow : "transparent",
                      color:
                        selectedUser === name ? T.accentLight : T.text,
                      cursor: "pointer",
                      transition: "all 0.15s",
                      fontWeight: selectedUser === name ? 600 : 400,
                    }}
                  >
                    {name}
                  </button>
                ))}
              </div>
              {!filtered.length && (
                <div
                  style={{
                    textAlign: "center",
                    padding: "20px",
                    color: T.textMuted,
                    fontSize: "13px",
                  }}
                >
                  未找到匹配的姓名
                </div>
              )}
            </div>
          )}

          {error && (
            <div
              style={{
                marginTop: "12px",
                padding: "8px 12px",
                borderRadius: T.radiusSm,
                background: T.dangerGlow,
                color: T.danger,
                fontSize: "13px",
              }}
            >
              {error}
            </div>
          )}

          <Button
            onClick={handleLogin}
            disabled={!isAdmin && !selectedUser}
            style={{
              width: "100%",
              marginTop: "20px",
              justifyContent: "center",
              padding: "11px",
            }}
            size="lg"
          >
            {isAdmin ? "管理员登录" : `以 ${selectedUser || "..."} 身份进入`}
          </Button>
        </Card>

        <p
          style={{
            textAlign: "center",
            fontSize: "11px",
            color: T.textMuted,
            marginTop: "20px",
          }}
        >
          invoice-toolkit v3.0.0 · MCP Server + RAG
        </p>
      </div>
    </div>
  );
}
