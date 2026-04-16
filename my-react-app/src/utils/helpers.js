/**
 * 通用工具函数
 *
 * 包含数据生成器、文件图标映射、全局样式注入、
 * 以及共用的内联编辑样式对象。
 */

import { NAME_LIST, CATEGORIES } from "../constants";
import { T } from "../theme";

// ─── 默认示例数据生成器 ────────────────────────────────────

/**
 * 生成 8 条示例报销记录，用于首次加载展示
 * @returns {Array<object>} 报销记录数组
 */
export function generateDefaultRows() {
  const usages = ["差旅费", "加班餐费", "快递费", "打印费", "打车费", "材料费"];
  return NAME_LIST.slice(0, 8).map((name, i) => ({
    id: `r${i}`,
    序号: i + 1,
    "姓名/公司": name,
    日期: "2025-03-01",
    金额: (Math.random() * 500 + 50).toFixed(2),
    用途: usages[i % 6],
    备注: "",
    类别: CATEGORIES[i % CATEGORIES.length],
    状态: "待报销",
  }));
}

// ─── 文件类型图标 ──────────────────────────────────────────

/**
 * 根据文件名后缀返回 emoji 图标
 * @param {string} name - 文件名
 * @returns {string} emoji
 */
export function fileIcon(name) {
  if (name.endsWith(".pdf")) return "📕";
  if (/\.(png|jpg|jpeg)$/i.test(name)) return "🖼️";
  if (name.endsWith(".xlsx")) return "📗";
  return "📄";
}

// ─── 全局动画样式注入 ──────────────────────────────────────

/**
 * 向 <head> 注入全局 CSS（字体、关键帧动画、滚动条样式）
 * 使用 id 守卫确保只注入一次
 */
export function injectStyles() {
  if (document.getElementById("invoice-tk-styles")) return;
  const s = document.createElement("style");
  s.id = "invoice-tk-styles";
  s.textContent = `
    @import url('https://fonts.googleapis.com/css2?family=Noto+Sans+SC:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap');
    @keyframes fadeInUp   { from { opacity:0; transform:translateY(20px); } to { opacity:1; transform:translateY(0); } }
    @keyframes fadeIn     { from { opacity:0; } to { opacity:1; } }
    @keyframes slideInRight { from { opacity:0; transform:translateX(30px); } to { opacity:1; transform:translateX(0); } }
    @keyframes pulse      { 0%,100% { opacity:1; } 50% { opacity:.6; } }
    @keyframes spin       { from { transform:rotate(0deg); } to { transform:rotate(360deg); } }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    ::-webkit-scrollbar        { width: 6px; height: 6px; }
    ::-webkit-scrollbar-track  { background: transparent; }
    ::-webkit-scrollbar-thumb  { background: rgba(100,116,139,0.3); border-radius: 3px; }
    ::-webkit-scrollbar-thumb:hover { background: rgba(100,116,139,0.5); }
  `;
  document.head.appendChild(s);
}

// ─── 表格内联编辑输入框样式 ────────────────────────────────

/** 供 CellEditor 组件使用的通用样式对象 */
export const cellEditStyle = {
  width: "100%",
  padding: "6px 8px",
  background: T.bg,
  border: `1px solid ${T.accent}`,
  borderRadius: "4px",
  color: T.text,
  fontFamily: T.font,
  fontSize: "13px",
  outline: "none",
};
