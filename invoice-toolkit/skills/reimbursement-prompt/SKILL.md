---
name: reimbursement-prompt
description: >
  报销提示词模板技能 — 用于生成报销系统自动填报的浏览器操作指令。
  当需要执行报销填报、生成报销操作提示词、渲染报销模板、调试 browser agent
  报销流程时使用此技能。触发场景包括：用户提到「报销」「填报」「提示词模板」
  「自动报销」「生成报销指令」「browser agent 报销」「附件上传」「转卡金额」
  等关键词时，务必加载本技能。即使用户只是想预览、编辑模板或排查报销执行问题，
  也应优先触发。
---

# 报销提示词模板技能

## 概述

本技能提供报销系统自动填报所需的**模板**和**纯计算工具**。

技能本身不包含任何业务编排逻辑（Agent 循环、LLM 调用、数据库访问）。
编排引擎位于后端 `invoice_toolkit/agent_orchestrator.py`，通过 bash 调用
本技能的脚本。

### 三种执行模式

| 模式 | 流程编排者 | 说明 |
|------|-----------|------|
| ⚡ 经典替换 | 前端代码 | 前端写死流程：计算→渲染→执行，速度快但不灵活 |
| 🤖 LLM 渲染 | 前端代码 + LLM 渲染 | 前端写死流程，但模板渲染由 LLM 执行，支持条件分支 |
| 🧠 Agent 自主编排 | **LLM 自主决策** | LLM 通过 function calling 自己决定调用工具的顺序 |

## 文件结构

```
skills/reimbursement-prompt/
├── SKILL.md                          ← 你正在读的文件
├── scripts/
│   ├── __init__.py                   ← 包入口（仅导出纯函数）
│   └── calculate_amounts.py          ← 金额汇总、附件去重、ID映射
├── templates/
│   └── default.md                    ← 默认提示词模板
└── references/
    ├── template-syntax.md            ← 模板语法详解
    └── browser-agent-guide.md        ← browser agent 动作类型
```

> Agent 编排引擎 `agent_orchestrator.py` 位于后端 `invoice_toolkit/` 目录，
> 不在 Skill 内。

## Skill 工具（CLI 调用）

Skill 脚本通过 **stdin/stdout JSON** 与外部交互，无需任何外部依赖。

### calculate_amounts.py

```bash
echo '{"records": [...]}' | python skills/reimbursement-prompt/scripts/calculate_amounts.py
```

**输入** (stdin JSON):
```json
{
  "records": [
    {"db_id": 12, "序号": 1, "姓名/公司": "张三", "金额": 100, "发票号码": "12345678", "匹配附件": true, "附件路径": "/path/to/file.pdf"},
    ...
  ]
}
```

**输出** (stdout JSON):
```json
{
  "success": true,
  "amount_by_person": {"张三": 300.0},
  "detail_by_person": {"张三": "100.00+200.00=300.00"},
  "transfer_summary": "在「转卡」区域中找到户名为张三的那一行...",
  "attachment_summary": "在补充说明中上传附件...",
  "record_id_map": "本报销单包含 2 条记录...",
  "enriched_records": [...]
}
```

## Agent 自主编排模式

### 工作原理

后端 `invoice_toolkit/agent_orchestrator.py` 实现 LLM Agent 循环：

```
用户点击「开始填报」
  ↓
前端发送 POST /api/agent-reimbursement { record_ids, target_url }
  ↓
后端启动 Agent 循环：
  while not done:
    1. 将当前对话历史发送给 LLM（含可用工具列表）
    2. LLM 返回一个 tool_call（它自己决定调哪个工具）
    3. 执行该工具（calculate_amounts 通过 bash 调 Skill 脚本）
    4. LLM 根据结果决定下一步
  ↓
返回完整的步骤日志给前端
```

### LLM 可用的工具

| 工具名 | LLM 何时调用 | 实现位置 |
|--------|------------|----------|
| `read_records` | 需要获取完整记录数据时 | 后端 agent_orchestrator |
| `calculate_amounts` | 拿到记录后、渲染前 | **Skill 脚本**（bash 调用） |
| `load_template` | 需要模板时 | **Skill 文件**（读取 templates/） |
| `render_template` | 有了模板和计算后的记录时 | 后端 agent_orchestrator |
| `done` | 所有步骤完成时 | 后端 agent_orchestrator |

## 类别与报销入口映射

渲染模板时，系统根据 `{{类别}}` 字段自动确定应点击的报销入口按钮：

| 类别 | 点击按钮 |
|------|----------|
| 材料、快递 | 「日常报销(专用材料、邮寄费、办公费等)」 |
| 出差 | 「国内差旅」 |
| 手机通讯费 | 「手机通讯费(仅限横向科研及个人科研基金)」 |
| 加班餐 | 「科研业务专项费(科研燃油、加班及接待餐)」 |
| 试剂耗材 | 「试剂耗材管理平台预约报销」 |

## MCP 工具

| 工具 | 用途 | 触发者 |
|------|------|--------|
| `invoice_calculate_amounts` | 按户名分组计算转卡金额 | 前端 / Agent |
| `invoice_get_prompt_template` | 读取 Skill 模板 | 前端 / Agent |
| `invoice_run_reimbursement` | **LLM Agent 自主编排完整流程** | 前端 Agent 模式 / MCP 客户端 |

## HTTP 端点

| 端点 | 用途 | 模式 |
|------|------|------|
| `POST /api/agent-reimbursement` | LLM 自主编排报销（Agent 模式） | 🧠 Agent |
| `POST /api/browser-task` | 执行已渲染的指令（固定流程） | ⚡ 经典 / 🤖 LLM |
| `POST /api/render-prompt` | LLM 渲染模板 | 🤖 LLM |
| `POST /api/calculate-amounts` | 计算转卡金额 | ⚡ 经典 / 🤖 LLM |
| `GET /api/prompt-template` | 获取 Skill 模板 | 所有模式 |

## 模板语法（详见 `references/template-syntax.md`）

- **区段**：`===标题===` ... `======` 定义重复区段，其余为一次性区段
- **变量**：`{{变量名}}` 引用数据字段
- **计算字段**：`{{转卡汇总}}`、`{{附件汇总}}`、`{{记录ID映射}}` 由 `calculate_amounts` 自动注入

## 注意事项

1. 同一报销单中的记录应属于同一类别
2. 金额计算基于选中记录，非全部数据库记录
3. LLM 渲染失败时自动回退正则替换
4. **附件上传必须按路径去重**——由 `calculate_amounts` 自动处理
5. Agent 模式下 LLM 会读取本 SKILL.md 作为背景知识，了解类别映射、模板语法等规则
6. Agent 模式设有 `max_steps` 上限（默认10），防止无限循环
7. **Skill 内脚本零外部依赖**——只用 Python 标准库，可通过 bash 独立运行
