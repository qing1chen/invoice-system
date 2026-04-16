"""动作执行器 — 在 Playwright Page 上执行 Action"""

from __future__ import annotations

import asyncio
import logging

from .models import Action

logger = logging.getLogger(__name__)


async def execute_action(page, action: Action, dom_info: dict, active_frame=None) -> str:
    """在页面上执行动作。返回执行结果描述。出错时抛出异常。"""
    elements = dom_info.get("elements", [])

    if action.type == "done":
        return f"任务完成: {action.value}"

    if action.type == "skip":
        return f"跳过: {action.value}"

    if action.type == "wait":
        seconds = max(1, min(10, int(float(action.value or "2"))))
        await asyncio.sleep(seconds)
        return f"等待 {seconds} 秒"

    if action.type == "go_to_url":
        await page.goto(action.value, wait_until="domcontentloaded", timeout=30000)
        return f"导航到 {action.value}"

    if action.type == "scroll":
        return await _execute_scroll(page, action, active_frame)

    if action.type == "upload_file":
        return await _execute_upload(page, action, elements, active_frame)

    if action.type == "js_click":
        return await _execute_js_click(page, action, active_frame)

    # 需要定位元素的动作: click / fill / js_fill / select
    if action.index < 0 or action.index >= len(elements):
        raise ValueError(f"元素索引 {action.index} 超出范围 [0, {len(elements)-1}]")

    return await _execute_element_action(page, action, elements, active_frame)


# ── 滚动 ──

async def _execute_scroll(page, action: Action, active_frame) -> str:
    pixels = int(float(action.value or "300"))
    scrolled = False

    if active_frame and active_frame != page:
        try:
            old_scroll = await active_frame.evaluate("window.scrollY || document.documentElement.scrollTop || 0")
            await active_frame.evaluate(f"window.scrollBy(0, {pixels})")
            await asyncio.sleep(0.3)
            new_scroll = await active_frame.evaluate("window.scrollY || document.documentElement.scrollTop || 0")
            if abs(new_scroll - old_scroll) > 5:
                scrolled = True
        except Exception:
            pass

        if not scrolled:
            try:
                scrolled = await active_frame.evaluate("""
                    (px) => {
                        const all = document.querySelectorAll('*');
                        let best = null, bestArea = 0;
                        for (const el of all) {
                            if (el.scrollHeight > el.clientHeight + 10) {
                                const rect = el.getBoundingClientRect();
                                const area = rect.width * rect.height;
                                if (area > bestArea) { bestArea = area; best = el; }
                            }
                        }
                        if (best) {
                            const before = best.scrollTop;
                            best.scrollBy(0, px);
                            return Math.abs(best.scrollTop - before) > 1;
                        }
                        document.documentElement.scrollBy(0, px);
                        document.body.scrollBy(0, px);
                        return true;
                    }
                """, pixels)
            except Exception:
                pass

    if not scrolled:
        try:
            await page.evaluate(f"window.scrollBy(0, {pixels})")
        except Exception:
            pass

    await asyncio.sleep(0.5)
    target_desc = "iframe内" if (active_frame and active_frame != page and scrolled) else "主页面"
    return f"滚动 {pixels}px ({target_desc})"


# ── 文件上传 ──

