# 记忆系统使用指南

## 快速开始

### 1. 初始化记忆目录

```bash
echo '{"action": "init", "base_dir": "./memory"}' | python scripts/memory_store.py
```

```python
from scripts.memory_store import MemoryStore
store = MemoryStore("./memory")
store.init()
```

### 2. 从现有模板生成种子记忆

```bash
echo '{"template_path": "./templates/default.md", "base_dir": "./memory", "category": "日常报销"}' \
  | python scripts/seed_converter.py
```

### 3. 执行后写入记忆

```bash
echo '{
  "base_dir": "./memory",
  "session": {
    "session_id": "s001",
    "category": "日常报销",
    "record_ids": [12, 15],
    "flow_used": "daily-reimbursement",
    "mode": "execution"
  },
  "steps": [
    {"step": 1, "action": "click", "target": "智能报销按钮", "result": "success", "duration_sec": 1.2},
    ...
  ]
}' | python scripts/memory_writer.py
```

```python
from scripts.memory_writer import MemoryWriter
writer = MemoryWriter("./memory")
writer.process_session(session_meta, steps)
```

### 4. 查询记忆

```bash
# 查找最佳流程
echo '{"action": "query_flow", "base_dir": "./memory", "data": {"category": "日常报销"}}' \
  | python scripts/memory_store.py

# 获取探索上下文（UI 地图 + 规则 + 错误 + 近期 session）
echo '{"action": "get_exploration_context", "base_dir": "./memory", "data": {"category": "日常报销"}}' \
  | python scripts/memory_store.py

# 查询错误模式
echo '{"action": "query_errors", "base_dir": "./memory", "data": {}}' \
  | python scripts/memory_store.py
```

### 5. 更新置信度

```bash
echo '{"action": "update_confidence", "base_dir": "./memory", "data": {"memory_type": "flows", "memory_id": "daily-reimbursement", "success": true}}' \
  | python scripts/memory_store.py
```

## 生成的目录结构

```
memory/
├── MEMORY.md                        ← 索引（自动生成，LLM 首先读这个）
├── procedural/
│   ├── flows/
│   │   └── 日常报销.flow.md          ← 完整操作流程
│   └── fragments/
│       ├── 处理单张发票.frag.md      ← 可复用片段
│       ├── 上传发票.frag.md
│       └── ...
├── semantic/
│   ├── ui-map.md                    ← 页面元素知识
│   ├── rules.md                     ← 业务规则
│   └── field-patterns.md            ← 表单字段规律
├── episodic/
│   ├── sessions/
│   │   └── 2025-01-15_日常报销_s001.ep.md  ← 执行日志
│   └── errors/
│       └── modal-blocking.md        ← 错误模式汇总
└── meta/
    ├── confidence.json              ← 置信度数据
    └── changelog.md                 ← 变更日志
```

## 与 BrowserAgent 集成

```python
from scripts.memory_store import MemoryStore, execution_mode
from scripts.memory_writer import MemoryWriter

class MemoryAwareBrowserAgent(BrowserAgent):
    def __init__(self, memory_dir, **kwargs):
        super().__init__(**kwargs)
        self.memory = MemoryStore(memory_dir)
        self.writer = MemoryWriter(memory_dir)

    async def run(self, task, url):
        category = self._detect_category(task)
        flow = self.memory.query_flow(category)

        if flow["found"] and flow["mode"] == "flash":
            # 置信度 > 0.85 → 直接按 flow 执行
            result = await self._execute_flow(flow, url)
        elif flow["found"] and flow["mode"] == "verify":
            # 0.5 ~ 0.85 → 每步验证
            result = await self._execute_with_verify(flow, url)
        else:
            # 无 flow 或置信度低 → 探索模式
            ctx = self.memory.get_exploration_context(category)
            result = await self._explore(task, url, ctx)

        # 执行后自动写入记忆
        self.writer.process_session(
            session_meta={...},
            steps=result.history,
        )
        return result
```

## CLI 接口一览

所有命令通过 `stdin JSON → stdout JSON`，`action` 字段分发：

| action | 用途 | data 参数 |
|--------|------|----------|
| `init` | 初始化目录 | — |
| `write_flow` | 写入流程 | flow_id, category, steps |
| `write_fragment` | 写入片段 | fragment_id, title, steps |
| `write_episode` | 写入 session | session_id, category, outcome, ... |
| `update_semantic` | 更新语义记忆 | file_name, entries |
| `update_confidence` | 更新置信度 | memory_type, memory_id, success |
| `query_flow` | 查询流程 | category |
| `query_fragment` | 查询片段 | fragment_id |
| `query_errors` | 查询错误 | error_type (可选) |
| `get_exploration_context` | 获取探索上下文 | category |
| `list_flows` | 列出所有流程 | — |
| `list_fragments` | 列出所有片段 | — |
| `rebuild_index` | 重建 MEMORY.md | — |

## 置信度与执行模式

```
confidence = success / (success + fail × 3) × 0.995^天数

≥ 0.85  → flash   ⚡ 直接执行
≥ 0.50  → verify  🔍 带验证执行
≥ 0.30  → explore 🧭 需要探索增强
< 0.30  → archive 🗄️ 不可靠，仅参考
```
