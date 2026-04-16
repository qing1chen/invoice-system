import { T } from "../../theme";

/**
 * 标签徽章组件
 *
 * @param {string} color - 徽章主色（默认为 accent）
 */
export default function Badge({ children, color = T.accent }) {
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        padding: "2px 10px",
        borderRadius: "20px",
        fontSize: "11px",
        fontWeight: 600,
        background: `${color}22`,
        color,
        letterSpacing: "0.3px",
      }}
    >
      {children}
    </span>
  );
}