async def _execute_upload(page, action: Action, elements: list, active_frame) -> str:
    file_path = action.value
    if not file_path:
        raise ValueError("upload_file 需要提供 value（文件路径）")
    if action.index < 0 or action.index >= len(elements):
        raise ValueError(f"元素索引 {action.index} 超出范围 [0, {len(elements)-1}]")

    el_info = elements[action.index]
    is_iframe_el = el_info.get("_iframe", False)
    target = active_frame if (is_iframe_el and active_frame) else page

    # 方式1: locator
    locator = _build_file_locator(target, el_info)
    if locator:
        try:
            count = await locator.count()
            if count > 0:
                await locator.first.set_input_files(file_path, timeout=10000)
                await asyncio.sleep(1.0)
                return f"上传文件 [{action.index}] ← '{file_path}'"
        except Exception as e:
            logger.warning("locator set_input_files 失败: %s, 尝试 JS 注入方式", e)

    # 方式2: Uploadifive 插件
    try:
        file_input_found = await target.evaluate("""
            () => {
                const uploadifive = document.querySelector('.uploadifive-button input[type="file"]');
                if (uploadifive) return true;
                return document.querySelectorAll('input[type="file"]').length > 0;
            }
        """)
        if file_input_found:
            file_input_locator = target.locator('.uploadifive-button input[type="file"]')
            count = await file_input_locator.count()
            if count == 0:
                file_input_locator = target.locator('input[type="file"]')
            await file_input_locator.first.set_input_files(file_path, timeout=10000)
            await asyncio.sleep(1.0)
            return f"上传文件(uploadifive) [{action.index}] ← '{file_path}'"
    except Exception as e:
        logger.warning("Uploadifive file input 方式失败: %s, 尝试 file_chooser", e)

    # 方式3: file_chooser 事件
    try:
        async with target.expect_file_chooser(timeout=5000) as fc_info:
            rect = el_info.get("rect", {})
            x = rect.get("x", 0) + rect.get("w", 0) // 2
            y = rect.get("y", 0) + rect.get("h", 0) // 2
            if x > 0 and y > 0:
                await page.mouse.click(x, y)
            else:
                await target.evaluate("""
                    () => {
                        const btn = document.querySelector('.uploadifive-button')
                                 || document.querySelector('[class*="upload"]');
                        if (btn) btn.click();
                    }
                """)
        file_chooser = await fc_info.value
        await file_chooser.set_files(file_path)
        await asyncio.sleep(1.0)
        return f"上传文件(file_chooser) [{action.index}] ← '{file_path}'"
    except Exception as e2:
        raise ValueError(f"文件上传失败: locator、uploadifive 和 file_chooser 方式均失败. {e2}")


def _build_file_locator(target, el_info: dict):
    """为文件上传构建 locator"""
    if el_info.get("id"):
        return target.locator(f"#{el_info['id']}")
    if el_info.get("name"):
        return target.locator(f"[name='{el_info['name']}']")

    el_class = el_info.get("class", "")
    if el_class:
        first_class = el_class.split()[0] if el_class.strip() else ""
        if first_class:
            return target.locator(f"input[type='file'].{first_class}")

    if "file" in el_info.get("type", "").lower():
        return target.locator("input[type='file']")

    return None


# ── JS 点击 ──

async def _execute_js_click(page, action: Action, active_frame) -> str:
    selector = action.selector or action.value
    if not selector:
        raise ValueError("js_click 需要提供 selector（CSS 选择器）")

    target = active_frame if active_frame and active_frame != page else page
    clicked = await target.evaluate("""
        (sel) => {
            const el = document.querySelector(sel);
            if (el) { el.click(); return el.textContent?.trim().slice(0, 50) || '(无文本)'; }
            return null;
        }
    """, selector)

    if clicked is None:
        for frame in page.frames:
            if frame == page.main_frame:
                continue
            try:
                clicked = await frame.evaluate("""
                    (sel) => {
                        const el = document.querySelector(sel);
                        if (el) { el.click(); return el.textContent?.trim().slice(0, 50) || '(无文本)'; }
                        return null;
                    }
                """, selector)
                if clicked is not None:
                    break
            except Exception:
                continue

    if clicked is None:
        raise ValueError(f"js_click 未找到匹配 '{selector}' 的元素")

    await asyncio.sleep(0.8)
    return f"JS点击 '{selector}' — {clicked}"


# ── 元素定位动作 (click / fill / js_fill / select) ──

async def _execute_element_action(page, action: Action, elements: list, active_frame) -> str:
    el_info = elements[action.index]
    is_iframe_el = el_info.get("_iframe", False)
    target = active_frame if (is_iframe_el and active_frame) else page
    iframe_tag = " [iframe]" if is_iframe_el else ""

    locator, locate_desc = _build_element_locator(target, el_info, page, action, is_iframe_el)

    # 坐标回退（无法构建 locator 时）
    if locator is None:
        return await _fallback_coordinate_action(page, action, el_info, iframe_tag)

    try:
        await locator.first.scroll_into_view_if_needed(timeout=5000)
    except Exception:
        pass

    if action.type == "click":
        await locator.first.click(timeout=10000)
        await asyncio.sleep(0.8)
        return f"点击 [{action.index}] {locate_desc} — {el_info.get('text', '')[:30]}{iframe_tag}"

    elif action.type in ("fill", "js_fill"):
        return await _execute_fill(locator, action, locate_desc, iframe_tag)

    elif action.type == "select":
        try:
            await locator.first.select_option(label=action.value, timeout=5000)
        except Exception:
            await locator.first.select_option(value=action.value, timeout=5000)
        await asyncio.sleep(0.3)
        return f"选择 [{action.index}] {locate_desc} → '{action.value[:30]}'{iframe_tag}"

    else:
        raise ValueError(f"未知动作类型: {action.type}")


