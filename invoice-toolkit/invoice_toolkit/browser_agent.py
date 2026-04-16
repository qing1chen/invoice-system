"""
BrowserAgent — 浏览器自动化 Agent 主循环

本模块只包含：
- BrowserAgent 类（浏览器生命周期 + 动作循环）
- 便捷函数 run_browser_task / run_browser_task_sync
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import sys
import time
import traceback
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from invoice_toolkit.model_browser.models import Action, StepResult, BrowserTaskResult
from invoice_toolkit.model_browser.js_scripts import INIT_EVENT_TRACKER, EXTRACT_DOM, INJECT_LABELS, CLEANUP_LABELS
from invoice_toolkit.model_browser.dom_formatter import format_dom_for_llm
from invoice_toolkit.model_browser.llm_bridge import call_llm
from invoice_toolkit.model_browser.action_executor import execute_action
from invoice_toolkit.model_browser import console as con

logger = logging.getLogger(__name__)


class BrowserAgent:
    """单步 LLM 浏览器自动化代理"""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.siliconflow.cn/v1",
        model: str = "deepseek-ai/DeepSeek-V3",
        headless: bool = False,
        use_vision: bool = False,
        max_steps: int = 20,
        max_failures: int = 5,
        timeout: int = 1000,
        verbose: bool = True,
        debug_llm_raw: bool = False,
        on_step: Optional[Callable[[dict], None]] = None,
        auto_auth: bool = True,
        llm_call_interval: float = 2.0,
        max_llm_calls: int = 30,
    ):
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.headless = headless
        self.use_vision = use_vision
        self.max_steps = max_steps
        self.max_failures = max_failures
        self.timeout = timeout
        self.verbose = verbose
        self.debug_llm_raw = debug_llm_raw
        self.on_step = on_step
        self.auto_auth = auto_auth
        self.llm_call_interval = llm_call_interval
        self.max_llm_calls = max_llm_calls

        # 记录数据库回写
        self.record_id: Optional[int] = None
        self.record_ids: Optional[List[int]] = None
        self.record_seq: Optional[int] = None
        self.record_db = None

        # 浏览器报错收集
        self._browser_errors: List[str] = []

        # Playwright 实例
        self._browser = None
        self._context = None
        self._page = None
        self._active_frame = None

        # LLM 调用控制
        self._llm_call_count = 0
        self._last_llm_call_time = 0.0

        # 元素 diff 追踪
        self._prev_element_keys: set | None = None

        # 反思：动作指纹循环检测
        self._action_fingerprints: List[str] = []
        self._force_skip_repeat_threshold: int = 4
        self._skipped_steps: List[dict] = []

    # ════════════════════════════════════════════════════════════
    #  浏览器生命周期
    # ════════════════════════════════════════════════════════════

    async def _launch_browser(self):
        from playwright.async_api import async_playwright

        self._pw = await async_playwright().start()
        launch_args = ["--no-sandbox", "--disable-dev-shm-usage",
                       "--disable-gpu", "--disable-web-security"]
        if not self.headless:
            launch_args.append("--disable-software-rasterizer")

        self._browser = await self._pw.chromium.launch(headless=self.headless, args=launch_args)
        self._context = await self._browser.new_context(
            viewport={"width": 1280, "height": 900}, locale="zh-CN",
        )
        self._page = await self._context.new_page()

        await self._context.add_init_script(INIT_EVENT_TRACKER)
        self._context.on("page", self._on_new_page)

        if self.verbose:
            mode = "有头（可通过 noVNC 查看）" if not self.headless else "无头"
            print(f"  {con.D}🖥️  浏览器已启动 — {mode}{con.RST}")
            if not self.headless:
                display = os.environ.get("DISPLAY", "未设置")
                print(f"  {con.D}   DISPLAY={display}{con.RST}")
                print(f"  {con.C}   📺 如在 Docker 中，请访问 http://localhost:6080 查看浏览器{con.RST}")

    def _on_new_page(self, page) -> None:
        asyncio.ensure_future(self._handle_new_page(page))

    async def _handle_new_page(self, page) -> None:
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=10000)
        except Exception:
            pass
        url = page.url or ""
        logger.info("[Popup] 检测到新标签页: %s", url[:120])

        noise_patterns = ["news.jsp", "/notice", "/bulletin", "/announcement"]
        if any(p in url.lower() for p in noise_patterns):
            if self.verbose:
                print(f"  {con.Y}🚫 关闭干扰弹窗: {url[:80]}{con.RST}")
            try:
                await page.close()
            except Exception as e:
                logger.debug("关闭弹窗失败: %s", e)
        else:
            if self.verbose:
                print(f"  {con.G}🔀 切换到新标签页: {url[:80]}{con.RST}")
            self._page = page

    async def _close_browser(self):
        for resource in (self._page, self._context, self._browser):
            try:
                if resource:
                    await resource.close()
            except Exception as e:
                logger.debug("关闭浏览器资源出错: %s", e)
        if hasattr(self, "_pw") and self._pw:
            try:
                await self._pw.stop()
            except Exception:
                pass

    # ════════════════════════════════════════════════════════════
    #  页面状态获取
    # ════════════════════════════════════════════════════════════

    async def _get_page_state(self) -> tuple[dict, str, Optional[str]]:
        """获取当前页面可交互元素 + 格式化文本 + 可选截图"""
        if self._page is None or self._page.is_closed():
            logger.warning("页面已关闭，跳过状态获取")
            empty = {"url": "", "title": "", "element_count": 0, "elements": [], "scroll": {}}
            return empty, "", None

        # 等待页面稳定
        for state in ("domcontentloaded", "networkidle"):
            try:
                await self._page.wait_for_load_state(state, timeout=10000 if state == "domcontentloaded" else 8000)
            except Exception:
                pass

        # 等待 loading 遮罩消失
        try:
            await self._page.wait_for_function("""() => {
                const indicators = document.querySelectorAll(
                    '.blockUI, .loading, .spinner, [class*="loading"], [class*="spinner"], '
                    + '.el-loading-mask, .ant-spin-spinning, .modal-backdrop'
                );
                for (const el of indicators) {
                    const style = window.getComputedStyle(el);
                    if (style.display !== 'none' && style.visibility !== 'hidden' && el.offsetParent !== null)
                        return false;
                }
                return true;
            }""", timeout=10000)
        except Exception:
            pass

        await asyncio.sleep(1.0)

        # DOM 提取
        dom_extract_failed = False
        try:
            dom_info = await self._page.evaluate(EXTRACT_DOM)
        except Exception as e:
            logger.warning("DOM 提取失败: %s", e)
            dom_extract_failed = True
            dom_info = {
                "url": self._page.url if not self._page.is_closed() else "",
                "title": "", "element_count": 0, "elements": [], "scroll": {},
            }

        # iframe 合并
        dom_info = await self._merge_iframe_elements(dom_info)

        # 标注覆盖层
        try:
            visible = [el for el in dom_info.get("elements", []) if el.get("inViewport")][:60]
            await self._page.evaluate(INJECT_LABELS, visible)
        except Exception:
            pass

        # 格式化
        dom_text, current_keys = format_dom_for_llm(dom_info, prev_element_keys=self._prev_element_keys)
        self._prev_element_keys = current_keys

        # 截图回退
        screenshot_b64 = None
        if self.use_vision and (dom_extract_failed or dom_info.get("element_count", 0) == 0):
            if not self._page.is_closed():
                try:
                    screenshot_bytes = await self._page.screenshot(type="png")
                    screenshot_b64 = base64.b64encode(screenshot_bytes).decode("utf-8")
                    if self.verbose:
                        reason = "DOM 提取失败" if dom_extract_failed else "DOM 元素为 0"
                        print(f"  {con.D}📷 {reason}，回退到截图{con.RST}")
                except Exception as e:
                    logger.warning("截图失败（回退）: %s", e)

        return dom_info, dom_text, screenshot_b64

    async def _merge_iframe_elements(self, dom_info: dict) -> dict:
        """扫描 iframe 并合并去重元素"""
        best_frame = None
        best_frame_elements: list = []
        best_frame_url = ""
        self._active_frame = self._page

        try:
            for frame in self._page.frames:
                if frame == self._page.main_frame:
                    continue
                try:
                    try:
                        await frame.wait_for_load_state("domcontentloaded", timeout=3000)
                    except Exception:
                        pass
                    frame_dom = await frame.evaluate(EXTRACT_DOM)
                    if frame_dom.get("element_count", 0) > len(best_frame_elements):
                        best_frame_elements = frame_dom.get("elements", [])
                        best_frame = frame
                        best_frame_url = frame.url
                except Exception as e:
                    logger.debug("frame 提取失败: %s", e)
        except Exception as e:
            logger.debug("frame 扫描异常: %s", e)

        if not best_frame_elements:
            return dom_info

        main_elements = dom_info.get("elements", [])
        main_count = len(main_elements)

        # 去重
        main_keys = set()
        for el in main_elements:
            r = el.get("rect", {})
            kx = round(r.get("x", 0) / 5) * 5
            ky = round(r.get("y", 0) / 5) * 5
            txt = (el.get("text", "") or "")[:20]
            main_keys.add(f"{el.get('tag', '')}_{kx}_{ky}_{txt}")

        deduped = []
        for el in best_frame_elements:
            r = el.get("rect", {})
            kx = round(r.get("x", 0) / 5) * 5
            ky = round(r.get("y", 0) / 5) * 5
            txt = (el.get("text", "") or "")[:20]
            key = f"{el.get('tag', '')}_{kx}_{ky}_{txt}"
            if key not in main_keys:
                deduped.append(el)
                main_keys.add(key)

        # 模态弹窗归属
        main_dialog = dom_info.get("activeDialog") or {}
        if main_dialog.get("isModal"):
            for el in deduped:
                el["inActiveDialog"] = False

        # 合并 & 重编号
        all_elements = main_elements + deduped
        for i, el in enumerate(all_elements):
            el["index"] = i
        for el in deduped:
            el["_iframe"] = True

        dom_info["elements"] = all_elements
        dom_info["element_count"] = len(all_elements)
        dom_info["has_iframe"] = True
        dom_info["iframe_url"] = best_frame_url

        if len(deduped) > main_count:
            self._active_frame = best_frame
            self._active_frame_url = best_frame_url

        if self.verbose:
            dup_removed = len(best_frame_elements) - len(deduped)
            dup_msg = f", 去重移除 {dup_removed} 个" if dup_removed > 0 else ""
            print(f"  {con.D}📎 检测到 iframe ({best_frame_url[:60]}), "
                  f"合并了 {len(deduped)} 个元素 "
                  f"(主页 {main_count} + iframe {len(deduped)}{dup_msg}){con.RST}")

        return dom_info

    async def _cleanup_labels(self):
        try:
            await self._page.evaluate(CLEANUP_LABELS)
        except Exception:
            pass
        try:
            for frame in self._page.frames:
                if frame != self._page.main_frame:
                    try:
                        await frame.evaluate(CLEANUP_LABELS)
                    except Exception:
                        pass
        except Exception:
            pass

    # ════════════════════════════════════════════════════════════
    #  循环检测（安全兜底）
    # ════════════════════════════════════════════════════════════

    @staticmethod
    def _action_fingerprint(action: Action) -> str:
        return f"{action.type}|{action.index}|{action.value[:30] if action.value else ''}"

    def _detect_loop(self) -> tuple[bool, str]:
        """同一模式重复 N 次 → 触发强制跳过"""
        fps = self._action_fingerprints
        min_repeats = self._force_skip_repeat_threshold
        for period in (2, 3):
            needed = period * min_repeats
            if len(fps) < needed:
                continue
            tail = fps[-needed:]
            pattern = tail[-period:]
            if all(tail[i:i + period] == pattern for i in range(0, needed, period)):
                desc = " → ".join(f"{fp.split('|')[0]}(idx={fp.split('|')[1]})" for fp in pattern)
                return True, f"同一操作模式重复 {min_repeats} 次（周期={period}）: {desc}"
        return False, ""

    # ════════════════════════════════════════════════════════════
    #  错误收集 & 数据库回写
    # ════════════════════════════════════════════════════════════

    def _collect_error(self, step_num: int, error_msg: str) -> None:
        short = error_msg[:200]
        self._browser_errors.append(f"[步骤{step_num}] {short}")

    def _save_browser_errors(self) -> None:
        if not self._browser_errors or not self.record_db:
            return
        error_text = "; ".join(self._browser_errors)
        try:
            written = self._write_errors_to_db(error_text, append=True)
            if self.verbose:
                print(f"  {con.D}📝 浏览器报错已写入记录数据库 ({len(self._browser_errors)} 条, {written} 条记录){con.RST}")
        except Exception as e:
            logger.warning("写入浏览器报错到数据库失败: %s", e)

    def _save_browser_errors_immediate(self, step_num: int, error_msg: str) -> None:
        if not self.record_db:
            if self.verbose:
                print(f"  {con.Y}⚠️  record_db 未注入，跳过原因未写入数据库{con.RST}")
            return
        entry = f"[步骤{step_num}] {error_msg[:200]}"
        try:
            written = self._write_errors_to_db(entry, append=True)
            if self.verbose:
                if written:
                    print(f"  {con.G}📝 跳过原因已实时写入数据库 ({written} 条记录){con.RST}")
                else:
                    print(f"  {con.Y}⚠️  写入数据库返回无匹配{con.RST}")
        except Exception as e:
            logger.warning("实时写入浏览器报错失败: %s", e)

    def _write_errors_to_db(self, error_text: str, *, append: bool = True) -> int:
        """统一的数据库写入方法"""
        written = 0
        if self.record_ids:
            for rid in self.record_ids:
                if self.record_db.upsert_browser_error(rid, error_text, append=append):
                    written += 1
        elif self.record_id:
            if self.record_db.upsert_browser_error(self.record_id, error_text, append=append):
                written += 1
        elif self.record_seq is not None:
            written = self.record_db.upsert_browser_error_by_seq(self.record_seq, error_text, append=append)
        return written

    # ════════════════════════════════════════════════════════════
    #  弹窗关闭（循环退出 / skip 后清理）
    # ════════════════════════════════════════════════════════════

    async def _try_close_dialog(self) -> bool:
        try:
            closed = await self._page.evaluate("""() => {
                const closeBtn = document.querySelector(
                    '.ui-dialog-titlebar-close, [aria-label="Close"], '
                    + 'button.close, .modal-header .close, '
                    + '[class*="dialog"] [class*="close"]'
                );
                if (closeBtn) { closeBtn.click(); return true; }
                const btns = document.querySelectorAll('button, a.btn, [role="button"]');
                for (const b of btns) {
                    const t = (b.textContent || '').trim();
                    if (['取消', '关闭', 'Close', 'Cancel', '确认', 'OK'].includes(t)) {
                        b.click(); return true;
                    }
                }
                return false;
            }""")
            if closed and self.verbose:
                print(f"  {con.G}✅ 已自动关闭弹窗{con.RST}")
            await asyncio.sleep(1.5)
            return bool(closed)
        except Exception:
            return False

    # ════════════════════════════════════════════════════════════
    #  主循环
    # ════════════════════════════════════════════════════════════

    async def run(self, task: str, url: str) -> BrowserTaskResult:
        """执行浏览器任务。"""
        start_time = time.time()
        steps_log: list[str] = []
        step_details: list[dict] = []
        history: list[str] = []
        consecutive_failures = 0

        if self.verbose:
            print(f"\n{'=' * 55}")
            print(f"  {con.B}{con.C}🌐 浏览器自动化任务{con.RST}")
            print(f"  {con.D}模型: {self.model}  |  视觉: {'开' if self.use_vision else '关'}  |  "
                  f"步数上限: {self.max_steps}  |  LLM 上限: {self.max_llm_calls}{con.RST}")
            print(f"  {con.D}URL:  {url}{con.RST}")
            print(f"  {con.D}任务: {task[:80]}{con.RST}")
            print(f"{'=' * 55}")
            sys.stdout.flush()

        try:
            await self._launch_browser()

            if self.verbose:
                print(f"  {con.D}⏳ 正在打开 {url}...{con.RST}")
            await self._page.goto(url, wait_until="domcontentloaded", timeout=60000)
            try:
                await self._page.wait_for_load_state("networkidle", timeout=60000)
            except Exception:
                pass
            if self.verbose:
                print(f"  {con.G}✅ 页面已加载: {self._page.url[:80]}{con.RST}")

            # 自动认证
            if self.auto_auth:
                await self._try_auto_auth(url)

            # ── 步骤循环 ──
            for step_num in range(1, self.max_steps + 1):
                result = await self._execute_step(
                    step_num, task, start_time,
                    steps_log, step_details, history,
                    consecutive_failures,
                )

                if isinstance(result, BrowserTaskResult):
                    return result

                # result 是更新后的 consecutive_failures
                consecutive_failures = result

            # 达到最大步数
            total_elapsed = time.time() - start_time
            msg = f"达到最大步数 {self.max_steps}"
            if self.verbose:
                print(f"\n  {con.Y}{con.B}⚠️ {msg}{con.RST}")
            self._save_browser_errors()
            return BrowserTaskResult(
                success=False, message=msg,
                steps=steps_log, step_details=step_details,
                total_steps=self.max_steps, error="max_steps",
                elapsed_seconds=total_elapsed,
                browser_errors=list(self._browser_errors),
            )

        except Exception as e:
            total_elapsed = time.time() - start_time
            logger.error("任务异常: %s", e, exc_info=True)
            if self.verbose:
                print(f"\n  {con.R}{con.B}❌ 异常: {e}{con.RST}")
                traceback.print_exc()
            self._collect_error(0, str(e))
            self._save_browser_errors()
            return BrowserTaskResult(
                success=False, message=f"任务异常: {e}",
                steps=steps_log, step_details=step_details,
                error=str(e), elapsed_seconds=total_elapsed,
                browser_errors=list(self._browser_errors),
            )

        finally:
            total_elapsed = time.time() - start_time
            if self.verbose:
                print(f"\n{'=' * 55}")
                print(f"  {con.D}总耗时: {total_elapsed:.1f}s, 共 {len(steps_log)} 步{con.RST}")
                print(f"  {con.D}💰 LLM 调用: {self._llm_call_count} 次 (上限 {self.max_llm_calls}){con.RST}")
                print(f"{'=' * 55}\n")
            await self._close_browser()

    async def _execute_step(
        self,
        step_num: int,
        task: str,
        start_time: float,
        steps_log: list,
        step_details: list,
        history: list,
        consecutive_failures: int,
    ) -> BrowserTaskResult | int:
        """执行单步。返回 BrowserTaskResult（终止） 或 int（更新后的 consecutive_failures）"""
        step_start = time.time()

        if self.verbose:
            effective_max = min(self.max_steps, self.max_llm_calls)
            con.print_step_header(step_num, effective_max)

        # 1. 获取页面状态
        dom_info, dom_text, screenshot_b64 = await self._get_page_state()

        if self.verbose:
            try:
                page_url = self._page.url if (self._page and not self._page.is_closed()) else dom_info.get("url", "")
            except Exception:
                page_url = dom_info.get("url", "")
            con.print_dom_summary(dom_text, page_url)

        # 2. LLM 调用上限检查
        if self._llm_call_count >= self.max_llm_calls:
            total_elapsed = time.time() - start_time
            msg = f"已达到 LLM 调用上限 ({self.max_llm_calls} 次)"
            if self.verbose:
                print(f"\n  {con.R}{con.B}💰 {msg}{con.RST}")
            self._save_browser_errors()
            return BrowserTaskResult(
                success=False, message=msg,
                steps=steps_log, step_details=step_details,
                total_steps=step_num, error="max_llm_calls",
                elapsed_seconds=total_elapsed,
                browser_errors=list(self._browser_errors),
            )

        # 3. 安全兜底：循环检测
        is_stuck, stuck_desc = self._detect_loop()
        if is_stuck:
            return await self._handle_forced_skip(
                step_num, step_start, stuck_desc,
                steps_log, step_details, history,
            )

        # 4. 限流
        elapsed_since = time.time() - self._last_llm_call_time
        if elapsed_since < self.llm_call_interval:
            wait = self.llm_call_interval - elapsed_since
            if self.verbose:
                print(f"  {con.D}⏱️  限流等待 {wait:.1f}s{con.RST}")
            await asyncio.sleep(wait)

        self._llm_call_count += 1
        if self.verbose and self._llm_call_count > 1:
            print(f"  {con.D}📊 LLM 调用 #{self._llm_call_count}/{self.max_llm_calls}{con.RST}")

        # 5. 调用 LLM
        raw, thinking, action = await call_llm(
            api_key=self.api_key, base_url=self.base_url, model=self.model,
            task=task, dom_text=dom_text, history=history,
            screenshot_b64=screenshot_b64, use_vision=self.use_vision,
        )
        self._last_llm_call_time = time.time()

        if self.verbose:
            con.print_thinking(thinking)
            con.print_action(action)
            if self.debug_llm_raw:
                con.print_llm_raw(raw)

        # 6. done
        if action.type == "done":
            return self._handle_done(
                step_num, step_start, action, raw, dom_text,
                steps_log, step_details, history, start_time,
            )

        # 7. skip
        if action.type == "skip":
            await self._handle_skip(step_num, step_start, action, thinking, steps_log, step_details, history)
            return 0  # reset consecutive_failures

        # 8. 记录动作指纹
        self._action_fingerprints.append(self._action_fingerprint(action))

        # 9. 清理标注后执行动作
        await self._cleanup_labels()

        success, exec_msg, error = True, "", ""
        try:
            exec_msg = await execute_action(
                self._page, action, dom_info,
                active_frame=getattr(self, "_active_frame", None),
            )
            consecutive_failures = 0
            if action.type in ("click", "js_click", "go_to_url"):
                await asyncio.sleep(2.0)
        except Exception as e:
            err_str = str(e)
            self._collect_error(step_num, err_str)

            # 页面崩溃
            if "Target closed" in err_str or "target page" in err_str.lower():
                return self._handle_crash(
                    step_num, step_start, action, err_str,
                    steps_log, step_details, history, start_time,
                )

            exec_msg = self._build_error_message(action, err_str)
            success = False
            error = err_str
            consecutive_failures += 1
            logger.warning("[Step %d] 执行失败: %s", step_num, e)
            if self.verbose:
                print(f"  {con.Y}🔄 错误已记录，将反馈给 LLM{con.RST}")

        elapsed = int((time.time() - step_start) * 1000)

        # 10. 记录
        status = "✅" if success else "❌"
        steps_log.append(f"步骤 {step_num}: {status} {action.description or action.type} — {exec_msg[:120]}")
        history.append(f"[{step_num}] {status} {exec_msg[:200]}")

        step_result = StepResult(
            step_number=step_num, action=action, success=success,
            message=exec_msg, error=error,
            page_url=self._page.url if not self._page.is_closed() else "",
            timestamp=datetime.now().strftime("%H:%M:%S"),
            llm_raw=raw[:500] if self.debug_llm_raw else "",
            dom_summary=dom_text[:200], elapsed_ms=elapsed,
        )
        step_details.append(step_result.to_dict())

        if self.verbose:
            con.print_result(success, exec_msg, elapsed)
        if self.on_step:
            self.on_step(step_details[-1])

        # 11. 连续失败检查
        if consecutive_failures >= self.max_failures:
            total_elapsed = time.time() - start_time
            msg = f"连续失败 {consecutive_failures} 次，终止任务"
            if self.verbose:
                print(f"\n  {con.R}{con.B}⚠️ {msg}{con.RST}")
            self._save_browser_errors()
            return BrowserTaskResult(
                success=False, message=msg,
                steps=steps_log, step_details=step_details,
                total_steps=step_num, error="max_failures",
                elapsed_seconds=total_elapsed,
                browser_errors=list(self._browser_errors),
            )

        # 12. 超时检查
        if time.time() - start_time > self.timeout:
            total_elapsed = time.time() - start_time
            msg = f"任务超时（{self.timeout}秒）"
            if self.verbose:
                print(f"\n  {con.R}{con.B}⏰ {msg}{con.RST}")
            self._save_browser_errors()
            return BrowserTaskResult(
                success=False, message=msg,
                steps=steps_log, step_details=step_details,
                total_steps=step_num, error="timeout",
                elapsed_seconds=total_elapsed,
                browser_errors=list(self._browser_errors),
            )

        await asyncio.sleep(0.5)
        return consecutive_failures

    # ── 步骤处理子方法 ──

    def _handle_done(self, step_num, step_start, action, raw, dom_text,
                     steps_log, step_details, history, start_time) -> BrowserTaskResult:
        elapsed = int((time.time() - step_start) * 1000)
        result_msg = f"任务完成: {action.value}"
        steps_log.append(f"步骤 {step_num}: ✅ {result_msg}")
        history.append(f"[{step_num}] ✅ {result_msg}")
        step_details.append(StepResult(
            step_number=step_num, action=action, success=True,
            message=result_msg, page_url=self._page.url,
            timestamp=datetime.now().strftime("%H:%M:%S"), elapsed_ms=elapsed,
        ).to_dict())

        if self.verbose:
            con.print_result(True, result_msg, elapsed)
        if self.on_step:
            self.on_step(step_details[-1])

        total_elapsed = time.time() - start_time
        self._save_browser_errors()

        skip_note = f"（注意：有 {len(self._skipped_steps)} 个步骤被跳过）" if self._skipped_steps else ""
        return BrowserTaskResult(
            success=True, message=(action.value or "任务完成") + skip_note,
            steps=steps_log, step_details=step_details,
            total_steps=step_num, elapsed_seconds=total_elapsed,
            llm_call_count=self._llm_call_count,
            browser_errors=list(self._browser_errors),
        )

    async def _handle_skip(self, step_num, step_start, action, thinking, steps_log, step_details, history):
        elapsed = int((time.time() - step_start) * 1000)
        skip_reason = action.value or action.description or "LLM 主动跳过"
        self._collect_error(step_num, f"LLM 跳过: {skip_reason}")
        self._skipped_steps.append({"step": step_num, "reason": skip_reason, "thinking": thinking[:200]})
        self._save_browser_errors_immediate(step_num, f"LLM 跳过: {skip_reason}")

        if self.verbose:
            print(f"  {con.Y}⏭️ LLM 主动跳过: {skip_reason[:80]}{con.RST}")

        steps_log.append(f"步骤 {step_num}: ⏭️ 跳过 — {skip_reason[:80]}")
        history.append(f"[{step_num}] ⏭️ 跳过: {skip_reason[:100]}")
        step_details.append(StepResult(
            step_number=step_num, action=action, success=False,
            message=f"跳过: {skip_reason}", error="skipped",
            page_url=self._page.url if not self._page.is_closed() else "",
            timestamp=datetime.now().strftime("%H:%M:%S"), elapsed_ms=elapsed,
        ).to_dict())

        if self.on_step:
            self.on_step(step_details[-1])

        await self._try_close_dialog()
        self._action_fingerprints.clear()
        await asyncio.sleep(0.5)

    async def _handle_forced_skip(self, step_num, step_start, stuck_desc,
                                   steps_log, step_details, history) -> int:
        if self.verbose:
            print(f"  {con.R}{con.B}🚨 安全兜底触发：{stuck_desc}{con.RST}")
            print(f"  {con.R}   LLM 未能自主跳出循环，系统强制跳过{con.RST}")

        skip_reason = f"系统强制跳过（安全兜底）：{stuck_desc}"
        self._collect_error(step_num, skip_reason)
        self._skipped_steps.append({"step": step_num, "reason": skip_reason, "loop_pattern": stuck_desc})
        self._save_browser_errors_immediate(step_num, skip_reason)

        await self._try_close_dialog()

        steps_log.append(f"步骤 {step_num}: ⏭️ 强制跳过 — {skip_reason[:80]}")
        history.append(f"[{step_num}] ⏭️ 系统强制跳过: {skip_reason[:100]}")
        step_details.append(StepResult(
            step_number=step_num,
            action=Action(type="skip", value=skip_reason, description="系统强制跳过"),
            success=False, message=skip_reason, error="forced_skip",
            page_url=self._page.url if not self._page.is_closed() else "",
            timestamp=datetime.now().strftime("%H:%M:%S"),
            elapsed_ms=int((time.time() - step_start) * 1000),
        ).to_dict())

        self._action_fingerprints.clear()
        await asyncio.sleep(0.5)
        return 0  # reset consecutive_failures

    def _handle_crash(self, step_num, step_start, action, err_str,
                      steps_log, step_details, history, start_time) -> BrowserTaskResult:
        exec_msg = f"执行失败（页面崩溃）: {err_str.splitlines()[0]}"
        logger.error("[Step %d] 页面崩溃，无法继续: %s", step_num, err_str[:100])
        if self.verbose:
            print(f"  {con.R}💥 页面崩溃，任务将终止{con.RST}")

        steps_log.append(f"步骤 {step_num}: ❌ {action.description or action.type} — {exec_msg[:80]}")
        history.append(f"[{step_num}] ❌ {exec_msg[:100]}")
        step_details.append(StepResult(
            step_number=step_num, action=action, success=False,
            message=exec_msg, error=err_str,
            page_url="(crashed)",
            timestamp=datetime.now().strftime("%H:%M:%S"),
            elapsed_ms=int((time.time() - step_start) * 1000),
        ).to_dict())

        total_elapsed = time.time() - start_time
        self._save_browser_errors()
        return BrowserTaskResult(
            success=False, message=f"页面崩溃终止: {err_str[:100]}",
            steps=steps_log, step_details=step_details,
            total_steps=step_num, error="page_crashed",
            elapsed_seconds=total_elapsed,
            browser_errors=list(self._browser_errors),
        )

    @staticmethod
    def _build_error_message(action: Action, err_str: str) -> str:
        if "intercepts pointer events" in err_str or "subtree intercepts" in err_str:
            hint = ("💡 点击被遮挡层拦截 — 页面上有未关闭的弹窗/对话框/遮罩。"
                    "下一步请先在当前弹窗内完成操作。")
            return f"执行失败: {err_str.splitlines()[0]} — {hint}"
        if "Timeout" in err_str and "exceeded" in err_str:
            timeout_hint = ""
            if action.type == "upload_file":
                timeout_hint = " — 💡 文件上传超时，建议 skip 或尝试 click 上传按钮"
            return f"执行失败（超时）: {err_str.splitlines()[0]}{timeout_hint}"
        return f"执行失败: {err_str.splitlines()[0] if err_str else '未知错误'}"

    # ── 自动认证 ──

    async def _try_auto_auth(self, url: str):
        try:
            try:
                from invoice_toolkit.browser_auth import BrowserAuth
            except ImportError:
                from browser_auth import BrowserAuth

            auth = BrowserAuth.from_env(verbose=self.verbose)
            logged_in = await auth.ensure_logged_in(self._page, url)
            if not logged_in and self.verbose:
                print(f"  {con.Y}⚠️  自动登录失败，LLM 将尝试处理登录页面{con.RST}")
        except ImportError as ie:
            logger.warning("[Auth] browser_auth 模块不可用: %s", ie)
            if self.verbose:
                print(f"  {con.Y}⚠️  browser_auth 模块未找到: {ie}{con.RST}")
        except Exception as e:
            logger.warning("[Auth] 自动认证异常: %s", e, exc_info=True)
            if self.verbose:
                print(f"  {con.Y}⚠️  自动认证异常: {e}{con.RST}")

    # ── 类方法：从 Settings 创建 ──

    @classmethod
    async def from_settings(
        cls, task: str, url: str, settings=None, *,
        verbose: bool = True, debug_llm_raw: bool = False,
        on_step: Optional[Callable[[dict], None]] = None,
        auto_auth: bool = True,
        record_ids: Optional[List[int]] = None,
        record_id: Optional[int] = None,
        record_seq: Optional[int] = None,
        record_db=None,
        **overrides,
    ) -> BrowserTaskResult:
        if settings is None:
            from invoice_toolkit.config import Settings
            settings = Settings.from_env()

        browser_cfg = settings.browser
        llm_cfg = settings.llm

        agent = cls(
            api_key=llm_cfg.api_key,
            base_url=llm_cfg.base_url,
            model=overrides.pop("model", browser_cfg.model_name),
            headless=overrides.pop("headless", browser_cfg.headless),
            use_vision=overrides.pop("use_vision", browser_cfg.use_vision),
            max_steps=overrides.pop("max_steps", browser_cfg.max_steps),
            max_failures=overrides.pop("max_failures", browser_cfg.max_failures),
            timeout=overrides.pop("timeout", browser_cfg.timeout),
            llm_call_interval=overrides.pop("llm_call_interval", browser_cfg.llm_call_interval),
            max_llm_calls=overrides.pop("max_llm_calls", browser_cfg.max_llm_calls),
            verbose=verbose, debug_llm_raw=debug_llm_raw,
            on_step=on_step, auto_auth=auto_auth,
        )

        agent.record_ids = record_ids
        agent.record_id = record_id
        agent.record_seq = record_seq
        if record_db is not None:
            agent.record_db = record_db
        elif record_ids or record_id is not None or record_seq is not None:
            try:
                from invoice_toolkit.database import get_record_db
                agent.record_db = get_record_db(settings)
            except Exception as e:
                logger.warning("自动获取 record_db 失败: %s", e)

        return await agent.run(task, url)


# ════════════════════════════════════════════════════════════
#  便捷函数
# ════════════════════════════════════════════════════════════

async def run_browser_task(
    task: str, url: str, *,
    settings=None,
    on_step: Optional[Callable[[dict], None]] = None,
    record_ids: Optional[List[int]] = None,
    record_id: Optional[int] = None,
    record_seq: Optional[int] = None,
    record_db=None,
    **overrides,
) -> BrowserTaskResult:
    return await BrowserAgent.from_settings(
        task, url, settings=settings, on_step=on_step,
        record_ids=record_ids, record_id=record_id,
        record_seq=record_seq, record_db=record_db,
        **overrides,
    )


def run_browser_task_sync(task: str, url: str, *, settings=None, **overrides) -> BrowserTaskResult:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    coro = run_browser_task(task, url, settings=settings, **overrides)
    if loop and loop.is_running():
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()
    else:
        return asyncio.run(coro)
