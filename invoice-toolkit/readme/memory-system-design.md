# 报销浏览器探索 Agent 记忆系统设计

## 一、问题分析

### 当前方案的瓶颈

当前 `default.md` 本质上是一份**硬编码的程序性记忆**——人类手工写死了每一步点击顺序。这带来三个核心问题：

1. **脆弱性**：网页 UI 一改（按钮名变了、流程加了一步），模板就废了，需要人工重写
2. **不可迁移**：换一个报销系统，所有模板从零开始
3. **无法积累**：agent 每次执行都是"失忆"状态，上次撞过的坑（弹窗遮挡、元素不可见）下次照撞

### 设计目标

探索 agent 自主点击 → 发现可行路径 → 存入记忆 → 下次直接复用或在此基础上优化。最终让 `default.md` 这样的静态模板退化为"初始种子"，agent 的实际行为由积累的记忆驱动。

---

## 二、记忆架构总览

借鉴 COALA 论文的三类记忆分类、Claude Code 的分层 Markdown 文件系统、browser-use 的步级自我摘要机制，设计如下四层记忆：

```
memory/
├── MEMORY.md                    ← 索引文件（指针，类似 Claude Code 的 memory.md）
├── procedural/                  ← 程序性记忆：怎么做
│   ├── flows/
│   │   ├── 日常报销.flow.md     ← 一个完整流程的动作序列
│   │   ├── 国内差旅.flow.md
│   │   └── 试剂耗材.flow.md
│   └── fragments/
│       ├── 上传发票.frag.md     ← 可复用的操作片段
│       ├── 填写联系人.frag.md
│       └── 选择经费项目.frag.md
├── semantic/                    ← 语义记忆：知道什么
│   ├── ui-map.md                ← 页面元素知识图谱
│   ├── rules.md                 ← 业务规则（类别映射等）
│   └── field-patterns.md        ← 表单字段的类型/格式规律
├── episodic/                    ← 情景记忆：经历过什么
│   ├── sessions/
│   │   ├── 2025-01-15_日常报销_张三.ep.md
│   │   └── 2025-01-16_国内差旅_李四.ep.md
│   └── errors/
│       ├── modal-blocking.md    ← 弹窗遮挡问题汇总
│       └── element-not-found.md ← 元素找不到问题汇总
└── meta/
    ├── confidence.json          ← 每段记忆的置信度/使用次数
    └── changelog.md             ← 记忆变更日志
```

---

## 三、各层记忆详细设计

### 3.1 程序性记忆（Procedural Memory）— 怎么做

**灵感来源**：browser-use 的 `AgentHistoryList`（可保存/重放的动作序列）+ Claude Code 的 `.claude/rules/` 模块化规则文件。

#### Flow 文件格式

每个 `.flow.md` 记录一条**端到端的完整操作路径**，相当于当前 `default.md` 的学习版：

```markdown
---
flow_id: daily-reimbursement-v3
category: 日常报销
confidence: 0.92
success_count: 14
fail_count: 2
last_used: 2025-01-15T10:30:00
last_updated: 2025-01-14T16:00:00
source: exploration          # exploration | manual | merged
preconditions:
  - 已登录报销系统
  - 至少有一条待报销记录
---

# 日常报销完整流程

## Step 1: 进入报销入口
- action: click
- target: 「智能报销」按钮
- wait_after: page_load
- fallback: 如果按钮不可见，先向下滚动 500px

## Step 2: 选择报销类型
- action: click
- target: 「日常报销(专用材料、邮寄费、办公费等)」按钮
- precondition: Step 1 成功后页面出现报销类型列表
- notes: 类别到按钮的映射见 semantic/rules.md#类别映射

## Step 3: 处理发票 [repeat: each 发票]
- fragment: 上传发票.frag.md
- input: { 发票号码, 发票路径 }
- on_skip: 记录 record_id 到跳过列表

## Step 4: 前往报销
- action: click
- target: 「前往报销」按钮
- precondition: 所有发票复选框已选中
- verify: 页面跳转到报销详情页

## Step 5: 填写联系人
- fragment: 填写联系人.frag.md

## Step 6: 选择经费项目
- fragment: 选择经费项目.frag.md
- input: { 项目代码 }

## Step 7: 上传附件
- fragment: 上传附件.frag.md
- input: { 附件汇总 }

## Step 8: 提交
- action: click
- target: 「返回」按钮
```

