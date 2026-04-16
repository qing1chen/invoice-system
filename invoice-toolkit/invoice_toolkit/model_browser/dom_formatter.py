"""DOM 信息格式化 — 将 JS 提取结果转为 LLM 可读文本"""

from __future__ import annotations


def format_dom_for_llm(
    dom_info: dict,
    max_elements: int = 80,
    prev_element_keys: set | None = None,
) -> tuple[str, set]:
    """
    将 DOM 信息格式化为 LLM 可读文本。

    特性：
    - 滚动状态（"上方 X 页, 下方 Y 页"）
    - 模态弹窗过滤（弹窗存在时仅显示弹窗内元素）
    - 视口内元素不受 max_elements 限制
    - 新增元素标 *新增* 标记（与上一步 diff）

    返回: (formatted_text, current_element_keys)
    """
    scroll = dom_info.get("scroll", {})
    lines = [
        f"页面: {dom_info.get('title', '')}",
        f"URL: {dom_info.get('url', '')}",
    ]

    if scroll:
        pages_above = scroll.get("pages_above", 0)
        pages_below = scroll.get("pages_below", 0)
        if pages_above > 0 or pages_below > 0:
            lines.append(f"|滚动| 上方 {pages_above} 页, 下方 {pages_below} 页")

    # 模态弹窗警告
    active_dialog = dom_info.get("activeDialog") or {}
    is_modal = bool(active_dialog.get("isModal"))
    dialog_title = active_dialog.get("title", "") if active_dialog else ""

    if active_dialog.get("present"):
        if is_modal:
            title_part = f"【{dialog_title}】" if dialog_title else ""
            lines.append(f"|模态弹窗| {title_part} （元素已过滤为弹窗内，先关闭弹窗再操作主页面）")
        elif dialog_title:
            lines.append(f"|弹窗| 检测到非模态弹窗【{dialog_title}】，可与页面其他元素共存")

    elements = dom_info.get("elements", [])

    # 模态弹窗过滤
    hidden_outside_dialog = 0
    if is_modal:
        in_dialog = [el for el in elements if el.get("inActiveDialog") is True]
        hidden_outside_dialog = len(elements) - len(in_dialog)
        elements = in_dialog

    lines.append(
        f"可交互元素共 {len(elements)} 个"
        + (f"（已隐藏 {hidden_outside_dialog} 个弹窗外元素）" if hidden_outside_dialog else "")
        + ":"
    )
    lines.append("")

    current_keys = set()

    viewport_elements = [el for el in elements if el.get("inViewport")]
    offscreen_elements = [el for el in elements if not el.get("inViewport")]
    display_elements = viewport_elements + offscreen_elements[: max(0, max_elements - len(viewport_elements))]

    for el in display_elements:
        tag = el["tag"]
        idx = el["index"]

        el_key = f"{tag}_{el.get('id', '')}_{el.get('name', '')}_{el.get('text', '')[:20]}"
        current_keys.add(el_key)

        is_new = prev_element_keys is not None and el_key not in prev_element_keys
        new_marker = " *新增*" if is_new else ""

        desc = _build_element_desc(el)
        suffix = _build_element_suffix(el)
        viewport_tag = "" if el.get("inViewport") else " [视口外]"

        lines.append(f"  [{idx}] <{tag}> {desc}{suffix}{viewport_tag}{new_marker}")

    remaining = len(elements) - len(display_elements)
    if remaining > 0:
        lines.append(f"  ... 还有 {remaining} 个视口外元素未显示")

    return "\n".join(lines), current_keys


def _build_element_desc(el: dict) -> str:
    """构建单个元素的描述文本"""
    parts: list[str] = []

    if el.get("text"):
        parts.append(f'文本="{el["text"][:50]}"')
    if el.get("placeholder"):
        parts.append(f'placeholder="{el["placeholder"]}"')
    if el.get("name"):
        parts.append(f'name="{el["name"]}"')
    if el.get("id"):
        parts.append(f'id="{el["id"]}"')
    if el.get("aria_label"):
        parts.append(f'aria="{el["aria_label"]}"')
    if el.get("type"):
        parts.append(f'type={el["type"]}')
    if el.get("role"):
        parts.append(f'role={el["role"]}')
    if el.get("href"):
        parts.append(f'href="{el["href"]}"')
    if el.get("value"):
        parts.append(f'当前值="{el["value"][:30]}"')
    if el.get("class"):
        parts.append(f'class="{el["class"][:40]}"')
    if el.get("accept"):
        parts.append(f'accept="{el["accept"]}"')

    # 状态
    if el.get("checked") is True:
        parts.append("✅已选中")
    elif el.get("checked") is False:
        parts.append("⬜未选中")
    if el.get("aria_checked"):
        parts.append(f'aria-checked={el["aria_checked"]}')
    if el.get("aria_selected"):
        parts.append(f'aria-selected={el["aria_selected"]}')
    if el.get("disabled"):
        parts.append("🚫已禁用")
    if el.get("readonly"):
        parts.append("🔒只读(需用js_fill)")
    if el.get("selected_option"):
        parts.append(f'当前选项="{el["selected_option"]}"')

    return ", ".join(parts) if parts else "(无描述)"


def _build_element_suffix(el: dict) -> str:
    """构建元素后缀标签（iframe / 隐式 / shadow / 文件上传）"""
    suffix = ""
    source = el.get("_source", "")

    if el.get("_iframe"):
        suffix = " [iframe]"
    elif source.startswith("implicit"):
        suffix = " [隐式交互]"
    if source.endswith("_shadow"):
        suffix += " [shadow]"

    if el.get("tag") == "input" and el.get("type", "").lower() == "file":
        rect = el.get("rect", {})
        if rect.get("w", 0) < 10 or rect.get("h", 0) < 10:
            suffix += " [文件上传/插件隐藏-用upload_file自动处理]"
        else:
            suffix += " [文件上传]"

    return suffix
