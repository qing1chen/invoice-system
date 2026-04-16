# 工具函数参考文档

> 本文件描述了 LLM 编排流程中可调用的所有工具函数。
> 工具通过 bash 调用 Python 脚本，LLM 负责传参和处理返回值。

---

## 调用方式

所有工具统一通过 bash 命令调用：

```bash
cd <skill_dir> && python -m scripts.tools <tool_name> '<args_json>'
```

返回值为 JSON 字符串（stdout），直接解析即可。

**错误处理**：失败时返回 `{"success": false, "error": "错误描述"}` 或 `{"error": "错误描述"}`。
非零退出码也表示失败。

---

## 概览

| 工具名 | 用途 | 何时调用 |
|--------|------|----------|
| `get_config` | 获取系统配置 | 步骤 0 |
| `get_ocr_names` | 获取 OCR 识别名单 | 步骤 1.2 |
| `collect_files` | 收集类别目录文件 | 步骤 1.1 |
| `collect_source_candidates` | 收集来源目录候选附件 | 步骤 1.3 / 1.5.1 |
| `lookup_invoice_details` | 查询发票 OCR 详情 | 步骤 1.4 |
| `extract_attachment_text` | OCR 提取附件文字 | 步骤 1.4 |
| `copy_file` | 复制文件到类别目录 | 步骤 1.5.1 |
| `generate_meal_doc` | 生成加班餐情况说明 | 步骤 1.5.2 |
| `fix_meal_doc` | 修复加班餐情况说明 | 步骤 1.5.3 |
| `merge_meal_docs` | 合并加班餐说明文件 | 步骤 2 |
| `save_attachment_report` | 写入数据库 | 步骤 3 |
| `backup_file` | 备份原始文件 | 需要时 |

---

## 详细说明

### get_config

获取系统配置信息。

```bash
python -m scripts.tools get_config '{}'
```

```
返回: {
  "name_list": ["张三", "李四", ...],
  "source_root": "/path/to/课题组成员文件",
  "invoice_root": "/path/to/发票分类",
  "categories": ["打车", "出差", "加班餐", "打印", "快递", "材料"],
  "overtime_meal_output_dir": "/path/to/加班餐输出",
  "cache_dir": "/path/to/cache"
}
```

---

### get_ocr_names

获取已被百度 OCR 成功识别为发票的文件名集合。

```bash
python -m scripts.tools get_ocr_names '{}'
```

```
返回: {
  "ocr_names": ["张三+50+滴滴发票.pdf", "李四+80+出租车.pdf", ...]
}
```

**用途**：区分发票与附件。文件名在此集合中 = 发票，不在 = 附件。

---

### collect_files

收集指定类别目录下的所有文件。

```bash
python -m scripts.tools collect_files '{"category":"打车"}'
```

```
返回: [
  {
    "name": "张三+50+滴滴发票.pdf",
    "full_path": "/absolute/path/to/file",
    "parent": "张三",
    "person": "张三"
  },
  ...
]
```

**注意**：返回的是该类别目录下的所有文件（包括发票和附件），由 LLM 根据
OCR 名单进一步区分。

---

### collect_source_candidates

从来源目录（`source_root/<人名>/`）收集候选附件文件。

```bash
python -m scripts.tools collect_source_candidates '{"person":"张三"}'
```

```
返回: [
  {
    "name": "张三行程单.jpg",
    "full_path": "/path/to/课题组成员文件/张三/张三行程单.jpg",
    "parent": "张三",
    "person": "张三"
  },
  ...
]
```

**过滤条件**（工具内部处理）：
- 排除已在 OCR 识别名单中的文件（即排除发票）
- 排除本次运行中已被匹配使用过的文件

---

### lookup_invoice_details

从发票数据库中查询发票的 OCR 识别详情。

```bash
python -m scripts.tools lookup_invoice_details '{"filename":"张三+50+滴滴发票.pdf"}'
```

```
返回: {
  "价税合计": "50.00",
  "销售方名称": "滴滴出行科技有限公司",
  "商品名称": "客运服务费",
  "商品单价": ["50.00"],
  "购方名称": "山东大学",
  "发票类型": "增值税电子普通发票",
  "开票日期": "2024年03月12日",
  "匹配简介": "张三+50+打车",
  "姓名/公司": "张三"
}
```

**缺失字段**：未识别到的字段值为空字符串。

---

### extract_attachment_text

对任意类型的附件文件进行截图+OCR文字提取。

```bash
python -m scripts.tools extract_attachment_text '{"filepath":"/path/to/行程单-张三.pdf"}'
```

```
返回: {
  "text": "滴滴出行 行程明细\n乘车人：张三\n起点：济南西站\n终点：山东大学\n...",
  "truncated": false,
  "method": "pdf_ocr"
}
```