#### Fragment 文件格式

可复用的操作片段，被多个 flow 引用（类似函数）：

```markdown
---
fragment_id: upload-invoice
confidence: 0.95
avg_duration_sec: 8.5
success_count: 42
known_issues:
  - "readonly 属性导致 fill 失败，需降级 js_fill"
  - "大文件上传可能触发超时，需 wait 10s"
---

# 上传发票

## 前置检查
在票号列中查找「{{发票号码}}」。

## 分支 A: 发票已存在
- condition: 找到匹配行
- action: 检查该行复选框状态
  - 已选中 → 跳过，返回 success
  - 未选中 → click 该行复选框

## 分支 B: 发票不存在
1. click 「上传发票」按钮
2. click 「上传附件」按钮
3. upload_file 「{{发票路径}}」
4. click 「保存发票」按钮
5. wait 2s（等待列表刷新）
6. click 新出现的发票行的复选框
```

#### 与 default.md 的关系

| 维度 | default.md（现有） | flow.md（新系统） |
|------|-------|---------|
| 来源 | 人工编写 | 探索自动生成 + 人工可编辑 |
| 结构 | 纯文本，once/repeat 区段 | 结构化 step + fragment 引用 |
| 适应性 | 死板 | 有 fallback、分支、置信度 |
| 演进 | 手动维护 | 每次执行后自动更新置信度 |

**迁移策略**：现有 `default.md` 可一次性转换为一个 flow + 若干 fragment，作为初始种子记忆。

---

### 3.2 语义记忆（Semantic Memory）— 知道什么

**灵感来源**：Claude Code 的 `CLAUDE.md`（项目知识）+ LangChain Agent Builder 的"facts store"。

语义记忆存储 agent 对报销系统的**认知和理解**，不绑定具体执行序列。

#### ui-map.md — 页面元素知识图谱

```markdown
# 报销系统 UI 地图

## 首页 (https://reimburse.example.com/home)
- 「智能报销」按钮 → 进入报销类型选择页
- 「报销记录」按钮 → 进入历史记录页
- 「个人中心」链接 → 右上角

## 报销类型选择页
- 包含 5 个报销入口按钮（见 rules.md#类别映射）
- 页面底部有「返回首页」链接

## 发票选择页
- 顶部：已有发票列表（表格，列：票号/金额/日期/操作）
- 每行有复选框
- 底部：「上传发票」按钮、「前往报销」按钮
- ⚠️ 「前往报销」按钮在至少选中一张发票后才可点击

## 报销详情页
- 「点击修改信息」链接 → 展开联系人表单
  - 手机输入框、联系电话输入框
  - 报销投递校区下拉框（选项：千佛山校区、趵突泉校区...）
- 「请填写报销项目」链接 → 弹出经费项目选择窗口
  - 底部「填写他人项目」区域
  - 项目代码输入框、项目负责人姓名输入框
- 「请填写说明」区域 → 附件上传区
  - 「上传附件」按钮
```

这份 UI 地图由探索 agent 在每次交互中逐步补充，记录的不是 DOM 细节，而是**人类可理解的页面结构**。

#### rules.md — 业务规则

```markdown
# 业务规则

## 类别映射（来源：SKILL.md，经实际验证）
| 类别 | 按钮文本 | 验证状态 |
|------|---------|---------|
| 材料、快递 | 日常报销(专用材料、邮寄费、办公费等) | ✅ 已验证 |
| 出差 | 国内差旅 | ✅ 已验证 |
| 手机通讯费 | 手机通讯费(仅限横向科研及个人科研基金) | ⚠️ 未测试 |

## 表单规则
- 手机号：固定填写 12345678912
- 报销投递校区：固定选择「千佛山校区」
- 项目负责人姓名：只需填写姓氏「陈」，系统自动匹配

## 发现的隐式规则
- 发票上传后需等待 2-3 秒列表才刷新
- 同一发票号码不能重复上传，会弹出错误提示
- 附件大小限制约 10MB
```

