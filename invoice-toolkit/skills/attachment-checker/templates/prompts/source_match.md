---
name: source_match
description: 当类别目录内找不到附件时，从报销人的来源目录候选文件中挑一个最合适的。
input_variables:
  - category
  - required_attachment
  - rule_text
  - attachment_keywords
  - invoice_name
  - person
  - amount
  - seller
  - commodity
  - candidates_block
---

## System

你是财务附件匹配专家。从来源目录的候选文件中为一张发票找到正确的附件，只返回 JSON，不要解释。

## Human

# 类别：{category}
# 需要的附件类型：{required_attachment}
# 规则要求：{rule_text}

# 发票信息
- 文件名：{invoice_name}
- 报销人：{person}
- 金额：{amount}
- 销售方：{seller}
- 商品：{commodity}

# 来源目录中该报销人的候选文件
{candidates_block}

---
# 任务
从候选文件中挑出最可能作为该发票附件的一个文件。如果候选中没有合适的，返回 null。

# 返回 JSON
{{
  "matched_filename": "候选文件名.pdf 或 null",
  "reason": "简短说明为什么选这个或为什么没有合适的"
}}

# 提示
- 附件特征：{attachment_keywords}
- 候选文件名必须是列表中真实存在的
- 若所有候选都不像附件（都是发票/无关文件），返回 null
- 金额 > 1000 时若需要转账截图，优先选银行回单/付款截图/转账截图类文件
