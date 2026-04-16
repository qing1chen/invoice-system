# 报销 Agent 记忆系统 v2

> 让浏览器 Agent 像人一样"记住"操作步骤——做过的事不用重新摸索，踩过的坑不会再踩。

---

## 这个系统解决什么问题？

想象你是一个新员工，第一天需要在公司报销系统里提交发票。你一步步摸索：点哪个按钮、填哪个表、上传什么文件。做完一次后，你**记住了**这个流程。下次再做同样的事，你不用再摸索了，直接按记忆操作就行。

这个记忆系统就是给浏览器 Agent（自动操作网页的 AI）提供同样的能力：

```
第 1 次执行：我不知道怎么报销 → 边探索边学 → 把成功的步骤记下来
第 2 次执行：我记得上次怎么做的 → 直接按记忆执行 → 快 10 倍
第 N 次执行：我已经非常熟练了 → 闭眼操作 → 遇到异常也知道怎么处理
```

**没有记忆系统时**：Agent 每次都像第一天上班的新员工，从零开始。
**有了记忆系统后**：Agent 像一个干了三年的老员工，熟门熟路。

---

## 核心概念：四层记忆

这个系统模仿人脑的记忆分类方式，把 Agent 的"记忆"分成四层。用一个比喻来理解：

```
想象你在学开车 🚗

📋 程序性记忆 = "怎么做"
   → 你记住了：启动→挂挡→松手刹→踩油门→走
   → 对 Agent 来说：点击「智能报销」→ 选类型 → 传发票 → 填信息 → 提交

🧠 语义记忆 = "知道什么"
   → 你知道：红灯要停、方向盘往左车往左
   → 对 Agent 来说：手机号固定填 12345678912、报销校区选千佛山

📖 情景记忆 = "经历过什么"
   → 你记得：上次在那个路口差点闯红灯
   → 对 Agent 来说：上次上传发票时弹出了"发票已存在"的弹窗

🔢 元记忆 = "哪些记忆靠谱"
   → 你知道：去公司的路我很熟（高置信度），去机场的路不太确定（低置信度）
   → 对 Agent 来说：日常报销流程我执行成功过 14 次（置信度 0.92），试剂报销只做过 1 次（置信度 0.50）
```

---

## 文件结构一览

初始化后，系统会生成这样的文件夹结构：

```
memory/
│
├── MEMORY.md                        ← 📖 目录索引（Agent 最先读这个文件）
│
├── procedural/                      ← 📋 程序性记忆：操作步骤
│   ├── flows/                       ← 完整流程（从头做到尾的全部步骤）
│   │   ├── 日常报销.flow.md         ←   "怎么报销日常费用"
│   │   └── 国内差旅.flow.md         ←   "怎么报销出差费用"
│   └── fragments/                   ← 可复用片段（像"函数"一样被多个流程调用）
│       ├── 上传发票.frag.md         ←   "怎么上传一张发票"（日常报销和差旅都要用）
│       └── 填写联系人.frag.md       ←   "怎么填联系人信息"
│
├── semantic/                        ← 🧠 语义记忆：知识和规则
│   ├── ui-map.md                    ←   "页面上有哪些按钮/输入框"
│   ├── rules.md                     ←   "业务规则（手机号填什么、校区选哪个）"
│   └── field-patterns.md            ←   "表单字段的格式规律"
│
├── episodic/                        ← 📖 情景记忆：执行日志
│   ├── sessions/                    ← 每次执行的详细记录
│   │   └── 2025-01-15_日常报销_s001.ep.md
│   └── errors/                      ← 按错误类型归纳的经验
│       └── modal-blocking.md        ←   "弹窗遮挡问题怎么处理"
│
└── meta/                            ← 🔢 元记忆：关于记忆的记忆
    ├── confidence.json              ←   "每条记忆的可信度打分"
    └── changelog.md                 ←   "记忆修改日志（谁改了什么，依据是什么）"
```

**为什么用 Markdown 而不用数据库？**
因为 LLM 天然能读懂 Markdown。直接把 `.flow.md` 文件内容塞进 prompt 里，Agent 就能理解该怎么操作。而且你也可以直接打开文件查看和手动修改，不需要任何特殊工具。