---

### 3.3 情景记忆（Episodic Memory）— 经历过什么

**灵感来源**：browser-use 的 `memory` 参数（每步自我摘要）+ 论文中描述的"rolling buffer + compressed older steps"。

#### Session 文件

每次探索/执行会生成一份 session 记录：

```markdown
---
session_id: ep-20250115-001
timestamp: 2025-01-15T10:30:00
mode: exploration          # exploration | execution | replay
category: 日常报销
records: [12, 15, 18]
outcome: partial_success
duration_sec: 145
steps_total: 23
steps_succeeded: 20
steps_failed: 3
flow_used: daily-reimbursement-v3    # null if pure exploration
---

# 执行摘要
为张三的 3 条日常报销记录执行自动填报。2 张发票成功上传并选中，
1 张发票（票号 99887766）因"发票号码重复"被跳过（record_id=18）。
联系人信息和经费项目填写正常。附件上传成功。

# 关键事件
- Step 5: 发票 99887766 上传时弹出「该发票已存在」提示框 → 执行 skip
- Step 12: 「报销投递校区」下拉框第一次点击无反应，第二次成功
- Step 18: 附件上传耗时 12 秒（文件 3.2MB），接近超时阈值

# 新发现
- 「该发票已存在」提示框是模态弹窗，需先点「确定」关闭
- 下拉框偶尔需要等待 1 秒后再点击

# 记忆更新建议
- → procedural/fragments/上传发票.frag.md: 增加「发票已存在」弹窗处理分支
- → semantic/ui-map.md: 补充「发票已存在」模态弹窗描述
- → episodic/errors/modal-blocking.md: 新增案例
```

#### Error 汇总文件

按错误类型聚合的经验库，让 agent 快速查找"以前遇到过类似问题吗"：

```markdown
---
error_type: modal-blocking
occurrences: 7
last_seen: 2025-01-15
---

# 模态弹窗遮挡问题

## 模式
弹窗出现后，底层元素不可点击。agent 必须先处理弹窗。

## 已知弹窗
| 弹窗内容 | 触发场景 | 处理方法 |
|---------|---------|---------|
| 「该发票已存在」 | 重复上传发票 | 点击「确定」关闭 |
| 「保存成功」 | 表单保存后 | 点击「确定」或等待自动关闭 |
| 「确认提交？」 | 最终提交前 | 根据任务需要点「确定」或「取消」|

## 通用恢复策略
1. 检测到点击失败时，先用 DOM 检查是否存在模态遮罩层
2. 如果存在，尝试点击弹窗上的关闭/确定按钮
3. 如果弹窗无按钮，尝试按 Escape
4. 重新执行被阻断的操作
```

---

### 3.4 元记忆（Meta Memory）— 记忆的记忆

#### confidence.json — 置信度追踪

```json
{
  "flows": {
    "daily-reimbursement-v3": {
      "confidence": 0.92,
      "success_count": 14,
      "fail_count": 2,
      "last_used": "2025-01-15T10:30:00",
      "avg_duration_sec": 120,
      "decay_factor": 0.98
    }
  },
  "fragments": {
    "upload-invoice": {
      "confidence": 0.95,
      "success_count": 42,
      "fail_count": 2
    }
  },
  "semantic": {
    "ui-map.md": {
      "last_verified": "2025-01-15",
      "staleness_days": 0
    }
  }
}
```

**置信度衰减**：长时间未使用的记忆，置信度按 `decay_factor ^ 天数` 衰减。这避免过度依赖可能已过时的旧记忆。

---

## 四、记忆生命周期

### 4.1 写入：探索后如何生成记忆

