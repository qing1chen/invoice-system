// ─── 主题配色 & 排版 ────────────────────────────────────────
export const T = {
  bg: "#0f1117", surface: "#161922", surfaceHover: "#1c2030",
  card: "#1a1e2e", cardHover: "#1f2440",
  border: "rgba(99,115,146,0.15)", borderHover: "rgba(99,115,146,0.3)",
  text: "#e2e8f0", textSecondary: "#8892a8", textMuted: "#5a6478",
  accent: "#6366f1", accentGlow: "rgba(99,102,241,0.15)", accentLight: "#818cf8",
  success: "#22c55e", successGlow: "rgba(34,197,94,0.15)",
  warning: "#f59e0b", warningGlow: "rgba(245,158,11,0.15)",
  danger: "#ef4444", dangerGlow: "rgba(239,68,68,0.15)",
  info: "#3b82f6",
  font: "'Noto Sans SC', system-ui, sans-serif",
  mono: "'JetBrains Mono', monospace",
  radius: "10px", radiusSm: "6px",
  shadow: "0 4px 24px rgba(0,0,0,0.25)",
};

// ─── 状态颜色映射 ───────────────────────────────────────────
export const STATUS_COLORS = {
  "待报销": T.warning,
  "审核中": T.info,
  "已报销": T.success,
  "已驳回": T.danger,
};

// ─── 日志颜色映射 ───────────────────────────────────────────
export const LOG_COLORS = {
  success: T.success,
  error: T.danger,
  info: T.info,
};