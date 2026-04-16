/**
 * ReimbursementAgent — 智能填报助手 v10 (LLM 渲染 + Agent 编排)
 * 文件位置：src/components/ReimbursementAgent.jsx
 *
 * v10 改动（删除经典替换，LLM 渲染默认，正则仅做内部备选）：
 *   1. 二段式渲染引擎开关：🤖 LLM 渲染 | 🧠 Agent
 *   2. LLM 渲染为默认模式，正则替换仅在 LLM 失败时自动回退
 *   3. Agent 模式不变：LLM 自主调用工具完成全流程
 *   4. 核心计算逻辑在 Skill 模块：
 *      - skills/reimbursement-prompt/scripts/calculate_amounts.py
 *      - skills/reimbursement-prompt/scripts/agent_orchestrator.py
 *
 * 两种执行流程对比：
 *   🤖 LLM 渲染：前端 calculateTransferAmounts → /api/render-prompt (LLM) → /api/browser-task
 *                （LLM 失败时自动回退正则替换）
 *   🧠 Agent：   前端 → /api/agent-reimbursement → LLM 自主调用工具 → 完成
 */
import { useState, useCallback, useRef, useEffect, useMemo } from 'react';
import { T } from '../theme';
import { Button, Card, Badge, Input, EmptyState, PageHeader, Spinner } from './ui';

// ─── localStorage 键 ────────────────────────────────────
const SK_URL              = 'pa_target_url';
const SK_PROMPT_TEMPLATE  = 'pa_prompt_template_v7';
const SK_PROMPT_TEMPLATE_V6 = 'pa_prompt_template_v6';
const SK_PROMPT_TEMPLATE_V5 = 'pa_prompt_template_v5';
const SK_PROMPT_GLOBAL    = 'pa_prompt_global';
const SK_PROMPT_PER       = 'pa_prompt_per_record';
const SK_RENDER_MODE      = 'pa_render_mode';            // 'llm' | 'agent'

// ─── 发票字段 ──────────────────────────────────────────
const INVOICE_FIELD_SET = new Set([
  '发票号码', '价税合计', '商品名称', '开票日期',
  '销售方名称', '发票类型', '匹配附件', '发票路径', '附件路径',
]);

// ─── 计算字段（由转卡金额计算注入） ────────────────────
const CALCULATED_FIELD_SET = new Set([
  '转卡金额', '转卡明细', '转卡汇总', '附件汇总', '记录ID映射',
]);

// 聚合类计算字段：由 calculateTransferAmounts 基于整批选中记录汇总生成。
// 这些字段可以合法地为空字符串（例如没有任何选中记录带附件 → 附件汇总为空），
// 因此：
//   1) getMissingFields 不应将它们视为"缺少字段"
//   2) renderPromptRegex 应在值为空时直接替换为空串（而非保留 {{xx}} 字面量）
const AGGREGATE_COMPUTED_FIELDS = new Set(['转卡汇总', '附件汇总', '记录ID映射']);

const INTERNAL_FIELD_SET = new Set([
  'id', 'db_id', 'updated_at', 'extra_fields', 'category',
  '匹配发票', '匹配发票金额', '是否匹配', '匹配方式',
  '组合金额', '备注分解金额', '未匹配金额',
]);

const RECORD_BASE_FIELDS = ['序号', '姓名/公司', '填写日期', '金额', '物品简介', '备注', '类别'];

// ─── 默认模板（Skill 加载失败时的内置回退） ─────────────
const DEFAULT_TEMPLATE = `{{记录ID映射}}
首先点击「智能报销」按钮，然后根据本报销单的类别「{{类别}}」，按照「类别与报销入口映射」表点击对应的报销入口按钮。
===每张发票重复执行===
注意：本步骤只处理票号「{{发票号码}}」这一张发票，不要操作其他发票。如果该票号的复选框已经是选中状态，则跳过本步骤，直接进入下一张发票。
在网页的票号列中查找「{{发票号码}}」：如果找到，点击该行对应的复选框将其选中；如果未找到，则点击「上传发票」按钮，点击「上传附件」按钮，输入文件路径「{{发票路径}}」，点击文件，点击「打开」按钮，点击「保存发票」按钮，再点击选中该发票对应的复选框。
完成后，立即进入下一张发票的操作，不要回头重复操作已处理过的发票。
======
所有发票处理完毕后，逐一核对每张发票的复选框均已选中，确认无误后点击「前往报销」按钮。点击「点击修改信息」链接，在「手机」输入框中填写 13181923826，在「联系电话」输入框中填写 13181923826，在「报销投递校区」下拉框中选择「千佛山校区」，点击「保存」按钮。
点击「请填写报销项目」链接，在弹出的「选择经费项目」窗口中，找到底部「填写他人项目」区域，在「项目代码」输入框中填写「{{项目代码}}」，在「项目负责人姓名」输入框中填写「陈阿莲」，点击「保存」按钮。
点击「修改支付信息」链接。
将「汇款」区域中每一条收款方的金额改为 0.00。
{{转卡汇总}}
{{附件汇总}}
注意：如果上方附件列表中出现了相同文件路径的重复条目，每个文件只上传一次，跳过重复项。
点击「返回」按钮。`;

// ─── Skill 模板加载键 ────────────────────────────────
const SK_SKILL_LOADED  = 'pa_skill_template_loaded';