```
探索 Agent 完成一次任务
     │
     ├─ 成功路径 ──────────────────────────────────┐
     │                                              ▼
     │                              提取动作序列 → 生成/更新 flow.md
     │                              提取新 UI 知识 → 更新 ui-map.md
     │                              提取业务规则 → 更新 rules.md
     │                              生成 session 摘要 → 写入 episodic/
     │                              更新置信度 → confidence.json
     │
     ├─ 失败路径 ──────────────────────────────────┐
     │                                              ▼
     │                              分析失败原因 → 更新 errors/
     │                              如果是新的失败模式 → 创建新 error 文件
     │                              降低相关 flow/fragment 置信度
     │                              生成 session 摘要（标记失败点）
     │
     └─ 部分成功 ─────────────────────────────────┐
                                                    ▼
                                    成功部分 → 提取/增强 fragment
                                    失败部分 → 分析原因，建议 fallback
                                    生成 session 摘要（标记分界点）
```

### 4.2 读取：执行时如何使用记忆

参考 Claude Code 的分层加载策略：

```
Agent 收到任务（类别=日常报销，records=[...]）
     │
     ▼
1. 加载 MEMORY.md 索引（始终加载，<200行）
     │
     ▼
2. 根据类别匹配 flow
   ├─ 找到高置信度 flow → 进入「执行模式」（类似 browser-use flash_mode）
   │   加载对应 flow.md + 引用的 fragment
   │   跳过评估和推理，直接按序列执行
   │
   ├─ 找到低置信度 flow → 进入「验证模式」
   │   加载 flow 但每步都验证 DOM 状态
   │   随时准备分支或回退
   │
   └─ 未找到 flow → 进入「探索模式」
       加载 ui-map.md + rules.md 作为参考
       加载相关 error 文件作为避坑指南
       自主探索，每步记录
     │
     ▼
3. 按需加载（on-demand）
   - 遇到弹窗 → 查 errors/modal-blocking.md
   - 需要填表 → 查 field-patterns.md
   - 遇到新页面 → 查 ui-map.md 看是否有记录
```

### 4.3 整合：Dream 机制（借鉴 Claude Code Auto Dream）

定期运行**记忆整合**，避免记忆膨胀和碎片化：

```python
# 触发条件（满足任一）：
# 1. 新增 session 超过 10 个且距上次整合 > 24h
# 2. MEMORY.md 超过 150 行
# 3. 用户手动触发

def dream_consolidation():
    """记忆整合：去重、合并、淘汰"""
    
    # 1. Fragment 合并
    # 多个 session 中重复出现的成功操作片段 → 提取为独立 fragment
    
    # 2. Flow 版本升级
    # 如果最近 5 次执行中有 3 次走了不同分支 → 更新 flow 的分支结构
    
    # 3. Error 去重
    # 相同原因的错误合并为一条，增加计数
    
    # 4. 过期淘汰
    # 置信度衰减到 < 0.3 且超过 30 天未使用 → 归档到 archive/
    
    # 5. MEMORY.md 瘦身
    # 重新生成索引，只保留高频/高置信度的指针
```

---

## 五、MEMORY.md 索引文件设计

类似 Claude Code 的 `memory.md` 作为指针索引，不存储具体内容：

```markdown
# 报销 Agent 记忆索引

## 可用流程（按置信度排序）
- [日常报销 v3](procedural/flows/日常报销.flow.md) — 置信度 0.92, 14次成功
- [国内差旅 v2](procedural/flows/国内差旅.flow.md) — 置信度 0.85, 8次成功
- [试剂耗材 v1](procedural/flows/试剂耗材.flow.md) — 置信度 0.60, 3次成功 ⚠️

## 常用片段
- [上传发票](procedural/fragments/上传发票.frag.md) — 置信度 0.95
- [填写联系人](procedural/fragments/填写联系人.frag.md) — 置信度 0.98
- [选择经费项目](procedural/fragments/选择经费项目.frag.md) — 置信度 0.90

## 已知问题（最近 7 天活跃）
- [模态弹窗遮挡](episodic/errors/modal-blocking.md) — 7次出现
- [元素不可见](episodic/errors/element-not-found.md) — 3次出现

## 最近更新
- 2025-01-15: 更新上传发票片段，增加「发票已存在」分支
- 2025-01-14: 日常报销流程升级到 v3

## 系统知识
- [UI 地图](semantic/ui-map.md) — 最后验证 2025-01-15
- [业务规则](semantic/rules.md)
- [表单字段规律](semantic/field-patterns.md)
```

