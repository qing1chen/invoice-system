// ─── 人员名单（从环境变量 VITE_NAME_LIST 加载）──────────────
// 在 .env 中以英文逗号分隔配置；docker-compose 构建时通过 build args 注入
const _DEFAULT_NAME_LIST = [
  "李四"
];

function _parseNameList(raw) {
  if (!raw || typeof raw !== "string" || !raw.trim()) return _DEFAULT_NAME_LIST;
  // 兼容中文逗号、分号、换行
  const names = raw
    .replace(/[，;；\n]/g, ",")
    .split(",")
    .map((n) => n.trim())
    .filter(Boolean);
  return names.length ? names : _DEFAULT_NAME_LIST;
}

export const NAME_LIST = _parseNameList(import.meta.env.VITE_NAME_LIST);

// ─── 业务常量 ───────────────────────────────────────────────
export const CATEGORIES = [
  "出差","加班餐","快递","打印","打车","材料","论文和专利",
];

export const STATUS_LIST = ["待报销","审核中","已报销","已驳回"];

export const ADMIN_USER = {
  name: "管理员",
  role: "admin",
  password: "admin123",
};

export const TABLE_COLUMNS = [
  "序号","姓名/公司","日期","金额","用途","备注","类别","状态",
];

// ─── 默认 MCP 服务器地址 ────────────────────────────────────
// 修改后（通过 Nginx 代理，自动转发到后端容器）
export const DEFAULT_MCP_URL = '';

// ─── MCP 工具映射表 ────────────────────────────────────────
export const MCP_TOOL_MAP = {
  scan:        { mcpTool: "invoice_scan_directory",     defaultArgs: { params: {} } },
  ocr:         { mcpTool: "invoice_run_ocr",            defaultArgs: {} },
  match:       { mcpTool: "invoice_run_matching",       defaultArgs: {} },
  classify:    { mcpTool: "invoice_run_classification", defaultArgs: {} },
  move:        { mcpTool: "invoice_run_file_move",      defaultArgs: { params: { confirm: false } } },
  check:       { mcpTool: "invoice_check_with_rules",   defaultArgs: { params: { template_name: "rules", dry_run: false } }},
  checkNames:  { mcpTool: "invoice_check_filenames",    defaultArgs: { params: { dry_run: true } } },
  pipeline:    { mcpTool: "invoice_run_pipeline",       defaultArgs: { params: { skip_ocr: false, confirm_move: false } } },
  rag:         { mcpTool: "invoice_query_policy",       defaultArgs: {} },
  clean:       { mcpTool: "invoice_clean_data",         defaultArgs: { params: { confirm: false } } },
  rebuildRag:  { mcpTool: "invoice_rebuild_rag_index",  defaultArgs: {} },
  // ── 数据同步工具（前端 FileManager / TableEditor 内部调用）──
  listFiles:   { mcpTool: "invoice_list_member_files",  defaultArgs: { params: {} } },
  readTable:   { mcpTool: "invoice_read_table",         defaultArgs: {} },
  saveTable:   { mcpTool: "invoice_save_table",         defaultArgs: {} },
};

// ─── 前端工具面板配置 ───────────────────────────────────────
export const TOOLS = [
  { id: "scan",       name: "扫描发票目录", icon: "📂", desc: "扫描 data/课题组成员文件/ 下的文件清单",            danger: false },
  { id: "ocr",        name: "OCR 识别",     icon: "🔍", desc: "对发票文件执行 OCR 识别",                         danger: false },
  { id: "match",      name: "报销匹配",     icon: "🔗", desc: "发票与报销记录智能匹配（含 OCR）",                danger: false },
  { id: "classify",   name: "发票分类",     icon: "🏷️", desc: "按规则对发票文件智能分类（7 个类别）",             danger: false },
  { id: "move",       name: "文件移动",     icon: "📁", desc: "按分类结果移动文件到对应目录",                     danger: true  },
  { id: "check",      name: "附件检查",     icon: "✅", desc: "检查发票附件完整性（行程单/明细/情况说明）",       danger: false },
  { id: "checkNames", name: "文件名检查",   icon: "📝", desc: "检查/修正文件名（报销人+金额+用途）",              danger: false },
  { id: "pipeline",   name: "完整流程",     icon: "🚀", desc: "OCR → 匹配 → 分类 → 移动 → 检查",                danger: true  },
  { id: "rag",        name: "政策问答",     icon: "💬", desc: "基于报销政策文档的智能问答（RAG）",                danger: false },
  { id: "clean",      name: "清理数据",     icon: "🗑️", desc: "清理项目生成的数据（不可撤销）",                   danger: true  },
];