// ─── 转卡金额计算（前端本地） ────────────────────────
function calculateTransferAmounts(records) {
  const personAmounts = {};
  for (const rec of records) {
    const name = (rec['姓名/公司'] || '').trim();
    if (!name) continue;
    const amt = parseFloat(rec['金额'] || 0) || 0;
    if (!personAmounts[name]) personAmounts[name] = [];
    personAmounts[name].push(amt);
  }
  const amountByPerson = {};
  const detailByPerson = {};
  for (const [name, amts] of Object.entries(personAmounts)) {
    const total = Math.round(amts.reduce((s, a) => s + a, 0) * 100) / 100;
    amountByPerson[name] = total;
    if (amts.length === 1) {
      detailByPerson[name] = amts[0].toFixed(2);
    } else {
      detailByPerson[name] = amts.map(a => a.toFixed(2)).join('+') + '=' + total.toFixed(2);
    }
  }

  // 生成「转卡汇总」—— 去重后的完整转卡指令，每个户名只出现一次
  const summaryLines = Object.entries(amountByPerson).map(([name, total]) => {
    const detail = detailByPerson[name];
    let line = `在「转卡」区域中找到户名为${name}的那一行，将其金额设置为 ${total.toFixed(2)}`;
    if (detail.includes('+')) line += `（明细：${detail}）`;
    line += '，点击保存。';
    return line;
  });
  const transferSummary = summaryLines.join('\n');

  // 收集所有有附件的记录，按附件路径去重后生成上传指令
  // 路径归一化：统一分隔符、去除尾部斜杠、去除多余空白、处理 URI 编码
  const normalizePath = (p) => {
    let n = p.trim();
    n = n.replace(/\\/g, '/');           // 统一为正斜杠
    n = n.replace(/\/+/g, '/');          // 合并连续斜杠
    n = n.replace(/\/+$/, '');           // 去除尾部斜杠
    try { n = decodeURIComponent(n); } catch (_) {}  // 解码 URI 编码
    return n;
  };
  const seenPaths = new Set();
  const attachmentLines = [];
  for (const r of records) {
    if (!r['匹配附件'] || !r['附件路径']) continue;
    // 附件路径可能含逗号分隔的多个路径
    const paths = r['附件路径'].split(',').map(p => p.trim()).filter(Boolean);
    for (const p of paths) {
      const normalized = normalizePath(p);
      if (seenPaths.has(normalized)) continue;
      seenPaths.add(normalized);
      // 上传指令使用归一化后的路径，避免路径格式不一致
      attachmentLines.push(`在补充说明中上传附件，点击上传附件，输入文件路径「${normalized}」，点击文件，点击打开。`);
    }
  }
  const attachmentSummary = attachmentLines.join('\n');

  // 生成「记录ID映射」—— 在模板开头告知 LLM 每条记录的 db_id，
  // 以便 LLM 在 skip 时自行给出对应的 record_id 参数
  const idMapLines = records.map((r, i) =>
    `  - record_id=${r['db_id'] || '?'}, 序号=${r['序号'] || '?'}, 姓名=${r['姓名/公司'] || '?'}, 票号=${r['发票号码'] || '?'}, 金额=${r['金额'] || '?'}`
  );
  const recordIdMap = [
    `本报销单包含 ${records.length} 条记录，record_id 与发票的对应关系如下：`,
    ...idMapLines,
    `如需跳过某条记录的操作，请在 skip 动作的 value 中注明对应的 record_id。`,
  ].join('\n');

  return records.map(rec => {
    const name = (rec['姓名/公司'] || '').trim();
    return {
      ...rec,
      '转卡金额': name && amountByPerson[name] != null ? amountByPerson[name].toFixed(2) : (rec['金额'] || '0.00'),
      '转卡明细': name && detailByPerson[name]       ? detailByPerson[name]             : String(rec['金额'] || '0.00'),
      '转卡汇总': transferSummary,
      '附件汇总': attachmentSummary,
      '记录ID映射': recordIdMap,
    };
  });
}

// ─── 迁移 ──────────────────────────────────────────────
function migrateToV7() {
  if (localStorage.getItem(SK_PROMPT_TEMPLATE) !== null) return null;
  const v6 = localStorage.getItem(SK_PROMPT_TEMPLATE_V6);
  if (v6 !== null) return v6;
  const v5 = localStorage.getItem(SK_PROMPT_TEMPLATE_V5);
  if (v5 !== null) return v5;
  const globalPart = localStorage.getItem(SK_PROMPT_GLOBAL);
  const perPart    = localStorage.getItem(SK_PROMPT_PER);
  if (globalPart === null && perPart === null) return null;
  const parts = [];
  if (globalPart?.trim()) parts.push(globalPart.trim());
  parts.push('===每张发票重复执行===');
  if (perPart?.trim()) parts.push(perPart.trim());
  parts.push('======');
  parts.push('所有发票添加完毕后，检查报销单总金额是否正确，确认无误后点击「前往报销」按钮。');
  return parts.join('\n');
}

// ═══════════════════════════════════════════════════════════
// 模板解析引擎
// ═══════════════════════════════════════════════════════════

function parseSections(template) {
  if (!template || !template.trim()) return [];
  const sections = [];
  let remaining = template;
  while (remaining.length > 0) {
    const markerMatch = remaining.match(/^(===.+?===)\s*$/m);
    if (!markerMatch) {
      if (remaining.trim()) sections.push({ type: 'once', content: remaining.trim() });
      break;
    }
    const markerIdx = remaining.indexOf(markerMatch[0]);
    const before = remaining.slice(0, markerIdx).trim();
    if (before) sections.push({ type: 'once', content: before });
    remaining = remaining.slice(markerIdx + markerMatch[0].length);
    const dividerMatch = remaining.match(/^======\s*$/m);
    if (!dividerMatch) {
      if (remaining.trim()) sections.push({ type: 'repeat', content: remaining.trim() });
      break;
    }
    const dividerIdx = remaining.indexOf(dividerMatch[0]);
    const repeatContent = remaining.slice(0, dividerIdx).trim();
    if (repeatContent) sections.push({ type: 'repeat', content: repeatContent });
    remaining = remaining.slice(dividerIdx + dividerMatch[0].length);
  }
  return sections;
}

function getSectionStats(sections) {
  const onceCount   = sections.filter(s => s.type === 'once').length;
  const repeatCount = sections.filter(s => s.type === 'repeat').length;
  return { onceCount, repeatCount, total: sections.length };
}

// ═══════════════════════════════════════════════════════════
// 渲染引擎 A：正则替换（前端本地）
// ═══════════════════════════════════════════════════════════

function renderPromptRegex(template, record) {
  if (!record) return template;
  return template.replace(/\{\{(.+?)\}\}/g, (match, key) => {
    const name = key.trim();
    const val = record[name];
    if (val === undefined || val === null || val === '') {
      // 聚合类计算字段：空值替换为空串（让模板自然省略这一行），
      // 而不是保留字面量 {{附件汇总}} 污染输出
      if (AGGREGATE_COMPUTED_FIELDS.has(name)) return '';
      return match;
    }
    return String(val);
  });
}

function buildMergedInstructionRegex(template, records) {
  const sections = parseSections(template);
  if (!records.length) return sections.map(s => s.content).join('\n\n');
  // ── 注入转卡金额计算结果 ──
  const enrichedRecords = calculateTransferAmounts(records);
  const firstRecord = enrichedRecords[0];
  const parts = [];
  for (const section of sections) {
    if (section.type === 'once') {
      parts.push(renderPromptRegex(section.content, firstRecord));
    } else {
      parts.push(`\n以下对 ${enrichedRecords.length} 张发票依次执行，严格按顺序逐一处理，每张只操作一次：\n`);
      const expanded = enrichedRecords.map((rec, i) => {
        const rendered = renderPromptRegex(section.content, rec);
        const label = `【第 ${i + 1}/${enrichedRecords.length} 张: ${rec['姓名/公司'] || ''} ¥${rec['金额'] || ''}${rec['发票号码'] ? ` 票号${rec['发票号码']}` : ''}】`;
        return `${label}\n${rendered}`;
      });
      parts.push(expanded.join('\n\n'));
    }
  }
  return parts.join('\n\n');
}