def _build_element_locator(target, el_info, page, action, is_iframe_el):
    """构建元素 locator，返回 (locator, describe_str) 或 (None, "")"""
    if el_info.get("id"):
        return target.locator(f"#{el_info['id']}"), f"id={el_info['id']}"
    if el_info.get("name"):
        return target.locator(f"[name='{el_info['name']}']"), f"name={el_info['name']}"

    # 文本定位（iframe 内）
    if is_iframe_el:
        text = el_info.get("text", "").strip()
        tag = el_info.get("tag", "")
        if text and tag:
            return target.locator(f"{tag}:has-text('{text[:30]}')"), f"text='{text[:30]}'"

    return None, ""


async def _fallback_coordinate_action(page, action: Action, el_info: dict, iframe_tag: str) -> str:
    """坐标回退：无法通过 id/name/text 定位时，直接用坐标操作"""
    rect = el_info.get("rect", {})
    x = rect.get("x", 0) + rect.get("w", 0) // 2
    y = rect.get("y", 0) + rect.get("h", 0) // 2

    if action.type == "click":
        await page.mouse.click(x, y)
        await asyncio.sleep(0.8)
        return f"坐标点击 ({x}, {y}) — {el_info.get('text', '')[:30]}{iframe_tag}"
    elif action.type in ("fill", "js_fill"):
        await page.mouse.click(x, y)
        await asyncio.sleep(0.3)
        await page.keyboard.press("Control+a")
        await page.keyboard.type(action.value, delay=30)
        await asyncio.sleep(0.3)
        return f"坐标填写 ({x}, {y}) ← '{action.value[:30]}'"
    elif action.type == "select":
        await page.mouse.click(x, y)
        await asyncio.sleep(0.5)
        return f"坐标选择 ({x}, {y}) — 尝试选择 '{action.value[:30]}'"

    raise ValueError(f"无法定位元素 [{action.index}]: {el_info}")


async def _execute_fill(locator, action: Action, locate_desc: str, iframe_tag: str) -> str:
    """执行填写：先尝试 Playwright fill，失败则降级为 JS 填写"""
    use_js = (action.type == "js_fill")

    if not use_js:
        try:
            await locator.first.evaluate("""
                el => {
                    el.removeAttribute('readonly');
                    el.removeAttribute('disabled');
                    el.readOnly = false;
                    el.disabled = false;
                }
            """)
            await asyncio.sleep(0.1)
            await locator.first.fill("", timeout=5000)
            await locator.first.fill(action.value, timeout=5000)
            await asyncio.sleep(0.3)
            return f"填写 [{action.index}] {locate_desc} ← '{action.value[:30]}'{iframe_tag}"
        except Exception as e:
            logger.warning("[Fill] Playwright fill 失败 (index=%d): %s — 降级 JS 填写", action.index, e)
            use_js = True

    if use_js:
        await locator.first.evaluate("""
            (el, val) => {
                el.removeAttribute('readonly');
                el.removeAttribute('disabled');
                el.readOnly = false;
                el.disabled = false;

                const nativeSetter = Object.getOwnPropertyDescriptor(
                    HTMLInputElement.prototype, 'value'
                )?.set || Object.getOwnPropertyDescriptor(
                    HTMLTextAreaElement.prototype, 'value'
                )?.set;
                if (nativeSetter) { nativeSetter.call(el, val); }
                else { el.value = val; }

                el.dispatchEvent(new Event('focus', {bubbles: true}));
                el.dispatchEvent(new Event('input', {bubbles: true}));
                el.dispatchEvent(new Event('change', {bubbles: true}));
                el.dispatchEvent(new Event('blur', {bubbles: true}));
            }
        """, action.value)
        await asyncio.sleep(0.3)
        return f"JS填写 [{action.index}] {locate_desc} ← '{action.value[:30]}'{iframe_tag}"
