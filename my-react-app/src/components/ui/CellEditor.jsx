import { CATEGORIES, STATUS_LIST } from "../../constants";
import { cellEditStyle } from "../../utils/helpers";

/**
 * 表格单元格内联编辑控件
 *
 * 根据列名自动切换 <select> / <input>：
 *   - "类别" → 下拉（CATEGORIES）
 *   - "状态" → 下拉（STATUS_LIST）
 *   - "金额" → number 输入
 *   - "日期" → date 输入
 *   - 其他  → text 输入
 *
 * @param {string}   col      - 列名
 * @param {string}   value    - 当前值
 * @param {function} onChange - 值变化回调
 * @param {function} onCommit - 确认编辑（失焦 / 回车）
 */
export default function CellEditor({ col, value, onChange, onCommit }) {
  const selectOptions =
    col === "类别" ? ["", ...CATEGORIES] : col === "状态" ? STATUS_LIST : null;

  if (selectOptions) {
    return (
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onBlur={onCommit}
        autoFocus
        style={cellEditStyle}
      >
        {col === "类别" && <option value="">未分类</option>}
        {(col === "类别" ? CATEGORIES : STATUS_LIST).map((o) => (
          <option key={o} value={o}>
            {o}
          </option>
        ))}
      </select>
    );
  }

  return (
    <input
      type={col === "金额" ? "number" : col === "日期" ? "date" : "text"}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      onBlur={onCommit}
      onKeyDown={(e) => e.key === "Enter" && onCommit()}
      autoFocus
      style={cellEditStyle}
    />
  );
}