// ═══════════════════════════════════════════════════════════
// 渲染引擎 B：LLM 智能渲染（调用后端 /api/render-prompt）
// ═══════════════════════════════════════════════════════════

async function buildMergedInstructionLLM(template, records, baseUrl, signal) {
  const res = await fetch(`${baseUrl}/api/render-prompt`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    signal,
    body: JSON.stringify({ template, records }),
  });
  if (!res.ok) {
    const errText = await res.text().catch(() => '');
    throw new Error(`后端渲染失败 (${res.status}): ${errText.slice(0, 200)}`);
  }
  const data = await res.json();
  if (!data.success) throw new Error(data.message || 'LLM 渲染返回失败');
  return data.rendered;
}

// ═══════════════════════════════════════════════════════════
// 工具函数
// ═══════════════════════════════════════════════════════════

function extractVariables(template) {
  const sections = parseSections(template);
  const vars = new Set();
  for (const s of sections) {
    for (const m of s.content.matchAll(/\{\{(.+?)\}\}/g)) vars.add(m[1].trim());
  }
  return [...vars];
}

function getMissingFields(template, record) {
  return extractVariables(template).filter(v => {
    // 聚合计算字段永不算"缺失"——它们由 calculateTransferAmounts 基于
    // 整批选中记录生成，空字符串是合法结果（例如没有附件要上传）
    if (AGGREGATE_COMPUTED_FIELDS.has(v)) return false;
    const val = record?.[v];
    return val === undefined || val === null || val === '';
  });
}

function checkCategoryConsistency(records) {
  if (records.length <= 1) return { consistent: true, categories: [] };
  const categories = [...new Set(records.map(r => r['类别'] || '未分类'))];
  return { consistent: categories.length <= 1, categories };
}

const LOG_COLORS = { info: '#3b82f6', success: '#22c55e', error: '#ef4444', step: '#a78bfa', warn: '#f59e0b' };

// ═══════════════════════════════════════════════════════════
// LLM 预览 Hook（调用后端，带缓存）
// ═══════════════════════════════════════════════════════════

function useLLMPreview(template, records, renderMode, baseUrl) {
  const [llmPreview, setLlmPreview]   = useState('');
  const [llmLoading, setLlmLoading]   = useState(false);
  const [llmError, setLlmError]       = useState('');
  const abortRef = useRef(null);
  const cacheRef = useRef(new Map());

  const cacheKey = useMemo(() => {
    if (renderMode !== 'llm' || !records.length) return '';
    return `${template}__${records.map(r => r.id).sort().join(',')}`;
  }, [template, records, renderMode]);

  const triggerLLMPreview = useCallback(async () => {
    if (renderMode !== 'llm' || !records.length) {
      setLlmPreview(''); setLlmError(''); return;
    }
    if (!baseUrl) {
      setLlmError('MCP 服务器地址为空，无法调用 LLM 渲染'); return;
    }
    if (cacheRef.current.has(cacheKey)) {
      setLlmPreview(cacheRef.current.get(cacheKey)); setLlmError(''); return;
    }
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setLlmLoading(true); setLlmError('');
    try {
      const result = await buildMergedInstructionLLM(template, records, baseUrl, controller.signal);
      cacheRef.current.set(cacheKey, result);
      setLlmPreview(result);
    } catch (err) {
      if (err.name === 'AbortError') return;
      setLlmError(err.message);
    } finally { setLlmLoading(false); }
  }, [template, records, renderMode, baseUrl, cacheKey]);

  const clearCache = useCallback(() => { cacheRef.current.clear(); setLlmPreview(''); }, []);
  useEffect(() => () => abortRef.current?.abort(), []);

  return { llmPreview, llmLoading, llmError, triggerLLMPreview, clearCache };
}

// ─── 主组件 ──────────────────────────────────────────────

