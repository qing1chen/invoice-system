---
name: attachment-checker
description: >
  发票附件完整性检查 Skill。当用户提到「检查附件」「附件是否齐全」「缺少什么附件」
  「附件规则」「跑一下附件检查」或需要自定义/修改检查规则时触发此 Skill。
  本 Skill 是一个**多步骤 LLM 编排流程**：你（LLM）是流程指挥官，通过自然语言
  理解规则、做出判断、通过 bash 调用 Python 工具脚本完成文件IO/数据库/OCR等操作。
  也适用于：检查单个类别的附件、修改检查规则、查看检查结果、自定义新类别。
---

# 附件完整性检查 Skill

## 你的角色

你是**流程编排者**。整个附件检查工作流由你驱动——你阅读规则、收集数据、
做出判断、调用工具。Python 脚本只是你的"手"，负责你做不了的事（文件IO、
数据库读写、OCR API调用、docx生成）。

**关键原则**：
- 每一步决策都由你（LLM）基于规则和数据做出，不是代码硬编码的
- 工具脚本是无状态的纯函数，你负责传参和处理返回值
- 你在对话上下文中维护整个流程的状态

---

## 工具调用方式

所有工具通过 bash 调用 `scripts/tools.py`。

**SKILL_DIR** = 本文件所在目录（你读取本文件时已知路径，直接使用即可）。

```bash
cd <SKILL_DIR> && python -m scripts.tools <tool_name> '<args_json>'
```

示例：
```bash
cd /path/to/attachment-checker && python -m scripts.tools get_config '{}'
cd /path/to/attachment-checker && python -m scripts.tools collect_files '{"category":"打车"}'
```

返回值为 JSON。失败时返回 `{"error": "..."}`。

> 首次执行前先读 `references/tools.md` 了解全部工具的参数和返回值。

---

## 流程骨架

```
步骤 0: 读 references/rules.md + references/tools.md，调 get_config 和 get_ocr_names
     ↓
步骤 1: 对每个类别 ───────────────────┐
  1.1 collect_files                    │
  1.2 用 ocr_names 区分发票/附件       │
  1.3 collect_source_candidates        │
  1.4 extract_attachment_text          │
  1.5 逐张发票判定（你的核心决策）     │
  1.6 向用户汇报本类别结果             │
  （下一个类别）◄──────────────────────┘
     ↓
步骤 2: 加班餐有生成文件时 → merge_meal_docs
     ↓
步骤 3: save_attachment_report 写入数据库
     ↓
步骤 4: 向用户汇报最终摘要表
```

---

## 步骤 0: 加载规则和配置

1. 读 `references/rules.md` —— 理解所有类别的检查规则
2. 读 `references/tools.md` —— 了解可用工具及参数
3. 调用：
```bash
python -m scripts.tools get_config '{}'
python -m scripts.tools get_ocr_names '{}'
```
在上下文中保存 `config`（含 name_list、categories）和 `ocr_names`。

**检查点**：告诉用户"已加载 X 个类别的检查规则，开始检查"

---

## 步骤 1: 按类别循环检查

对 config.categories 中的每个类别执行以下子步骤：

### 1.1 收集文件
```bash
python -m scripts.tools collect_files '{"category":"打车"}'
```

### 1.2 区分发票与附件
用步骤 0 获取的 ocr_names：在名单中 → 发票，不在 → 附件候选。无发票则跳过。

### 1.3 来源目录收集
对每个发票涉及的人名：
```bash
python -m scripts.tools collect_source_candidates '{"person":"张三"}'
```
合并到附件候选列表（去重）。

### 1.4 OCR 提取附件文字
对每个附件候选：
```bash
python -m scripts.tools extract_attachment_text '{"filepath":"..."}'
```
同时查发票详情：
```bash
python -m scripts.tools lookup_invoice_details '{"filename":"张三+50+滴滴发票.pdf"}'
```

### 1.5 逐张发票判定

