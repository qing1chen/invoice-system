import { T } from "../../theme";

/**
 * 加载旋转指示器
 *
 * @param {number} size - 直径（px），默认 20
 */
export default function Spinner({ size = 20 }) {
  return (
    <div
      style={{
        width: size,
        height: size,
        border: `2px solid ${T.border}`,
        borderTopColor: T.accent,
        borderRadius: "50%",
        animation: "spin 0.8s linear infinite",
        flexShrink: 0,
      }}
    />
  );
}
