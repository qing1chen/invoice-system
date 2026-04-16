import { T } from "../../theme";

/**
 * 空状态占位组件
 *
 * @param {string} icon     - emoji 图标
 * @param {string} title    - 主文案
 * @param {string} subtitle - 副文案（可选）
 */
export default function EmptyState({ icon, title, subtitle }) {
  return (
    <div style={{ textAlign: "center", padding: "60px 20px", color: T.textMuted }}>
      <div style={{ fontSize: "48px", marginBottom: "16px", opacity: 0.5 }}>
        {icon}
      </div>
      <div
        style={{
          fontSize: "16px",
          fontWeight: 500,
          color: T.textSecondary,
          marginBottom: "8px",
        }}
      >
        {title}
      </div>
      {subtitle && <div style={{ fontSize: "13px" }}>{subtitle}</div>}
    </div>
  );
}
