/**
 * main.jsx — 应用入口（已集成 Page Agent）
 * 文件位置：src/main.jsx
 *
 * 改动：末尾追加 Page Agent 初始化。
 * Agent 在页面右下角显示浮窗面板，用户直接用自然语言操控当前页面。
 * LLM 配置从 .env → docker-compose args → Vite 环境变量注入。
 */
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.jsx'

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <App />
  </StrictMode>,
)

/* ═══════════════════════════════════════════════════════
 *  Page Agent 初始化
 *
 *  .env 传递链路：
 *    SILICONFLOW_API_KEY  → VITE_LLM_API_KEY  → import.meta.env
 *    LLM_MODEL_NAME       → VITE_LLM_MODEL    → import.meta.env
 *    LLM_BASE_URL         → VITE_LLM_BASE_URL → import.meta.env
 *
 *  Docker 部署走 /llm-proxy/v1 (Nginx)，本地开发直连 API
 * ═══════════════════════════════════════════════════════ */
async function initPageAgent() {
  try {
    const { PageAgent } = await import('page-agent')

    const apiKey = import.meta.env.VITE_LLM_API_KEY || ''
    const model  = import.meta.env.VITE_LLM_MODEL   || 'Qwen/Qwen3-8B'
    const baseURL = import.meta.env.DEV
      ? (import.meta.env.VITE_LLM_BASE_URL || 'https://api.siliconflow.cn/v1')
      : '/llm-proxy/v1'

    if (!apiKey) {
      console.warn(
        '[PageAgent] 未配置 API Key。\n' +
        '请在 invoice-system/.env 中填写 SILICONFLOW_API_KEY，然后重新 docker-compose build。'
      )
      return
    }

    const agent = new PageAgent({ apiKey, baseURL, model, language: 'zh-CN' })

    // 挂到 window 方便调试
    window.__agent = agent

    console.log(`[PageAgent] ✅ 已启动 | 模型: ${model} | 接口: ${baseURL}`)
  } catch (err) {
    console.warn('[PageAgent] 初始化跳过:', err.message)
  }
}

initPageAgent()