---

## 六、探索 → 记忆 的提取管道

探索 agent 执行完毕后，调用一个 **Memory Writer**（可以是独立的 LLM 调用，类似 Claude Code 的 dream sub-agent）来生成记忆：

```python
class MemoryWriter:
    """从执行历史中提取记忆，写入文件系统"""
    
    def process_session(self, session_history: AgentHistoryList):
        # Step 1: 生成 session 摘要（episodic）
        episode = self._summarize_session(session_history)
        self._write_episode(episode)
        
        # Step 2: 提取/更新 flow（procedural）
        if episode.outcome in ('success', 'partial_success'):
            action_sequence = self._extract_action_sequence(session_history)
            self._update_or_create_flow(action_sequence, episode)
        
        # Step 3: 提取可复用片段（procedural fragments）
        fragments = self._identify_reusable_fragments(session_history)
        for frag in fragments:
            self._update_or_create_fragment(frag)
        
        # Step 4: 更新 UI 知识（semantic）
        new_ui_knowledge = self._extract_ui_knowledge(session_history)
        self._merge_ui_map(new_ui_knowledge)
        
        # Step 5: 更新错误模式（episodic errors）
        errors = self._extract_error_patterns(session_history)
        self._update_error_files(errors)
        
        # Step 6: 更新置信度（meta）
        self._update_confidence(episode)
        
        # Step 7: 重建索引
        self._rebuild_memory_index()
    
    def _summarize_session(self, history) -> Episode:
        """用 LLM 从完整历史中生成结构化摘要"""
        # 类似 browser-use 的 memory 参数：
        # 每步的 long_term_memory 字段聚合为 session 摘要
        prompt = f"""
        分析以下浏览器操作历史，生成结构化的 session 摘要。
        重点关注：
        1. 整体流程是否成功
        2. 哪些步骤遇到了问题及解决方法
        3. 发现了哪些新的 UI 元素或业务规则
        4. 有哪些操作可以提取为可复用片段
        
        操作历史：
        {history.to_json()}
        """
        return llm.generate(prompt, output_schema=Episode)
    
    def _identify_reusable_fragments(self, history) -> list:
        """识别可复用的操作片段
        
        规则：
        - 连续 3+ 步操作构成一个逻辑单元
        - 该单元在不同 session 中出现 2+ 次
        - 该单元有清晰的输入/输出边界
        """
        pass
```

---

## 七、与现有系统的集成

### 7.1 对接 calculate_amounts.py

`calculate_amounts.py` 的计算结果（转卡汇总、附件汇总等）作为**运行时上下文**注入，不进入长期记忆。但其计算逻辑的**使用模式**可以被记忆：

```
semantic/rules.md 中记录：
- 转卡金额按「姓名/公司」分组累加
- 附件按路径去重
- 记录ID映射放在提示词最开头

这些是 agent 从多次执行中"学到"的规则，
即使 calculate_amounts.py 不可用，agent 也知道该做什么。
```

### 7.2 对接 BrowserAgent

```python
class MemoryAwareBrowserAgent(BrowserAgent):
    """在原有 BrowserAgent 基础上集成记忆系统"""
    
    def __init__(self, memory_dir: str, **kwargs):
        super().__init__(**kwargs)
        self.memory = MemoryStore(memory_dir)
    
    async def run(self, task: str, url: str):
        # 1. 加载相关记忆
        category = self._detect_category(task)
        flow = self.memory.get_best_flow(category)
        
        if flow and flow.confidence > 0.85:
            # 高置信度：直接执行（flash mode）
            result = await self._execute_flow(flow, url)
        elif flow:
            # 低置信度：带验证执行
            result = await self._execute_with_verification(flow, url)
        else:
            # 无记忆：探索模式
            context = self.memory.get_exploration_context(category)
            result = await self._explore(task, url, context)
        
        # 2. 执行后写入记忆
        writer = MemoryWriter(self.memory)
        writer.process_session(result.history)
        
        return result
```