export default function ReimbursementAgent({ tableData, user, mcpUrl }) {
  const isAdmin = user.role === 'admin';
  const myRecords = isAdmin ? tableData : tableData.filter(r => r['姓名/公司'] === user.name);
  const baseUrl = useMemo(() => {
    const raw = (mcpUrl || '').replace(/\/mcp\/?$/, '').replace(/\/$/, '');
    // 如果 mcpUrl 是相对路径（如 "/mcp"），去掉后变空串，回退到当前 origin
    return raw || (typeof window !== 'undefined' ? window.location.origin : '');
  }, [mcpUrl]);

  const [migrated] = useState(() => migrateToV7());

  const [targetUrl, setTargetUrl]           = useState(() => localStorage.getItem(SK_URL) || '');
  const [promptTemplate, setPromptTemplate] = useState(() =>
    localStorage.getItem(SK_PROMPT_TEMPLATE) ?? migrated ?? DEFAULT_TEMPLATE
  );
  const [selectedIds, setSelectedIds]       = useState(new Set());
  const [showVarPanel, setShowVarPanel]     = useState(false);
  const [executing, setExecuting]           = useState(false);
  const [currentIdx, setCurrentIdx]         = useState(-1);
  const [logs, setLogs]                     = useState([]);
  const [renderMode, setRenderMode]         = useState(() => {
    const saved = localStorage.getItem(SK_RENDER_MODE) || 'llm';
    return saved === 'regex' ? 'llm' : saved;   // 兼容旧版：regex → llm
  });
  const logsEndRef = useRef(null);
  const promptRef  = useRef(null);
  const [amountSummary, setAmountSummary] = useState(null); // 转卡金额汇总

  useEffect(() => { logsEndRef.current?.scrollIntoView({ behavior: 'smooth' }); }, [logs]);
  useEffect(() => { if (migrated) localStorage.setItem(SK_PROMPT_TEMPLATE, migrated); }, [migrated]);

  // ── Skill 模板加载：首次挂载时从后端 /api/prompt-template 加载 ──
  useEffect(() => {
    // 仅在用户未手动编辑过模板时（首次使用或重置后）才从 Skill 加载
    const alreadyLoaded = localStorage.getItem(SK_SKILL_LOADED);
    const userEdited    = localStorage.getItem(SK_PROMPT_TEMPLATE);
    if (alreadyLoaded || userEdited) return;

    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(`${baseUrl}/api/prompt-template?name=default`);
        if (!res.ok) return;
        const data = await res.json();
        if (cancelled || !data.success || !data.template) return;
        setPromptTemplate(data.template);
        localStorage.setItem(SK_PROMPT_TEMPLATE, data.template);
        localStorage.setItem(SK_SKILL_LOADED, '1');
      } catch { /* Skill 加载失败，使用内置默认模板 */ }
    })();
    return () => { cancelled = true; };
  }, [baseUrl]);

  const saveTemplate   = (val) => { setPromptTemplate(val); localStorage.setItem(SK_PROMPT_TEMPLATE, val); };
  const saveUrl        = (url) => { setTargetUrl(url);       localStorage.setItem(SK_URL, url); };
  const saveRenderMode = (mode) => { setRenderMode(mode);    localStorage.setItem(SK_RENDER_MODE, mode); };

  const addLog = useCallback((type, text) =>
    setLogs(p => [...p, { time: new Date().toLocaleTimeString('zh-CN'), type, text }]),
  []);

  const toggleRecord = (id) => {
    setSelectedIds(prev => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  };
  const selectAll  = () => setSelectedIds(new Set(myRecords.map(r => r.id)));
  const selectNone = () => setSelectedIds(new Set());

  const selectedRecords = useMemo(() => myRecords.filter(r => selectedIds.has(r.id)), [myRecords, selectedIds]);

  // ── 选中记录变化时，计算转卡金额汇总 ──
  const enrichedSelectedRecords = useMemo(() => {
    if (!selectedRecords.length) { setAmountSummary(null); return []; }
    const enriched = calculateTransferAmounts(selectedRecords);
    // 生成按人汇总
    const summary = {};
    for (const rec of enriched) {
      const name = (rec['姓名/公司'] || '').trim();
      if (name && !summary[name]) {
        summary[name] = { total: rec['转卡金额'], detail: rec['转卡明细'] };
      }
    }
    setAmountSummary(summary);
    return enriched;
  }, [selectedRecords]);

  const variableGroups = useMemo(() => {
    const recordFields = new Set(RECORD_BASE_FIELDS);
    const invoiceFields = new Set(INVOICE_FIELD_SET);
    (tableData || []).forEach(r => {
      Object.keys(r).forEach(k => {
        if (INTERNAL_FIELD_SET.has(k)) return;
        if (INVOICE_FIELD_SET.has(k)) invoiceFields.add(k); else recordFields.add(k);
      });
    });
    return [
      { label: '📋 报销记录', desc: '来自「报销明细」表', fields: [...recordFields] },
      { label: '🧾 发票信息', desc: '来自 OCR 识别的发票数据', fields: [...invoiceFields] },
      { label: '🔢 计算字段', desc: '按户名自动汇总的转卡金额', fields: [...CALCULATED_FIELD_SET] },
    ];
  }, [tableData]);

  const allFields = useMemo(() => variableGroups.flatMap(g => g.fields), [variableGroups]);

  const insertVariable = (fieldName) => {
    const el = promptRef.current;
    if (!el) return;
    const tag = `{{${fieldName}}}`;
    const start = el.selectionStart ?? promptTemplate.length;
    const end   = el.selectionEnd ?? start;
    const next  = promptTemplate.slice(0, start) + tag + promptTemplate.slice(end);
    saveTemplate(next);
    requestAnimationFrame(() => { el.focus(); const pos = start + tag.length; el.setSelectionRange(pos, pos); });
  };

  const parsedSections = useMemo(() => parseSections(promptTemplate), [promptTemplate]);
  const sectionStats   = useMemo(() => getSectionStats(parsedSections), [parsedSections]);
  const usedVars       = extractVariables(promptTemplate);
  const missingInPreview = enrichedSelectedRecords[0] ? getMissingFields(promptTemplate, enrichedSelectedRecords[0]) : [];
  const categoryCheck  = useMemo(() => checkCategoryConsistency(selectedRecords), [selectedRecords]);

  const regexPreviewText = selectedRecords.length > 0
    ? buildMergedInstructionRegex(promptTemplate, selectedRecords)
    : parseSections(promptTemplate).map(s => s.content).join('\n\n');

  const { llmPreview, llmLoading, llmError, triggerLLMPreview, clearCache } =
    useLLMPreview(promptTemplate, selectedRecords, renderMode, baseUrl);

  const previewText = renderMode === 'llm' && llmPreview ? llmPreview : regexPreviewText;

  // ── 执行 ──
  const handleExecute = useCallback(async () => {
    if (!selectedRecords.length || !targetUrl) return;

    // agent 模式不需要检查模板区段
    if (renderMode !== 'agent') {
      const hasRepeat = parsedSections.some(s => s.type === 'repeat');
      if (!hasRepeat) return;
    }

    setExecuting(true); setLogs([]); setCurrentIdx(0);

    addLog('info', `🎯 目标网址: ${targetUrl}`);
    addLog('info', `📋 共选择 ${selectedRecords.length} 条记录，合并为一个报销单执行`);

    const modeLabels = { llm: '🤖 LLM 智能渲染', agent: '🧠 Agent 自主编排' };
    addLog('info', `🔧 执行模式: ${modeLabels[renderMode] || renderMode}`);

    if (!categoryCheck.consistent) {
      addLog('warn', `⚠️ 注意: 选中记录包含多个类别 (${categoryCheck.categories.join('、')})`);
    }
    selectedRecords.forEach((rec, i) => {
      addLog('info', `   ${i + 1}. ${rec['姓名/公司']} · ¥${rec['金额']} · ${rec['物品简介'] || '(无摘要)'} · ${rec['类别'] || '未分类'}`);
    });

// ════════════════════════════════════════════════════════
    // Agent 模式：两阶段执行
    //   第一阶段：LLM 编排生成指令
    //   第二阶段：BrowserAgent 执行浏览器操作
    // ════════════════════════════════════════════════════════
    if (renderMode === 'agent') {
      addLog('info', '🧠 启动 Agent 两阶段模式...');
      addLog('info', '   第一阶段：LLM 编排生成浏览器操作指令');
      addLog('info', '   第二阶段：BrowserAgent 执行浏览器操作');

      try {
        const res = await fetch(`${baseUrl}/api/agent-reimbursement`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            record_ids: selectedRecords.map(r => r.db_id).filter(Boolean),
            target_url: targetUrl,
            max_steps: 10,
          }),
        });
        const data = await res.json();

        // ── 第一阶段日志 ──
        if (data.agent_steps?.length) {
          addLog('info', `\n━━━ 第一阶段：Agent 编排（共 ${data.agent_steps.length} 步）━━━`);
          data.agent_steps.forEach((step, i) => {
            if (step.tool === '_text') {
              addLog('info', `   💬 LLM: ${(step.result?.text || '').slice(0, 200)}`);
            } else {
              const success = step.result?.success !== false;
              addLog(success ? 'step' : 'error',
                `   [${i + 1}] ${success ? '✓' : '✗'} ${step.tool}` +
                (step.result?.message ? ` — ${step.result.message}` : '') +
                (step.result?.record_count != null ? ` (${step.result.record_count} 条记录)` : '') +
                (step.result?.rendered_length != null ? ` (${step.result.rendered_length} 字符)` : '')
              );
            }
          });
        }

        if (data.agent_summary) {
          addLog('info', `   📝 编排摘要: ${data.agent_summary}`);
        }

        // ── 显示生成的指令 ──
        if (data.rendered_instruction) {
          addLog('info', `\n━━━ 生成的浏览器操作指令（${data.rendered_instruction.length} 字符）━━━`);
          // 只显示前 500 字符，避免日志过长
          const preview = data.rendered_instruction.length > 500
            ? data.rendered_instruction.slice(0, 500) + '\n...(已截断)'
            : data.rendered_instruction;
          addLog('info', preview);
        }

        // ── 第二阶段日志 ──
        if (data.browser_result) {
          addLog('info', '\n━━━ 第二阶段：浏览器执行 ━━━');
          if (data.browser_result.steps?.length) {
            data.browser_result.steps.forEach(s => addLog('step', s));
          }
          if (data.browser_result.success) {
            addLog('success', `✅ 浏览器执行完成: ${data.browser_result.message || '成功'}`);
          } else {
            addLog('error', `❌ 浏览器执行失败: ${data.browser_result.message || '失败'}`);
          }
        } else if (!data.rendered_instruction) {
          addLog('error', '❌ 第一阶段未生成指令，跳过浏览器执行');
        }

        // ── 最终结果 ──
        if (data.success) {
          addLog('success', `\n✅ 两阶段流程全部完成`);
          addLog('success', `📊 共 ${selectedRecords.length} 张发票已合并到一个报销单`);
        } else {
          addLog('error', `\n❌ 流程失败: ${data.message || data.agent_summary || '未知错误'}`);
        }
      } catch (err) {
        addLog('error', `❌ Agent 请求失败: ${err.message}`);
      }

      addLog('info', '\n━━━ 执行结束 ━━━');
      setCurrentIdx(-1); setExecuting(false);
      return;
    }
    // ════════════════════════════════════════════════════════
    // LLM 模式：前端写死流程，LLM 渲染，正则备选
    // ════════════════════════════════════════════════════════

    // 输出转卡金额汇总
    if (amountSummary) {
      addLog('info', '💰 转卡金额汇总（按户名自动计算）:');
      for (const [name, info] of Object.entries(amountSummary)) {
        addLog('info', `   ${name}: ¥${info.total}（${info.detail}）`);
      }
    }

    let mergedInstruction;

    addLog('info', '🤖 正在调用后端 LLM 渲染（含自动金额计算）...');
    try {
      mergedInstruction = await buildMergedInstructionLLM(promptTemplate, selectedRecords, baseUrl, null);
      addLog('success', `✅ LLM 渲染完成，指令长度: ${mergedInstruction.length} 字符`);
    } catch (err) {
      addLog('error', `❌ LLM 渲染失败: ${err.message}`);
      addLog('warn', '⚠️ 自动回退到正则替换...');
      mergedInstruction = buildMergedInstructionRegex(promptTemplate, selectedRecords);
      addLog('info', `📝 正则渲染完成，指令长度: ${mergedInstruction.length} 字符`);
    }

    addLog('info', `📄 模板结构: ${sectionStats.onceCount} 个一次性区域 + ${sectionStats.repeatCount} 个重复区域`);
    addLog('info', '🚀 已发送到后端，Playwright 正在执行...');

    try {
      const res = await fetch(`${baseUrl}/api/browser-task`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          task: mergedInstruction,
          url: targetUrl,
          record_ids: selectedRecords.map(r => r.db_id).filter(Boolean),
        }),
      });
      const data = await res.json();
      if (data.steps?.length) data.steps.forEach(s => addLog('step', s));
      if (data.success) {
        addLog('success', `✅ 报销单填报完成: ${data.message || '任务执行成功'}`);
        addLog('success', `📊 共 ${selectedRecords.length} 张发票已合并到一个报销单`);
      } else {
        addLog('error', `❌ 报销单填报失败: ${data.message || '任务失败'}`);
        if (data.error) addLog('error', `   原因: ${data.error}`);
      }
    } catch (err) { addLog('error', `❌ 请求失败: ${err.message}`); }

    addLog('info', '\n━━━ 执行结束 ━━━');
    setCurrentIdx(-1); setExecuting(false);
  }, [selectedRecords, targetUrl, promptTemplate, baseUrl, addLog, parsedSections, sectionStats, categoryCheck, renderMode, amountSummary]);

  const resetTemplate = () => {
    localStorage.removeItem(SK_SKILL_LOADED);
    localStorage.removeItem(SK_PROMPT_TEMPLATE);
    clearCache();
    // 尝试从 Skill 重新加载，失败则使用内置默认
    (async () => {
      try {
        const res = await fetch(`${baseUrl}/api/prompt-template?name=default`);
        if (res.ok) {
          const data = await res.json();
          if (data.success && data.template) {
            saveTemplate(data.template);
            localStorage.setItem(SK_SKILL_LOADED, '1');
            return;
          }
        }
      } catch { /* ignore */ }
      saveTemplate(DEFAULT_TEMPLATE);
    })();
  };

  // ── 样式 ──
  const sLabel = { fontSize: '12px', fontWeight: 500, color: T.textSecondary, display: 'block', marginBottom: '6px' };
  const sBtnSm = { background: 'none', border: 'none', cursor: 'pointer', fontFamily: T.font, fontSize: '12px', padding: '2px 8px', borderRadius: '4px' };
  const sTag   = (active) => ({
    display: 'inline-flex', alignItems: 'center', gap: '2px',
    padding: '3px 10px', borderRadius: '20px', fontSize: '12px', cursor: 'pointer',
    fontFamily: T.font, transition: 'all 0.15s', whiteSpace: 'nowrap',
    background: active ? T.accent + '18' : T.surface,
    color: active ? T.accent : T.textSecondary,
    border: `1px solid ${active ? T.accent + '40' : T.border}`,
  });
  const sTextarea = {
    width: '100%', resize: 'vertical', padding: '10px 12px',
    background: T.bg, color: T.text,
    border: `1px solid ${T.border}`, borderRadius: T.radiusSm,
    fontFamily: T.font, fontSize: '13px', lineHeight: 1.8,
    outline: 'none', transition: 'border-color 0.2s',
  };

  // 开关样式 — 二段式: llm | agent
  const isLLM = renderMode === 'llm';
  const isAgent = renderMode === 'agent';
  const modeIndex = isLLM ? 0 : 1; // 0=llm, 1=agent
  const sToggleTrack = {
    position: 'relative', width: '220px', height: '32px', borderRadius: '16px',
    background: T.surface, border: `1px solid ${T.border}`,
    cursor: 'pointer', display: 'flex', alignItems: 'center', padding: '2px',
    transition: 'all 0.2s',
  };
  const sToggleThumb = {
    position: 'absolute', left: `${2 + modeIndex * 110}px`,
    width: '108px', height: '28px', borderRadius: '14px',
    background: isAgent ? '#f59e0b' : T.accent,
    transition: 'all 0.25s cubic-bezier(0.4, 0, 0.2, 1)', opacity: 0.15,
  };
  const sToggleLabel2 = (mode) => ({
    flex: 1, textAlign: 'center', fontSize: '12px',
    fontWeight: renderMode === mode ? 600 : 400,
    color: renderMode === mode
      ? (mode === 'agent' ? '#f59e0b' : T.accent)
      : T.textMuted,
    position: 'relative', zIndex: 1, transition: 'all 0.2s', userSelect: 'none',
  });
  const cycleMode = () => {
    const next = isLLM ? 'agent' : 'llm';
    saveRenderMode(next);
  };

  return (
    <div style={{ animation: 'fadeIn 0.4s ease' }}>
      <PageHeader title="智能填报" subtitle="编写提示词 → 勾选记录 → 合并为一个报销单自动提交" />

      <div style={{ display: 'grid', gridTemplateColumns: '400px 1fr', gap: '16px', alignItems: 'start' }}>
        {/* ══════════ 左列 ══════════ */}
        <div style={{ display: 'grid', gap: '12px' }}>

          {/* 目标 URL */}
          <Card style={{ padding: '14px' }}>
            <label style={sLabel}>🌐 报销系统网址</label>
            <Input value={targetUrl} onChange={e => saveUrl(e.target.value)} placeholder="https://xxx.edu.cn/reimburse" style={{ width: '100%' }} />
          </Card>

          {/* ═══ 渲染引擎开关 ═══ */}
          <Card style={{ padding: '14px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '10px' }}>
              <span style={{ fontSize: '13px', fontWeight: 600, color: T.text }}>⚙️ 渲染引擎</span>
            </div>
            <div style={sToggleTrack} onClick={cycleMode}>
              <div style={sToggleThumb} />
              <span style={sToggleLabel2('llm')}>🤖 LLM 渲染</span>
              <span style={sToggleLabel2('agent')}>🧠 Agent</span>
            </div>
            <div style={{ marginTop: '8px', fontSize: '11px', color: T.textMuted, lineHeight: 1.7 }}>
              {isAgent ? (
                <><strong style={{ color: '#f59e0b' }}>Agent 自主编排</strong>：LLM 自己决定调用工具的顺序 — 读取记录、计算金额、加载模板、渲染、执行浏览器操作，全程由 AI 自主决策，无需固定流程。</>
              ) : (
                <><strong style={{ color: T.accent }}>LLM 智能渲染</strong>：调用后端 LLM 理解模板语义，自动裁剪条件分支、处理缺失字段、生成连贯指令。单区段失败时自动回退正则。</>
              )}
            </div>
          </Card>

          {/* ═══ 提示词模板 ═══ */}
          <Card style={{ padding: '14px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
              <span style={{ fontSize: '13px', fontWeight: 600, color: T.text }}>✏️ 提示词模板</span>
              <div style={{ display: 'flex', gap: '4px' }}>
                <button onClick={() => setShowVarPanel(!showVarPanel)} style={{ ...sBtnSm, color: T.accentLight, background: showVarPanel ? T.accent + '12' : 'none' }}>
                  {showVarPanel ? '收起变量' : '📎 插入变量'}
                </button>
                <button onClick={resetTemplate} style={{ ...sBtnSm, color: T.textMuted }}>重置</button>
              </div>
            </div>

            <div style={{ padding: '10px 12px', marginBottom: '10px', background: T.surface, borderRadius: T.radiusSm, border: `1px solid ${T.border}`, fontSize: '11px', color: T.textMuted, lineHeight: 1.7 }}>
              用两种标记划分区域（支持多组重复区块）：
              <div style={{ marginTop: '6px', fontFamily: T.mono, fontSize: '11px', lineHeight: 2 }}>
                <span style={{ color: T.textSecondary }}>一次性步骤（如点击按钮、选择类别）</span><br/>
                <span style={{ color: T.accent, fontWeight: 600 }}>===每张发票重复执行===</span>
                <span style={{ color: T.textMuted, marginLeft: '6px' }}>← 命名标记（可多组）</span><br/>
                <span style={{ color: T.warning }}>每张发票的操作（含 {'{{变量}}'}）</span><br/>
                <span style={{ color: '#22c55e', fontWeight: 600 }}>======</span>
                <span style={{ color: T.textMuted, marginLeft: '6px' }}>← 纯分隔线</span><br/>
                <span style={{ color: T.textSecondary }}>后续一次性步骤 / 可再写一组重复...</span>
              </div>
              {!isAgent && (
                <div style={{ marginTop: '8px', padding: '6px 8px', background: T.accent + '08', borderRadius: '4px', color: T.accent }}>
                  💡 支持自然语言条件（如"如果金额大于1000则..."），AI 会根据实际数据自动处理。
                </div>
              )}
            </div>

            {showVarPanel && (
              <div style={{ marginBottom: '8px', padding: '10px', background: T.bg, borderRadius: T.radiusSm, border: `1px dashed ${T.border}` }}>
                <div style={{ fontSize: '11px', color: T.textMuted, marginBottom: '6px' }}>
                  点击变量标签，自动插入到编辑框光标处<span style={{ color: T.accent }}> (也可直接写字段名)</span>：
                </div>
                {variableGroups.map(group => (
                  <div key={group.label} style={{ marginBottom: '6px' }}>
                    <div style={{ fontSize: '11px', fontWeight: 600, color: T.textSecondary, marginBottom: '3px' }}>
                      {group.label} <span style={{ fontWeight: 400, color: T.textMuted }}>— {group.desc}</span>
                    </div>
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px' }}>
                      {group.fields.map(f => <button key={f} onClick={() => insertVariable(f)} style={sTag(usedVars.includes(f))}>{f}</button>)}
                    </div>
                  </div>
                ))}
              </div>
            )}

            <textarea
              ref={promptRef} value={promptTemplate}
              onChange={e => { saveTemplate(e.target.value); clearCache(); }}
              placeholder={'首先点击「智能报销」...\n===每张发票重复执行===\n检查发票「{{发票号码}}」...\n======\n确认无误后点击提交...'}
              spellCheck={false} style={{ ...sTextarea, minHeight: '260px' }}
              onFocus={e => e.target.style.borderColor = T.accent + '60'}
              onBlur={e => e.target.style.borderColor = T.border}
            />

            <div style={{ marginTop: '8px', display: 'flex', flexWrap: 'wrap', gap: '6px', alignItems: 'center' }}>
              <span style={{ fontSize: '11px', color: T.textMuted }}>已识别区段:</span>
              {parsedSections.map((sec, i) => (
                <span key={i} style={{
                  padding: '2px 8px', borderRadius: '10px', fontSize: '11px',
                  background: sec.type === 'repeat' ? T.warning + '15' : T.accent + '15',
                  color: sec.type === 'repeat' ? T.warning : T.accent,
                  border: `1px solid ${sec.type === 'repeat' ? T.warning + '30' : T.accent + '30'}`,
                }}>
                  {sec.type === 'repeat' ? '🔁 重复' : '▶️ 一次性'} {i + 1}
                </span>
              ))}
              {parsedSections.length === 0 && <span style={{ fontSize: '11px', color: T.danger }}>⚠️ 未识别到任何区段</span>}
            </div>

            {usedVars.length > 0 && (
              <div style={{ marginTop: '6px', display: 'flex', flexWrap: 'wrap', gap: '4px', alignItems: 'center' }}>
                <span style={{ fontSize: '11px', color: T.textMuted }}>引用变量:</span>
                {usedVars.map(v => (
                  <span key={v} style={{
                    display: 'inline-block', padding: '2px 8px', borderRadius: '10px', fontSize: '11px', fontFamily: T.mono,
                    background: allFields.includes(v) ? T.accent + '15' : T.danger + '15',
                    color: allFields.includes(v) ? T.accent : T.danger,
                  }}>{v} {!allFields.includes(v) && '⚠️'}</span>
                ))}
              </div>
            )}
          </Card>

          {/* 使用说明 */}
          <Card style={{ padding: '14px' }}>
            <div style={{ fontSize: '13px', fontWeight: 600, color: T.text, marginBottom: '8px' }}>💡 怎么用</div>
            <div style={{ fontSize: '12px', color: T.textMuted, lineHeight: 1.9 }}>
              <strong style={{ color: T.textSecondary }}>第一步</strong>：在文本框中编写提示词，用 <code style={{ background: T.surface, padding: '1px 4px', borderRadius: '3px', color: T.accent }}>===每张发票重复执行===</code> 和 <code style={{ background: T.surface, padding: '1px 4px', borderRadius: '3px', color: '#22c55e' }}>======</code> 划分区域。<br/>
              <strong style={{ color: T.textSecondary }}>第二步</strong>：用 <code style={{ background: T.surface, padding: '1px 4px', borderRadius: '3px', color: T.accent }}>{'{{变量名}}'}</code> 引用字段，或直接用自然语言描述条件。<br/>
              <strong style={{ color: T.textSecondary }}>第三步</strong>：在右侧勾选要报销的记录。<br/>
              <strong style={{ color: T.textSecondary }}>第四步</strong>：点击「开始填报」，系统自动合并为一个报销单提交。<br/>
              <br/><span style={{ color: T.accent }}>🤖 </span><strong style={{ color: T.accent }}>默认 LLM 渲染</strong>：AI 会理解语义，自动裁剪条件分支、处理缺失字段。正则替换仅在 LLM 失败时自动备选。
            </div>
          </Card>
        </div>

        {/* ══════════ 右列 ══════════ */}
        <div style={{ display: 'grid', gap: '12px', alignContent: 'start' }}>

          {/* 选择记录 */}
          <Card style={{ padding: '14px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '10px' }}>
              <span style={{ fontSize: '13px', fontWeight: 600, color: T.text }}>
                📋 选择报销记录
                {selectedIds.size > 0 && <span style={{ marginLeft: '8px', padding: '2px 8px', borderRadius: '10px', fontSize: '11px', background: T.accent + '18', color: T.accent }}>已选 {selectedIds.size} 条</span>}
              </span>
              <div style={{ display: 'flex', gap: '4px' }}>
                <button onClick={selectAll}  style={{ ...sBtnSm, color: T.accentLight }}>全选</button>
                <button onClick={selectNone} style={{ ...sBtnSm, color: T.textMuted }}>清空</button>
              </div>
            </div>
            {!myRecords.length ? (
              <EmptyState icon="📋" title="暂无记录" subtitle="请先在「报销明细」中添加" />
            ) : (
              <div style={{ display: 'grid', gap: '4px', maxHeight: '260px', overflowY: 'auto' }}>
                {myRecords.map((r) => {
                  const checked = selectedIds.has(r.id);
                  const isRunning = executing && selectedRecords[currentIdx]?.id === r.id;
                  return (
                    <div key={r.id} onClick={() => !executing && toggleRecord(r.id)} style={{
                      display: 'flex', alignItems: 'center', gap: '10px',
                      padding: '10px 12px', borderRadius: T.radiusSm,
                      cursor: executing ? 'default' : 'pointer',
                      background: isRunning ? T.warning + '12' : checked ? T.accentGlow : T.surface,
                      border: `1px solid ${isRunning ? T.warning + '40' : checked ? T.accent + '30' : 'transparent'}`,
                      transition: 'all 0.15s', opacity: executing && !checked ? 0.5 : 1,
                    }}>
                      <div style={{
                        width: '18px', height: '18px', borderRadius: '4px', flexShrink: 0,
                        border: `2px solid ${checked ? T.accent : T.border}`,
                        background: checked ? T.accent : 'transparent',
                        display: 'flex', alignItems: 'center', justifyContent: 'center',
                        color: '#fff', fontSize: '11px', transition: 'all 0.15s',
                      }}>{checked && '✓'}</div>
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div style={{ fontSize: '13px', fontWeight: 500, color: T.text, display: 'flex', alignItems: 'center', gap: '6px' }}>
                          {r['姓名/公司']} · {r['物品简介'] || '(无摘要)'}
                          {isRunning && <Spinner size={12} />}
                        </div>
                        <div style={{ fontSize: '11px', color: T.textMuted, marginTop: '2px' }}>
                          序号 {r['序号']} · ¥{parseFloat(r['金额'] || 0).toFixed(2)} · {r['填写日期']} · {r['类别'] || '未分类'}
                          {r['发票号码'] && ` · 票号 ${r['发票号码']}`}
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </Card>

          {/* 类别警告 */}
          {selectedRecords.length > 1 && !categoryCheck.consistent && (
            <div style={{ padding: '10px 14px', background: T.warning + '10', borderRadius: T.radiusSm, border: `1px solid ${T.warning}30`, fontSize: '12px', color: T.warning }}>
              ⚠️ 选中的记录包含不同类别（{categoryCheck.categories.join('、')}），同一个报销单通常应属于同一类别。
            </div>
          )}

          {/* 转卡金额汇总 */}
          {amountSummary && Object.keys(amountSummary).length > 0 && (
            <Card style={{ padding: '12px 14px' }}>
              <div style={{ fontSize: '13px', fontWeight: 600, color: T.text, marginBottom: '8px' }}>
                💰 转卡金额汇总
                <span style={{ marginLeft: '8px', fontSize: '11px', fontWeight: 400, color: T.textMuted }}>按户名自动计算</span>
              </div>
              <div style={{ display: 'grid', gap: '4px' }}>
                {Object.entries(amountSummary).map(([name, info]) => (
                  <div key={name} style={{
                    display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                    padding: '6px 10px', background: T.surface, borderRadius: T.radiusSm,
                    border: `1px solid ${T.border}`,
                  }}>
                    <span style={{ fontSize: '12px', color: T.text, fontWeight: 500 }}>{name}</span>
                    <div style={{ textAlign: 'right' }}>
                      <span style={{ fontSize: '13px', fontWeight: 600, color: T.accent }}>¥{info.total}</span>
                      {info.detail.includes('+') && (
                        <div style={{ fontSize: '10px', color: T.textMuted, fontFamily: T.mono }}>{info.detail}</div>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </Card>
          )}

          {/* 指令预览 */}
          <Card style={{ padding: '14px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '10px' }}>
              <span style={{ fontSize: '13px', fontWeight: 600, color: T.text }}>
                🔍 合并指令预览
                {selectedRecords.length > 1 && <span style={{ marginLeft: '8px', padding: '2px 8px', borderRadius: '10px', fontSize: '11px', background: T.accent + '18', color: T.accent }}>{selectedRecords.length} 张发票 → 1 个报销单</span>}
              </span>
              <div style={{ display: 'flex', gap: '4px', alignItems: 'center' }}>
                <span style={{ padding: '2px 8px', borderRadius: '10px', fontSize: '10px', background: isAgent ? '#f59e0b15' : T.accent + '15', color: isAgent ? '#f59e0b' : T.accent, border: `1px solid ${isAgent ? '#f59e0b30' : T.accent + '30'}` }}>
                  {isAgent ? '🧠 Agent' : '🤖 LLM'}
                </span>
                {!isAgent && selectedRecords.length > 0 && (
                  <button onClick={triggerLLMPreview} disabled={llmLoading} style={{
                    ...sBtnSm, color: llmLoading ? T.textMuted : T.accent,
                    background: T.accent + '08', border: `1px solid ${T.accent}30`,
                    borderRadius: '10px', fontSize: '11px', padding: '3px 10px',
                  }}>{llmLoading ? '渲染中...' : '🔄 LLM 预览'}</button>
                )}
              </div>
            </div>

            {!selectedRecords.length ? (
              <div style={{ padding: '20px', textAlign: 'center', fontSize: '12px', color: T.textMuted }}>← 请先在上方勾选报销记录</div>
            ) : (
              <>
                {missingInPreview.length > 0 && (
                  <div style={{ padding: '8px 12px', marginBottom: '8px', background: T.warning + '10', borderRadius: T.radiusSm, border: `1px solid ${T.warning}30`, fontSize: '12px', color: T.warning }}>
                    ⚠️ 该记录缺少字段: {missingInPreview.join('、')}，LLM 会智能处理
                  </div>
                )}

                {llmLoading && (
                  <div style={{ padding: '8px 12px', marginBottom: '8px', background: T.accent + '08', borderRadius: T.radiusSm, border: `1px solid ${T.accent}20`, fontSize: '12px', color: T.accent, display: 'flex', alignItems: 'center', gap: '8px' }}>
                    <Spinner size={12} /> 正在调用后端 LLM 渲染...
                  </div>
                )}

                {llmError && (
                  <div style={{ padding: '8px 12px', marginBottom: '8px', background: T.danger + '10', borderRadius: T.radiusSm, border: `1px solid ${T.danger}30`, fontSize: '12px', color: T.danger }}>
                    ⚠️ LLM 预览失败: {llmError}（执行时将自动重试或回退正则）
                  </div>
                )}

                <div style={{
                  padding: '12px 14px', background: T.bg, borderRadius: T.radiusSm,
                  border: `1px solid ${T.border}`, fontSize: '12px', fontFamily: T.font,
                  color: T.text, lineHeight: 1.8, whiteSpace: 'pre-wrap',
                  maxHeight: '240px', overflowY: 'auto',
                  opacity: llmLoading ? 0.5 : 1, transition: 'opacity 0.2s',
                }}>
                  {!isAgent && !llmPreview && !llmLoading ? (
                    <div style={{ textAlign: 'center', color: T.textMuted, padding: '12px' }}>
                      当前显示即时预览。点击「🔄 LLM 预览」查看 AI 渲染效果。
                      <br/><span style={{ fontSize: '11px' }}>执行时会自动使用 LLM 渲染，正则仅在 LLM 失败时备选。</span>
                    </div>
                  ) : previewText}
                </div>

                {!isAgent && llmPreview && (
                  <div style={{ marginTop: '6px', fontSize: '11px', color: T.textMuted, display: 'flex', alignItems: 'center', gap: '4px' }}>
                    <span style={{ color: T.success }}>✓</span> LLM 渲染完成
                    {llmPreview.length !== regexPreviewText.length && <span>（与正则差异: {Math.abs(llmPreview.length - regexPreviewText.length)} 字符）</span>}
                  </div>
                )}

                <div style={{ marginTop: '12px', display: 'flex', gap: '8px', alignItems: 'center' }}>
                  <Button onClick={handleExecute} disabled={executing || !targetUrl} style={{ flex: 1, justifyContent: 'center', padding: '11px', fontSize: '14px' }}>
                    {executing ? <><Spinner size={14} /> 填报中...</> : `🚀 开始填报 (${selectedRecords.length} 张发票 → 1 个报销单)`}
                  </Button>
                </div>
                {!targetUrl && <div style={{ marginTop: '6px', fontSize: '11px', color: T.warning }}>⚠️ 请先填写目标报销系统网址</div>}
              </>
            )}
          </Card>

          {/* 执行日志 */}
          <Card style={{ padding: '0', display: 'flex', flexDirection: 'column', maxHeight: '320px' }}>
            <div style={{ padding: '10px 14px', borderBottom: `1px solid ${T.border}`, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <span style={{ fontSize: '13px', fontWeight: 600, color: T.text }}>📜 执行日志</span>
              <button onClick={() => setLogs([])} style={{ ...sBtnSm, color: T.textMuted }}>清空</button>
            </div>
            <div style={{ flex: 1, overflowY: 'auto', padding: '10px 14px' }}>
              {!logs.length ? (
                <div style={{ textAlign: 'center', padding: '24px 0', color: T.textMuted, fontSize: '12px' }}>勾选记录并点击「开始填报」后，日志将实时显示</div>
              ) : logs.map((log, i) => (
                <div key={i} style={{ marginBottom: '6px', borderLeft: `2px solid ${LOG_COLORS[log.type] || T.info}`, paddingLeft: '10px', animation: 'fadeIn 0.2s ease' }}>
                  <div style={{ fontSize: '10px', color: T.textMuted, fontFamily: T.mono }}>{log.time}</div>
                  <div style={{ fontSize: '12px', fontFamily: T.mono, color: LOG_COLORS[log.type] || T.text, whiteSpace: 'pre-wrap', lineHeight: 1.5 }}>{log.text}</div>
                </div>
              ))}
              <div ref={logsEndRef} />
            </div>
          </Card>
        </div>
      </div>
    </div>
  );
}