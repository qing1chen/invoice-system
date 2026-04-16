import { T } from "../../theme";

/**
 * 文本输入框组件
 *
 * onChange 回调直接传递 value 字符串（而非原生 event）
 */
export default function Input({
  value,
  onChange,
  placeholder,
  type = "text",
  style,
  ...rest
}) {
  return (
    <input
      type={type}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder={placeholder}
      style={{
        width: "100%",
        padding: "10px 14px",
        background: T.surface,
        border: `1px solid ${T.border}`,
        borderRadius: T.radiusSm,
        color: T.text,
        fontFamily: T.font,
        fontSize: "14px",
        outline: "none",
        transition: "border-color 0.2s",
        ...style,
      }}
      onFocus={(e) => (e.target.style.borderColor = T.accent)}
      onBlur={(e) => (e.target.style.borderColor = T.border)}
      {...rest}
    />
  );
}