**支持的文件类型**：
- 图片 (.jpg/.png/.bmp/…) → 直接 OCR
- PDF → PyMuPDF 渲染为图片后 OCR（最多 3 页）
- doc/docx → 优先 python-docx 直接读文本，失败则转 PDF 再 OCR

**返回 null**：文件不存在、格式不支持、或 OCR 失败时返回 null。

---

### copy_file

将来源目录中的附件复制到类别目录。

```bash
python -m scripts.tools copy_file '{"src":"/path/to/源文件","dst_dir":"/path/to/类别目录","mark_used":true}'
```

```
返回: {
  "success": true,
  "dst_path": "/path/to/类别目录/文件名.pdf",
  "dst_name": "文件名.pdf"
}
```

---

### generate_meal_doc

自动生成加班餐情况说明（山东大学科研业务专项经费使用说明表）。

```bash
python -m scripts.tools generate_meal_doc '{"person":"张三","amount":29.5,"seller":"美团外卖","commodity":"餐饮","invoice_filename":"张三+29.5+美团.pdf"}'
```

```
返回: {
  "generated_path": "/path/to/张三+29.5+加班餐.docx",
  "persons_text": "1人，张三",
  "success": true
}
```

**可选参数**：
- `name_list`：课题组名单，不传则从配置中获取
- `reason`：加班事由，不传则自动生成默认文本

**内部逻辑**：
- 人数 = ceil(amount / 30)
- 报销人排首位
- 从 name_list 随机补齐人数

---

### fix_meal_doc

修复已存在但校验不通过的加班餐情况说明。

```bash
python -m scripts.tools fix_meal_doc '{"original_path":"/path/to/原始说明.docx","invoice_filename":"张三+29.5+美团.pdf","person":"张三","amount":29.5,"target_persons":["张三","李四"],"required_count":1}'
```

```
返回: {
  "fixed_path": "/path/to/修复后.docx",
  "fix_msg": "补充人数1→2，修正金额",
  "success": true
}
```

**可选参数**：`reason_text`（事由文本）

**注意**：修复前自动备份原文件到 `cache/attachment_backup/`。

---

### merge_meal_docs

将多个加班餐情况说明合并为一个文件（最多 6 个一组）。

```bash
python -m scripts.tools merge_meal_docs '{"generated_files":["/path/to/file1.docx","/path/to/file2.docx"]}'
```

```
返回: {
  "merged_paths": ["/path/to/合并后.docx"],
  "merge_map": {
    "/path/to/file1.docx": "/path/to/合并后.docx",
    "/path/to/file2.docx": "/path/to/合并后.docx"
  },
  "success": true
}
```

---

### save_attachment_report

将附件检查结果写入记录数据库。

```bash
python -m scripts.tools save_attachment_report '{"results":[{"旧文件名":"张三+50+滴滴发票.pdf","附件状态":"附件齐全","缺少类型":"","匹配附件":"张三行程单.jpg","附件路径":"/path/to/...","生成文件":"","校验详情":"已匹配行程单","附件类别":"打车"}]}'
```

```
返回: {
  "success": true,
  "records_written": 15,
  "anomalies_written": 3
}
```

**results 数组每项的字段**：

| 字段 | 说明 | 示例 |
|------|------|------|
| `旧文件名` | 发票文件名 | `"张三+50+滴滴发票.pdf"` |
| `附件状态` | 判定结果 | `"附件齐全"` / `"缺少附件"` / `"已自动生成"` / `"附件已修复"` / `"附件校验不通过"` |
| `缺少类型` | 缺少的附件类型 | `"行程单"` / `"转账截图"` / `""` |
| `匹配附件` | 匹配到的附件文件名 | `"张三行程单.jpg"` |
| `附件路径` | 附件完整路径 | `"/path/to/..."` |
| `生成文件` | 自动生成的文件路径 | `"/path/to/..."` 或 `""` |
| `校验详情` | 人可读的校验信息 | `"已匹配行程单"` |
| `附件类别` | 所属检查类别 | `"打车"` |

**内部处理**：
- 通过 `records.匹配发票` 反查对应报销记录
- 多张发票对应同一记录时自动聚合（取最严重状态）
- 异常项自动追加到 `records.校验详情`
- 非 PDF/图片格式的附件自动转为 PDF

---

### backup_file

将文件备份到缓存目录。

```bash
python -m scripts.tools backup_file '{"filepath":"/path/to/要备份的文件","delete_original":true}'
```

```
返回: {
  "backup_path": "/path/to/cache/attachment_backup/文件名.docx",
  "success": true
}
```

---

## 错误处理

所有工具在执行失败时会返回：
```json
{
  "success": false,
  "error": "错误描述信息"
}
```

**LLM 的错误处理策略**：
1. 工具返回失败时，记录错误并继续处理下一项（不要整体中断）
2. OCR 提取失败时，退化为仅根据文件名判断
3. 生成/修复 docx 失败时，将状态标记为"缺少附件"
4. 数据库写入失败时，向用户报告并建议重试