---

## 快速开始

### 前提条件

- Python 3.8+
- 不需要安装任何第三方库（纯标准库实现）

### 第 1 步：初始化记忆目录

```python
from scripts.memory_store import MemoryStore

store = MemoryStore("./memory")
store.init()
# → 创建上面那个目录结构，生成骨架文件
```

或者用命令行：
```bash
echo '{"action": "init", "base_dir": "./memory"}' | python scripts/memory_store.py
```

### 第 2 步：从现有模板生成"种子记忆"

如果你已经有一份手写的 `default.md` 操作模板，可以一键转换为记忆系统的初始数据：

```python
from scripts.seed_converter import convert_template_to_memory

with open("./templates/default.md", "r") as f:
    template_text = f.read()

result = convert_template_to_memory(
    template_text=template_text,
    base_dir="./memory",
    category="日常报销",
)
# → 自动拆分为 1 个 flow + 若干 fragment + 初始业务规则
```

### 第 3 步：Agent 执行后写入记忆

每次 Agent 执行完任务，调用 `MemoryWriter` 自动提取并保存记忆：

```python
from scripts.memory_writer import MemoryWriter

writer = MemoryWriter("./memory")
result = writer.process_session(
    session_meta={
        "session_id": "s001",
        "category": "日常报销",
        "record_ids": [12, 15],
        "flow_used": "daily-reimbursement",   # 用了哪个流程（如果有的话）
        "mode": "execution",                   # execution | exploration
    },
    steps=[
        # Agent 执行的每一步操作
        {
            "step": 1,
            "action": "click",                 # 动作类型
            "target": "「智能报销」按钮",       # 操作目标
            "result": "success",               # 结果
            "duration_sec": 1.2,               # 耗时
            "page_url": "http://reimburse.example.com/home",
        },
        {
            "step": 2,
            "action": "upload_file",
            "target": "文件上传控件",
            "value": "/path/to/invoice.pdf",
            "result": "success",
            "duration_sec": 5.0,
            "page_url": "http://reimburse.example.com/invoice",
        },
        # ... 更多步骤
    ],
)
```

`process_session` 会自动完成以下 **9 件事**：
1. 生成本次执行的情景记忆（session 日志）
2. 从成功步骤中提取/更新 flow（完整流程）
3. 识别可复用的操作片段（fragment）
4. 提取新发现的 UI 元素 → 更新 ui-map.md
5. 提取错误模式 → 更新错误汇总
6. 更新置信度分数
7. 检测全局异常（连续失败 → 自动降低所有记忆的可信度）
8. 压缩过期的 session 文件（7天压缩，30天归档）
9. 重建 MEMORY.md 索引

### 第 4 步：查询记忆

```python
store = MemoryStore("./memory")

# 方式 A：精确查询（按类别名称）
flow = store.query_flow("日常报销")
if flow["found"]:
    print(f"找到流程，置信度 {flow['confidence']:.2f}，建议模式：{flow['mode']}")

# 方式 B：模糊搜索（v2 新增，输入任意关键词）
results = store.search_memory("出差费用")     # 能找到"国内差旅"
results = store.search_memory("发票上传")     # 能找到"upload-invoice" fragment
results = store.search_memory("弹窗")         # 能找到错误处理记忆

# 方式 C：获取完整工作上下文（直接塞进 LLM prompt）
context = store.get_working_context("日常报销")
print(context["context"])     # 已按预算裁剪好的 Markdown 文本
print(context["mode"])        # flash / verify / explore
print(context["total_lines"]) # 总行数（不会超出预算）
```

---

## 置信度与执行模式

Agent 怎么决定"这次该按记忆执行还是从头探索"？靠**置信度**——一个 0 到 1 之间的分数。

### 置信度怎么算

```
                    成功次数
基础置信度 = ─────────────────────────
             成功次数 + 失败次数 × 3
                                         失败的权重是成功的 3 倍
                                         ↑ 因为一次失败暴露的问题 > 一次成功提供的信息
```

```
最终置信度 = 基础置信度 × 0.995 ^ (天数 / 稳定性因子)
                                      ↑
                                      自适应衰减：
                                      验证 100 次的记忆，30 天后仍保留 98%
                                      只验证 3 次的记忆，30 天后降到 95%
```

