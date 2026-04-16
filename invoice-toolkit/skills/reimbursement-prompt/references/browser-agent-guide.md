# Browser Agent 集成说明

## 架构

`BrowserAgent`（`browser_agent.py`）是一个基于 Playwright + LLM 的单步决策浏览器自动化框架。它接收一段纯文本任务指令，逐步操作网页直到完成。

## 数据流

```
前端渲染好的提示词
  ↓ POST /api/browser-task {task, url, record_ids}
后端创建 BrowserAgent
  ↓ agent.run(task, url)
循环：
  _get_page_state() → DOM提取 + 格式化
  _call_llm()       → 发送 task+DOM+history → LLM返回 Action
  _execute_action() → Playwright 执行
  记录结果 → 下一步
  ↓
返回 BrowserTaskResult
```

## 可用动作类型

| 动作 | 必填参数 | 说明 |
|------|----------|------|
| `click` | index | 点击元素 |
| `fill` | index, value | 填写输入框（自动移除 readonly） |
| `js_fill` | index, value | JS 直接设值（fill 失败时降级） |
| `select` | index, value | 下拉框选择 |
| `upload_file` | index, value(路径) | 上传文件到 file input |
| `scroll` | value(像素) | 滚动页面 |
| `wait` | value(秒) | 等待 |
| `go_to_url` | value(URL) | 导航 |
| `js_click` | selector(CSS) | JS 点击（常规 click 无效时） |
| `skip` | value(原因) | 跳过当前步骤（数据/业务错误时） |
| `done` | value(说明) | 任务完成 |

## DOM 提取优化

对标 browser-use 实现的能力：
1. **Shadow DOM 穿透** — 递归遍历所有 open shadow root
2. **遮挡检测** — `elementFromPoint` 排除被覆盖的元素
3. **事件监听器追踪** — monkey-patch `addEventListener` 捕获框架事件
4. **视口感知排序** — viewport 内元素优先
5. **新增元素标记** — 与上一步 diff，新出现的元素标 `*新增*`
6. **模态弹窗检测** — 自动过滤弹窗外元素，提示 LLM 先处理弹窗
7. **iframe 合并** — 主页面 + iframe 元素去重合并

## 安全机制

- **循环检测**：LLM 应在第 2 次重复时自主 skip；代码层面在同一模式重复 4 次时强制跳过
- **连续失败**：达到 `max_failures` 次连续失败时终止
- **LLM 调用限流**：`max_llm_calls` 上限 + `llm_call_interval` 间隔
- **超时**：总任务超时保护
- **skip 后处理**：自动尝试关闭弹窗，记录错误到数据库

## record_ids 集成

前端 `POST /api/browser-task` 时额外传入 `record_ids` 数组，后端用于初始化 `record_db`。当 browser agent 的 LLM 执行 skip 时，可通过提示词开头的「记录ID映射」找到对应的 `record_id`，写入跳过原因。

## 渲染流程全图

```
前端勾选记录
    ↓
calculateTransferAmounts()
    ↓
按「姓名/公司」分组计算转卡金额 + 按路径去重附件
    ↓
注入 转卡金额/转卡明细/转卡汇总/附件汇总/记录ID映射
    ↓
加载模板（Skill 文件 或 localStorage）
    ↓
parseSections() 解析 once/repeat 区段
    ↓
正则替换 或 LLM 渲染
    ↓
输出纯文本提示词 + record_ids
    ↓
POST /api/browser-task
    ↓
BrowserAgent.run() → Playwright 执行
```