### 7.3 模板系统的演进

```
阶段 1（当前）: 静态模板 default.md → 正则替换 → 提示词
阶段 2（过渡）: 静态模板作为种子 → 转换为 flow.md → agent 在 flow 基础上执行
阶段 3（目标）: 无需种子 → agent 从 UI map + rules 自主探索 → 生成 flow → 下次复用
```

---

## 八、关键设计决策

### 8.1 为什么用 Markdown 文件而不用数据库？

借鉴 Claude Code 和 BrowserOS 的选择：

| 考量 | Markdown 文件 | 数据库 |
|------|-------------|--------|
| 可调试性 | 直接打开文件看内容 | 需要查询工具 |
| LLM 友好度 | LLM 天然理解 Markdown | 需要转换格式 |
| 可编辑性 | 用户可直接修改 | 需要管理界面 |
| 版本控制 | Git 天然支持 | 需要额外方案 |
| 部署复杂度 | 零依赖 | 需要数据库服务 |

**唯一的补充**：`confidence.json` 用 JSON 存储结构化元数据，因为需要频繁更新数值。

### 8.2 为什么分 flow 和 fragment？

**flow** = 端到端的完整路径（"怎么报销日常费用"）
**fragment** = 可复用的操作单元（"怎么上传一张发票"）

分离的好处：
1. 不同类别的 flow 可以共享 fragment（日常报销和国内差旅都需要"上传发票"）
2. fragment 的置信度可以独立于 flow（"上传发票"已经很可靠了，但整个"差旅报销"流程还在探索中）
3. 探索时可以局部复用（碰到已知的子流程直接用 fragment，只探索未知部分）

### 8.3 置信度模型

```
confidence = base_confidence * decay_factor ^ days_since_last_use

其中：
  base_confidence = success_count / (success_count + fail_count * 3)
  decay_factor = 0.995（约 30 天衰减到 86%，60 天衰减到 74%）

阈值：
  > 0.85: 直接执行（flash mode）
  0.5 ~ 0.85: 带验证执行
  0.3 ~ 0.5: 需要探索增强
  < 0.3: 归档，视为不可靠
```

失败的权重是成功的 3 倍（`fail_count * 3`），因为一次失败暴露的问题比一次成功提供的信息更多。

---

## 九、与参考系统的对比

| 特性 | Claude Code | browser-use | 本方案 |
|------|------------|-------------|--------|
| 存储格式 | Markdown 文件 | Python 对象 + JSON | Markdown 文件 + JSON |
| 记忆类型 | 声明性规则 + 自动笔记 | 步级摘要 + 历史重放 | 程序性 + 语义 + 情景 + 元记忆 |
| 索引方式 | MEMORY.md 指针 | 无（全量加载） | MEMORY.md 指针 |
| 整合机制 | Auto Dream | 无 | Dream 整合 |
| 分层加载 | 全局 → 项目 → 模块 | 无分层 | 索引 → 按需加载 |
| 可编辑性 | 用户可直接编辑 | 代码级修改 | 用户可直接编辑 |
| 跨任务复用 | 项目内复用 | 不支持 | Fragment 跨 flow 复用 |
| 置信度 | 无 | 无 | 有，含衰减模型 |
| 版本管理 | Git | 无 | Git + changelog |

---

## 十、实施路线

| 阶段 | 内容 | 交付物 |
|------|------|--------|
| **P0** | 目录结构 + MEMORY.md + confidence.json 骨架 | 空白记忆框架 |
| **P1** | default.md → flow.md + fragments 转换器 | 初始种子记忆 |
| **P2** | MemoryWriter：从 BrowserAgent 历史提取 episodic 记忆 | Session 文件自动生成 |
| **P3** | MemoryStore：读取 + 匹配 + 按需加载 | 执行时记忆检索 |
| **P4** | 置信度更新 + 三种执行模式切换 | 自适应执行 |
| **P5** | Dream 整合 + 过期淘汰 | 记忆自维护 |
| **P6** | 探索模式：无种子纯探索 + 自动生成 flow | 完整闭环 |