### 有效置信度（v2 新增）

一个完整流程（flow）会引用多个片段（fragment）。流程的实际可靠性取决于**最弱的那个环节**：

```
日常报销 flow （自身置信度 0.92）
├── 引用 上传发票 fragment  （置信度 0.95）
├── 引用 填写联系人 fragment（置信度 0.98）
└── 引用 选择经费项目 fragment（置信度 0.72）  ← 最弱环节！

有效置信度 = min(0.92, 0.95, 0.98, 0.72) = 0.72
                                             ↑
                                             以最弱的为准
```

### 四种执行模式

```
有效置信度 ≥ 0.85  →  ⚡ flash    直接执行，不思考
有效置信度 ≥ 0.50  →  🔍 verify   按流程执行，但每步都检查一下
有效置信度 ≥ 0.30  →  🧭 explore  需要边探索边执行
有效置信度 < 0.30  →  🗄️ archive  这条记忆不靠谱了，仅供参考
```

---

## 工作记忆管理（v2 新增）

### 问题：不能把所有记忆都塞给 LLM

LLM 的上下文窗口有限。如果把所有记忆文件全部加载，会浪费 token 甚至超出限制。所以需要一个"预算分配器"——根据执行模式，只加载必要的记忆。

### 三种模式的预算分配

```
⚡ flash 模式（总预算 400 行）
   ├── flow：200 行     ← 只需要知道"怎么做"
   ├── fragment：150 行
   ├── ui-map：0 行     ← 不需要，反正每步都是确定的
   ├── rules：0 行
   ├── errors：0 行
   └── sessions：0 行

🔍 verify 模式（总预算 800 行）
   ├── flow：200 行
   ├── fragment：200 行
   ├── ui-map：150 行   ← 需要验证页面元素是否还在
   ├── rules：100 行
   ├── errors：100 行   ← 需要知道常见坑在哪
   └── sessions：0 行

🧭 explore 模式（总预算 1500 行）
   ├── flow：150 行     ← 旧 flow 仅供参考
   ├── fragment：200 行
   ├── ui-map：400 行   ← 需要完整的页面地图
   ├── rules：200 行    ← 需要所有业务规则
   ├── errors：200 行   ← 需要所有避坑指南
   └── sessions：250 行 ← 需要看看最近几次是怎么做的
```

### 使用方式

```python
# 自动检测模式并按预算组装上下文
ctx = store.get_working_context("日常报销")

# 直接把 ctx["context"] 注入到 LLM prompt 中
prompt = f"""
你是一个报销 Agent。以下是你的记忆：

{ctx["context"]}

请根据以上记忆执行报销任务。
"""
```

---

## 语义搜索（v2 新增）

### 问题：精确匹配太死板

旧版系统只能用 `category="日常报销"` 精确匹配。如果用户说的是"帮我报材料费"或"出差费用报销"，就找不到对应的记忆了。

### 解决：TF-IDF 模糊搜索

新版内置了一个轻量级搜索引擎（零依赖，不需要安装任何库），支持中英文模糊匹配：

```python
# 这些都能找到正确的记忆：
store.search_memory("出差")        # → 找到 "国内差旅" flow
store.search_memory("发票上传")    # → 找到 "upload-invoice" fragment
store.search_memory("弹窗")        # → 找到 "modal-blocking" 错误记忆
store.search_memory("modal")       # → 同上（支持英文）

# 还可以按类型过滤：
store.search_memory("报销", filter_type="flow")       # 只搜 flow
store.search_memory("联系人", filter_type="fragment")  # 只搜 fragment
```

### 自动回退

`query_flow` 精确匹配失败时，会自动尝试语义搜索：

```python
# 旧版行为
store.query_flow("出差费用")  # → {"found": False}  ← 找不到！

# 新版行为
store.query_flow("出差费用")  # → {"found": True, "flow_id": "travel-reimbursement"}
#                                  精确匹配失败 → 自动搜索 → 找到"国内差旅"
```

---

## 演化安全机制（v2 新增）

### 问题：Agent 自我更新可能引入错误

