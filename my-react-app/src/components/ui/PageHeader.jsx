import { T } from "../../theme";

/**
 * 页面标题栏组件
 *
 * 左侧显示标题 + 副标题，右侧通过 children 放置操作按钮
 */
export default function PageHeader({ title, subtitle, children }) {
  return (
    <div
      style={{
        display: "flex",
        justifyContent: "space-between",
        alignItems: "center",
        marginBottom: "20px",
      }}
    >
      <div>
        <h2 style={{ fontSize: "20px", fontWeight: 700, color: T.text }}>
          {title}
        </h2>
        {subtitle && (
          <p style={{ fontSize: "13px", color: T.textMuted, marginTop: "4px" }}>
            {subtitle}
          </p>
        )}
      </div>
      {children && (
        <div style={{ display: "flex", gap: "10px", alignItems: "center" }}>
          {children}
        </div>
      )}
    </div>
  );
}
