import { T } from "../../theme";

// ─── 按钮变体 & 尺寸 ──────────────────────────────────────

const VARIANTS = {
  primary:   { background: T.accent, color: "#fff", border: "none" },
  secondary: { background: "transparent", color: T.textSecondary, border: `1px solid ${T.border}` },
  danger:    { background: T.danger, color: "#fff", border: "none" },
  ghost:     { background: "transparent", color: T.textSecondary, border: "none" },
  success:   { background: T.success, color: "#fff", border: "none" },
};

const SIZES = {
  sm: { padding: "6px 14px", fontSize: "12px" },
  md: { padding: "9px 20px", fontSize: "13px" },
  lg: { padding: "12px 28px", fontSize: "14px" },
};

/**
 * 通用按钮组件
 *
 * @param {string}  variant  - primary | secondary | danger | ghost | success
 * @param {string}  size     - sm | md | lg
 * @param {boolean} disabled - 禁用状态
 */
export default function Button({
  children,
  onClick,
  variant = "primary",
  size = "md",
  disabled,
  style,
  ...rest
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      {...rest}
      style={{
        ...VARIANTS[variant],
        ...SIZES[size],
        borderRadius: T.radiusSm,
        fontFamily: T.font,
        fontWeight: 500,
        cursor: disabled ? "not-allowed" : "pointer",
        opacity: disabled ? 0.5 : 1,
        transition: "all 0.2s ease",
        display: "inline-flex",
        alignItems: "center",
        gap: "6px",
        whiteSpace: "nowrap",
        ...style,
      }}
      onMouseEnter={(e) => {
        if (!disabled) e.target.style.filter = "brightness(1.15)";
      }}
      onMouseLeave={(e) => {
        e.target.style.filter = "brightness(1)";
      }}
    >
      {children}
    </button>
  );
}