Agent 每次执行后会自动更新记忆。但如果某次执行中 Agent 犯了错——比如错误地"发现"了一条不存在的业务规则——这个错误就会被当作"知识"写入记忆，后续所有执行都会受影响。

### 三道安全防线

#### 防线 1：冲突检测

写入语义记忆前，自动检查新内容是否与已有记忆矛盾：

```python
# 已有记忆：报销投递校区 = 千佛山校区
# Agent 试图写入：报销投递校区 = 趵突泉校区

result = store.update_semantic("rules.md", [
    {"section": "表单规则", "content": "| 报销投递校区 | 趵突泉校区 |"}
])

print(result["has_conflicts"])  # True!
print(result["conflicts"])
# → "对「报销投递校区」的描述冲突: 已有 [千佛山校区] vs 新写入 [趵突泉校区]"
```

冲突内容不会被丢弃，而是带着 `⚠️ 有争议` 标签写入，等待人工核实。

#### 防线 2：审计追溯

每次记忆更新都会在 changelog 中记录**是哪次执行导致的修改**：

```python
store.update_semantic("rules.md", entries, evidence_session_id="sess-001")

# changelog.md 中会记录：
# - 2025-01-15T10:30:00 更新语义记忆 rules.md (1条) [证据:session=sess-001]
```

如果发现记忆被错误修改，可以追溯到具体是哪次执行引入的问题。

#### 防线 3：全局异常检测

如果 Agent 连续 3 次执行都失败（可能是报销系统界面改版了），系统会自动：

```
检测到连续 3 次失败
    ↓
触发全局置信度衰减（所有记忆的置信度 × 0.7）
    ↓
所有 flow 从 flash 模式降级到 verify 或 explore 模式
    ↓
Agent 开始重新验证/探索，而不是继续用过时的记忆盲目执行
```

```python
# 手动触发全局衰减（通常不需要，系统会自动检测）
store.apply_global_decay(0.7, reason="报销系统升级了")
```

#### 附加安全：过期 Session 自动清理

```
session 文件生命周期：
  0~7 天   → 保留完整详情
  7~30 天  → 自动压缩（只保留摘要 + 关键事件）
  30 天+   → 自动归档到 archive/ 子目录
```

---

## 与 BrowserAgent 集成

完整的集成示例：

```python
from scripts.memory_store import MemoryStore
from scripts.memory_writer import MemoryWriter

class MemoryAwareBrowserAgent(BrowserAgent):
    def __init__(self, memory_dir, **kwargs):
        super().__init__(**kwargs)
        self.memory = MemoryStore(memory_dir)
        self.writer = MemoryWriter(memory_dir)

    async def run(self, task, url):
        category = self._detect_category(task)

        # ① 获取工作记忆上下文（自动检测模式 + 按预算裁剪）
        ctx = self.memory.get_working_context(category)
        mode = ctx["mode"]
        context_text = ctx["context"]

        if mode == "flash":
            # ⚡ 高置信度 → 直接按 flow 执行，不思考
            flow = self.memory.query_flow(category)
            result = await self._execute_flow(flow, url)

        elif mode == "verify":
            # 🔍 中置信度 → 按 flow 执行，但每步验证 DOM 状态
            flow = self.memory.query_flow(category)
            result = await self._execute_with_verify(flow, url, context_text)

        else:
            # 🧭 低置信度或无 flow → 探索模式
            result = await self._explore(task, url, context_text)

        # ② 执行后自动写入记忆（含安全检查 + 异常检测）
        self.writer.process_session(
            session_meta={
                "session_id": self._gen_session_id(),
                "category": category,
                "record_ids": result.record_ids,
                "flow_used": flow.get("flow_id") if mode != "explore" else None,
                "mode": mode,
            },
            steps=result.history,
        )

        return result
```

---

## CLI 命令速查表

所有命令格式：`echo '{"action": "...", "base_dir": "./memory", "data": {...}}' | python scripts/memory_store.py`

### 基础操作

| action | 用途 | data 参数 |
|--------|------|----------|
| `init` | 初始化目录 | — |
| `rebuild_index` | 重建 MEMORY.md | — |

### 写入记忆

