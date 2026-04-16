import { useState } from "react";
import { T } from "../../theme";

/**
 * 卡片容器组件
 *
 * @param {boolean}  hover   - 是否启用悬停高亮
 * @param {function} onClick - 点击回调（传入后自动显示 pointer 光标）
 */
export default function Card({ children, style, hover, onClick }) {
  const [hovered, setHovered] = useState(false);

  return (
    <div
      onClick={onClick}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        background: hovered && hover ? T.cardHover : T.card,
        border: `1px solid ${hovered && hover ? T.borderHover : T.border}`,
        borderRadius: T.radius,
        padding: "20px",
        transition: "all 0.25s ease",
        cursor: onClick ? "pointer" : "default",
        ...style,
      }}
    >
      {children}
    </div>
  );
}