**这是你的核心决策。** 对每张发票，综合规则、发票详情、候选附件文件名和 OCR 文字，判定：

| 状态 | 含义 |
|------|------|
| 附件齐全 | 找到匹配附件 |
| 缺少附件 | 找不到 → 兜底搜索来源目录 |
| 附件校验不通过 | 找到但内容不符 |
| 需要生成（仅加班餐） | 无情况说明 → 调 generate_meal_doc |
| 需要修复（仅加班餐） | 情况说明校验不通过 → 调 fix_meal_doc |

**判定顺序**：
1. 发票是否属于本类别（用 rules.md 中的发票特征关键词）
2. 在候选附件中按人名 → OCR内容 → 文件名语义匹配
3. 匹配到的附件做内容校验（加班餐：人数×30≥金额、人名在名单中等）
4. 金额>1000 → 还需转账截图（通用金额规则）
5. 材料类单价>1000 或金额>20000 → 还需验收单

**缺少附件时兜底**：再次 collect_source_candidates → 逐个 OCR → 找到则 copy_file。

**记录格式**（在上下文中维护列表）：
```json
{"旧文件名":"...", "附件状态":"...", "缺少类型":"...", "匹配附件":"附件文件名",
 "附件路径":"附件的完整绝对路径(full_path)", "生成文件":"生成文件的完整绝对路径",
 "校验详情":"...", "附件类别":"打车"}
```

> **⚠ 关键：`附件路径` 和 `生成文件` 必须填写完整的绝对路径！**
> - 匹配到附件时：`附件路径` = 附件的 `full_path`（来自 collect_files 或 collect_source_candidates 返回的 `full_path` 字段）
> - 自动生成时：`生成文件` = generate_meal_doc 返回的 `generated_path`，`附件路径` 也填此路径
> - copy_file 复制来源附件后：`附件路径` = copy_file 返回的 `dst_path`
> - 这些路径是后续浏览器自动化上传附件的唯一依据，留空会导致无法上传

### 1.6 检查点汇报
```
✓ 打车：共 8 张发票，6 齐全，1 缺少行程单，1 缺少转账截图
```

---

## 步骤 2: 加班餐合并

如有生成/修复的文件：
```bash
python -m scripts.tools merge_meal_docs '{"generated_files":["...","..."]}'
```
用返回的 `merge_map` 更新 all_results 中的路径：
- 遍历 all_results，凡 `生成文件` 或 `附件路径` 在 merge_map 的 key 中，
  将其替换为 merge_map 对应的 value（合并后路径）

> 合并会将同一人的多份说明合入一个 docx（最多 6 份一组），并删除原始散件。

---

## 步骤 3: 写入数据库

```bash
python -m scripts.tools save_attachment_report '{"results":[...]}'
```

---

## 步骤 4: 汇报最终结果

```
附件检查完成！

| 类别   | 发票数 | 齐全 | 缺少 | 已生成 | 不通过 |
|--------|--------|------|------|--------|--------|
| 打车   | 8      | 6    | 1    | 0      | 1      |
| 出差   | 5      | 5    | 0    | 0      | 0      |
| 加班餐 | 12     | 8    | 0    | 4      | 0      |

异常项：
- [打车] 张三+50+滴滴发票.pdf → 缺少行程单
```

---

## 参考文件

| 文件 | 何时读 |
|------|--------|
| `references/rules.md` | 步骤 0，理解检查规则 |
| `references/tools.md` | 步骤 0，了解工具参数 |
| `references/process-management.md` | 仅在需要理解架构时 |

---

## 自定义

| 想改什么 | 怎么做 |
|----------|--------|
| 金额阈值 1000→2000 | 编辑 `references/rules.md` 通用规则段 |
| 加班餐每人标准 30→50 | 编辑 `references/rules.md` 加班餐节 |
| 新增类别 | 在 `references/rules.md` 新增二级标题 |
| 增加工具函数 | 编辑 `scripts/tools.py` + `references/tools.md` |