| action | 用途 | data 参数 |
|--------|------|----------|
| `write_flow` | 写入完整流程 | flow_id, category, steps |
| `write_fragment` | 写入可复用片段 | fragment_id, title, steps |
| `write_episode` | 写入执行日志 | session_id, category, outcome, ... |
| `update_semantic` | 更新语义记忆 | file_name, entries, evidence_session_id |
| `update_confidence` | 更新置信度 | memory_type, memory_id, success |

### 查询记忆

| action | 用途 | data 参数 |
|--------|------|----------|
| `query_flow` | 查询最佳流程 | category |
| `query_fragment` | 查询片段 | fragment_id |
| `query_errors` | 查询错误模式 | error_type (可选) |
| `search` | 语义模糊搜索 | query, filter_type (可选) |
| `get_exploration_context` | 获取探索上下文 | category |
| `get_working_context` | 获取工作记忆上下文 | category, mode (可选) |
| `list_flows` | 列出所有流程 | — |
| `list_fragments` | 列出所有片段 | — |

### 系统管理

| action | 用途 | data 参数 |
|--------|------|----------|
| `apply_global_decay` | 全局置信度衰减 | decay_multiplier, reason |

### 示例

```bash
# 模糊搜索
echo '{"action":"search","base_dir":"./memory","data":{"query":"发票上传"}}' \
  | python scripts/memory_store.py

# 获取工作记忆（自动检测模式）
echo '{"action":"get_working_context","base_dir":"./memory","data":{"category":"日常报销"}}' \
  | python scripts/memory_store.py

# 全局衰减（报销系统升级后使用）
echo '{"action":"apply_global_decay","base_dir":"./memory","data":{"decay_multiplier":0.7,"reason":"系统升级"}}' \
  | python scripts/memory_store.py
```

---

## 系统演进路线

```
阶段 1 ✅  目录结构 + 骨架文件 + confidence.json
阶段 2 ✅  default.md → flow + fragment 种子转换器
阶段 3 ✅  MemoryWriter：执行后自动提取记忆
阶段 4 ✅  MemoryStore：读取 + 匹配 + 按需加载
阶段 5 ✅  置信度更新 + 三种执行模式切换
阶段 6 ✅  语义搜索 + 冲突检测 + 工作记忆预算 ← v2 新增
阶段 7 🔜  Dream 整合（记忆去重 + 合并 + 淘汰）
阶段 8 🔜  无种子纯探索 + 自动生成 flow（完整闭环）
```

---

## 模块一览

| 文件 | 职责 | 行数 |
|------|------|------|
| `memory_store.py` | 存储引擎（初始化/读/写/查询/搜索/置信度/预算） | ~2200 |
| `memory_writer.py` | 从执行历史提取记忆（含安全守卫） | ~890 |
| `seed_converter.py` | 模板 → 种子记忆转换 | ~370 |
| `calculate_amounts.py` | 报销金额计算（独立模块，不涉及记忆） | ~160 |
| `__init__.py` | 包入口 | ~40 |

所有模块**零外部依赖**，仅使用 Python 标准库。

---

## 常见问题

### Q：记忆文件可以手动修改吗？

可以。所有记忆都是 Markdown 文件，你可以直接用文本编辑器打开修改。修改后建议运行 `rebuild_index` 刷新索引。

### Q：Agent 写入了错误的记忆怎么办？

1. 查看 `meta/changelog.md`，找到错误写入的时间和关联的 session_id
2. 直接编辑对应的 `.md` 文件修正内容
3. 如果是业务规则错误，带 `⚠️ 有争议` 标签的内容优先检查

### Q：报销系统界面改版了怎么办？

运行全局衰减，让 Agent 重新进入探索模式：

```python
store.apply_global_decay(0.5, reason="报销系统界面改版")
```

### Q：记忆文件越来越多怎么办？

系统会自动清理：
- session 文件 7 天后压缩、30 天后归档
- 置信度低于 0.3 的记忆自动标记为 archive（不参与执行决策）
- 后续版本会加入 Dream 整合机制，自动去重合并

### Q：可以在不同项目间共享记忆吗？

可以。记忆目录是独立的文件夹，直接复制到另一个项目即可。建议先运行 `apply_global_decay(0.7, "迁移到新环境")`，让 Agent 在新环境中重新验证。
