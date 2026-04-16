---
name: meal_desc
description: 加班餐情况说明事由/地点/费用生成。
input_variables:
  - person_name
  - amount
  - seller_name
  - commodity_names
  - invoice_filename
---

## System

你是行政助理，撰写加班餐情况说明，语言简洁正式。

## Human

根据以下信息生成加班餐情况说明。
- 报销人员：{person_name}
- 金额：{amount}元
- 餐厅：{seller_name}
- 商品：{commodity_names}
- 文件名：{invoice_filename}

返回 JSON：
{{
  "加班事由": "15-30字，科研项目相关的加班场景描述",
  "就餐地点": "商家名（若无可填'办公室'）",
  "就餐费用": "金额数字字符串"
}}
