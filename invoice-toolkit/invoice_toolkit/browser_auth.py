"""
browser_auth.py — 浏览器认证模块（不经过 LLM）

三种认证策略，按优先级尝试：
    A. Cookie 注入 — 从 JSON 文件加载 cookie，跳过登录页
    B. 手动登录   — 打开有头浏览器，用户手动登录，按 Enter 确认后保存 Cookie
    C. 自动登录   — 用 Playwright 直接 fill + click（fallback）

Docker 环境下使用有头浏览器：
    - docker-compose 中配置 noVNC 服务
    - 浏览器显示在 VNC 中，通过 http://localhost:6080 访问
    - 用户在 noVNC 网页中看到浏览器，手动完成登录
    - 在终端（docker logs -f 或 docker attach）中按 Enter 确认

安全原则：
    - 密码只在本进程内存中，绝不发送到任何外部 LLM API
    - Cookie 文件建议放在 .gitignore 中
    - 登录成功后自动导出 Cookie 供下次复用

用法：
    from browser_auth import BrowserAuth

    auth = BrowserAuth.from_env()
    # 在 Playwright page 打开目标 URL 后调用：
    logged_in = await auth.ensure_logged_in(page, "https://pass.sdu.edu.cn/...")

    # 独立 Cookie 采集（有头浏览器 + 手动登录）：
    python browser_auth.py harvest https://pass.sdu.edu.cn/cas/login

环境变量 (.env):
    SDU_USERNAME=你的学号
    SDU_PASSWORD=你的密码
    COOKIE_FILE=data/cookies.json     # 可选，默认 data/cookies.json
    AUTH_MODE=manual                  # manual | auto | cookie_only
    DISPLAY=:99                       # Docker 中由 Xvfb 提供
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ANSI 颜色
_C = "\033[96m"
_G = "\033[92m"
_Y = "\033[93m"
_R = "\033[91m"
_B = "\033[1m"
_D = "\033[2m"
_0 = "\033[0m"


# ════════════════════════════════════════════════════════════
#  配置
# ════════════════════════════════════════════════════════════

@dataclass
class AuthConfig:
    """认证配置"""
    username: str = ""
    password: str = ""
    cookie_file: str = "data/cookies.json"

    # 认证模式: manual（手动优先）| auto（自动优先）| cookie_only（仅Cookie）
    auth_mode: str = "manual"

    # 登录页面识别规则
    login_url_patterns: List[str] = field(default_factory=lambda: [
        "pass.sdu.edu.cn/cas/login",
        "/cas/login",
        "/login",
    ])

    # 登录表单元素（SDU CAS）
    username_selector: str = "#un"
    password_selector: str = "#pd"
    login_button_selector: str = "#index_login_btn"

    # 登录成功判断
    login_success_timeout: int = 60  # 秒（自动登录后等待跳转的超时）
    manual_login_timeout: int = 300  # 秒（手动登录的超时，5分钟）
    login_success_url_must_not_contain: str = "/cas/login"

    # 手动登录轮询间隔
    manual_poll_interval: float = 2.0

    @classmethod
    def from_env(cls) -> AuthConfig:
        return cls(
            username=os.getenv("SDU_USERNAME", ""),
            password=os.getenv("SDU_PASSWORD", ""),
            cookie_file=os.getenv("COOKIE_FILE", "data/cookies.json"),
            auth_mode=os.getenv("AUTH_MODE", "manual"),
            manual_login_timeout=int(os.getenv("MANUAL_LOGIN_TIMEOUT", "300")),
        )

    @property
    def has_credentials(self) -> bool:
        return bool(self.username and self.password)

    @property
    def has_cookie_file(self) -> bool:
        return Path(self.cookie_file).is_file()

    def mask_password(self) -> str:
        """脱敏显示"""
        if not self.password:
            return "(空)"
        return self.password[0] + "***" + self.password[-1]


# ════════════════════════════════════════════════════════════
#  Cookie 管理
# ════════════════════════════════════════════════════════════

async def _load_cookies(page, cookie_file: str) -> bool:
    """从 JSON 文件加载 Cookie 到浏览器上下文。"""
    path = Path(cookie_file)
    if not path.is_file():
        logger.debug("[Auth] Cookie 文件不存在: %s", cookie_file)
        return False

    try:
        cookies = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(cookies, list) or not cookies:
            logger.warning("[Auth] Cookie 文件为空或格式错误")
            return False

        context = page.context
        await context.add_cookies(cookies)

        logger.info("[Auth] ✅ 已加载 %d 个 Cookie", len(cookies))
        return True

    except Exception as e:
        logger.warning("[Auth] Cookie 加载失败: %s", e)
        return False


async def _save_cookies(page, cookie_file: str) -> bool:
    """将当前浏览器所有 Cookie 导出到 JSON 文件。"""
    try:
        context = page.context
        cookies = await context.cookies()

        if not cookies:
            logger.warning("[Auth] 没有 Cookie 可以保存")
            return False

        path = Path(cookie_file)
        path.parent.mkdir(parents=True, exist_ok=True)

        clean_cookies = []
        for c in cookies:
            cc = dict(c)
            cc.pop("sameSite", None)
            clean_cookies.append(cc)

        path.write_text(
            json.dumps(clean_cookies, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        logger.info("[Auth] ✅ 已保存 %d 个 Cookie → %s", len(clean_cookies), cookie_file)
        return True

    except Exception as e:
        logger.warning("[Auth] Cookie 保存失败: %s", e)
        return False


# ════════════════════════════════════════════════════════════
#  登录页面检测
# ════════════════════════════════════════════════════════════

def _is_login_page(url: str, patterns: List[str]) -> bool:
    """检查当前 URL 是否是登录页面"""
    url_lower = url.lower()
    return any(p.lower() in url_lower for p in patterns)


# ════════════════════════════════════════════════════════════
#  方案 B：手动登录（有头浏览器，用户操作）
# ════════════════════════════════════════════════════════════

async def _manual_login(
    page,
    config: AuthConfig,
    verbose: bool = True,
) -> bool:
    """
    有头浏览器手动登录。

    流程：
    1. 浏览器已在登录页面（有头模式下用户可以看到）
    2. 提示用户在浏览器中手动完成登录
    3. 轮询检测 URL 变化（离开登录页 = 登录成功）
    4. 用户也可以按 Enter 手动确认

    Docker 中通过 noVNC (http://localhost:6080) 查看浏览器界面。

    返回是否登录成功。
    """
    if verbose:
        print(f"\n  {_B}{_C}{'═' * 50}{_0}")
        print(f"  {_B}🖥️  手动登录模式{_0}")
        print(f"  {_C}{'═' * 50}{_0}")
        print(f"  {_Y}请在浏览器中手动完成登录操作{_0}")
        print(f"  {_D}当前登录页: {page.url[:80]}{_0}")
        print()
        print(f"  {_C}📺 Docker 用户请打开浏览器访问:{_0}")
        print(f"  {_B}   http://localhost:6080{_0}")
        print(f"  {_D}   （noVNC 网页可看到浏览器界面）{_0}")
        print()
        print(f"  {_Y}登录完成后，系统会自动检测到。{_0}")
        print(f"  {_D}或者在此终端按 Enter 手动确认。{_0}")
        print(f"  {_D}超时: {config.manual_login_timeout} 秒{_0}")
        print(f"  {_C}{'─' * 50}{_0}")
        sys.stdout.flush()

    # 使用非阻塞 stdin 检测 Enter 键
    enter_pressed = asyncio.Event()

    def _stdin_reader():
        """后台线程读取 stdin"""
        try:
            sys.stdin.readline()
            enter_pressed.set()
        except Exception:
            pass

    import threading
    stdin_thread = threading.Thread(target=_stdin_reader, daemon=True)
    stdin_thread.start()

    start = time.time()
    check_count = 0

    while time.time() - start < config.manual_login_timeout:
        # 检查是否按了 Enter
        if enter_pressed.is_set():
            if verbose:
                print(f"  {_D}   ↵ 检测到 Enter 确认{_0}")
            # 按 Enter 后再检查一次 URL
            current_url = page.url
            if not _is_login_page(current_url, config.login_url_patterns):
                if verbose:
                    print(f"  {_G}🔓 手动登录成功！ → {current_url[:80]}{_0}")
                return True
            else:
                if verbose:
                    print(f"  {_Y}⚠️  仍在登录页面，请先在浏览器中完成登录{_0}")
                    print(f"  {_D}   当前 URL: {current_url[:80]}{_0}")
                # 重置，继续等待
                enter_pressed.clear()
                stdin_thread = threading.Thread(target=_stdin_reader, daemon=True)
                stdin_thread.start()

        # 自动检测 URL 变化
        try:
            current_url = page.url
            if not _is_login_page(current_url, config.login_url_patterns):
                if verbose:
                    print(f"\n  {_G}🔓 检测到登录成功！ → {current_url[:80]}{_0}")
                return True
        except Exception:
            pass

        check_count += 1
        if verbose and check_count % 10 == 0:
            elapsed = int(time.time() - start)
            remaining = config.manual_login_timeout - elapsed
            print(f"  {_D}   ⏳ 等待登录中... ({elapsed}s / 剩余 {remaining}s){_0}")
            sys.stdout.flush()

        await asyncio.sleep(config.manual_poll_interval)

    if verbose:
        print(f"  {_R}⏰ 手动登录超时 ({config.manual_login_timeout}s){_0}")
    return False


# ════════════════════════════════════════════════════════════
#  方案 C：自动登录（不经过 LLM）
# ════════════════════════════════════════════════════════════

async def _direct_login(
    page,
    config: AuthConfig,
    verbose: bool = True,
) -> bool:
    """
    用 Playwright 直接操作登录表单。
    密码只在本进程内存中流转，绝不发送到外部 API。
    """
    if not config.has_credentials:
        logger.warning("[Auth] 未配置用户名/密码，跳过自动登录")
        return False

    if verbose:
        print(f"  {_C}🔐 正在执行自动登录（不经过 LLM）...{_0}")
        print(f"  {_D}   用户: {config.username}{_0}")
        print(f"  {_D}   密码: {config.mask_password()}{_0}")

    try:
        await page.wait_for_selector(config.username_selector, timeout=30000)

        await page.fill(config.username_selector, "")
        await page.fill(config.username_selector, config.username)
        await asyncio.sleep(0.3)

        if verbose:
            print(f"  {_D}   ✓ 用户名已填写{_0}")

        await page.fill(config.password_selector, "")
        await page.fill(config.password_selector, config.password)
        await asyncio.sleep(0.3)

        if verbose:
            print(f"  {_D}   ✓ 密码已填写{_0}")

        await page.click(config.login_button_selector)

        if verbose:
            print(f"  {_D}   ✓ 已点击登录按钮，等待跳转...{_0}")

        start = time.time()
        while time.time() - start < config.login_success_timeout:
            await asyncio.sleep(0.5)
            current_url = page.url
            if config.login_success_url_must_not_contain not in current_url:
                if verbose:
                    print(f"  {_G}🔓 自动登录成功！ → {current_url[:80]}{_0}")
                return True

        if verbose:
            print(f"  {_R}⚠️  自动登录超时，可能密码错误或网络问题{_0}")
            try:
                error_el = await page.query_selector(".error, .msg-error, #msg, .alert-danger")
                if error_el:
                    error_text = await error_el.text_content()
                    print(f"  {_R}   页面提示: {error_text.strip()[:100]}{_0}")
            except Exception:
                pass

        return False

    except Exception as e:
        logger.error("[Auth] 自动登录执行失败: %s", e)
        if verbose:
            print(f"  {_R}❌ 自动登录失败: {e}{_0}")
        return False


# ════════════════════════════════════════════════════════════
#  主入口：BrowserAuth
# ════════════════════════════════════════════════════════════

class BrowserAuth:
    """
    浏览器认证管理器。

    认证模式（auth_mode）：

    "manual" — 手动优先（推荐 Docker 环境使用）：
        1. Cookie 注入 → 检查是否有效
        2. 有头浏览器 → 用户手动登录 → 保存 Cookie
        3. 自动登录（fallback）

    "auto" — 自动优先：
        1. Cookie 注入
        2. 自动登录（Playwright fill + click）
        3. 手动登录（fallback）

    "cookie_only" — 仅 Cookie：
        1. Cookie 注入 → 成功或失败

    用法：
        auth = BrowserAuth.from_env()
        logged_in = await auth.ensure_logged_in(page, target_url)
    """

    def __init__(self, config: AuthConfig | None = None, verbose: bool = True):
        self.config = config or AuthConfig.from_env()
        self.verbose = verbose

    @classmethod
    def from_env(cls, verbose: bool = True) -> BrowserAuth:
        return cls(config=AuthConfig.from_env(), verbose=verbose)

    async def ensure_logged_in(self, page, target_url: str) -> bool:
        """
        确保已登录。如果当前在登录页面，自动执行认证流程。

        参数：
            page: Playwright Page 对象（已打开 target_url）
            target_url: 目标 URL

        返回：True = 已登录（或无需登录），False = 认证失败
        """
        current_url = page.url

        # 检查是否在登录页面
        if not _is_login_page(current_url, self.config.login_url_patterns):
            if self.verbose:
                print(f"  {_G}🔓 无需登录（当前不在登录页）{_0}")
            return True

        if self.verbose:
            print(f"\n  {_Y}🔒 检测到登录页面: {current_url[:60]}...{_0}")
            print(f"  {_D}   认证模式: {self.config.auth_mode}{_0}")

        # ── 第一步：始终先尝试 Cookie 注入 ──
        if self.config.has_cookie_file:
            if self.verbose:
                print(f"  {_C}🍪 尝试 Cookie 注入 ({self.config.cookie_file})...{_0}")

            loaded = await _load_cookies(page, self.config.cookie_file)
            if loaded:
                await page.goto(target_url, wait_until="domcontentloaded", timeout=60000)
                # 等待页面完全加载（SDU 系统跳转较慢）
                try:
                    await page.wait_for_load_state("networkidle", timeout=60000)
                except Exception:
                    pass
                await asyncio.sleep(3)

                if not _is_login_page(page.url, self.config.login_url_patterns):
                    if self.verbose:
                        print(f"  {_G}🔓 Cookie 登录成功！ → {page.url[:80]}{_0}")
                    return True
                else:
                    if self.verbose:
                        print(f"  {_Y}⚠️  Cookie 已过期，进入下一步认证{_0}")
        else:
            if self.verbose:
                print(f"  {_D}   Cookie 文件不存在，跳过{_0}")

        # ── 第二步：根据模式选择认证策略 ──
        if self.config.auth_mode == "cookie_only":
            if self.verbose:
                print(f"  {_R}❌ Cookie 模式：Cookie 无效，认证失败{_0}")
            return False

        if self.config.auth_mode == "manual":
            # 手动优先：先手动，再自动
            return await self._try_manual_then_auto(page, target_url)
        else:
            # auto 模式：先自动，再手动
            return await self._try_auto_then_manual(page, target_url)

    async def _try_manual_then_auto(self, page, target_url: str) -> bool:
        """手动登录优先，失败后降级自动登录"""
        # 确保在登录页
        if not _is_login_page(page.url, self.config.login_url_patterns):
            await page.goto(target_url, wait_until="domcontentloaded", timeout=60000)
            await asyncio.sleep(1)

        # 手动登录
        success = await _manual_login(page, self.config, verbose=self.verbose)
        if success:
            await _save_cookies(page, self.config.cookie_file)
            return True

        # 降级自动登录
        if self.config.has_credentials:
            if self.verbose:
                print(f"\n  {_Y}⚠️  手动登录超时，尝试自动登录...{_0}")
            if not _is_login_page(page.url, self.config.login_url_patterns):
                await page.goto(target_url, wait_until="domcontentloaded", timeout=60000)
                await asyncio.sleep(1)
            success = await _direct_login(page, self.config, verbose=self.verbose)
            if success:
                await _save_cookies(page, self.config.cookie_file)
                return True

        if self.verbose:
            print(f"  {_R}❌ 所有认证方式均失败{_0}")
        return False

    async def _try_auto_then_manual(self, page, target_url: str) -> bool:
        """自动登录优先，失败后降级手动登录"""
        if self.config.has_credentials:
            if not _is_login_page(page.url, self.config.login_url_patterns):
                await page.goto(target_url, wait_until="domcontentloaded", timeout=60000)
                await asyncio.sleep(1)
            success = await _direct_login(page, self.config, verbose=self.verbose)
            if success:
                await _save_cookies(page, self.config.cookie_file)
                return True
            if self.verbose:
                print(f"\n  {_Y}⚠️  自动登录失败，降级为手动登录{_0}")

        # 降级手动登录
        if not _is_login_page(page.url, self.config.login_url_patterns):
            await page.goto(target_url, wait_until="domcontentloaded", timeout=60000)
            await asyncio.sleep(1)
        success = await _manual_login(page, self.config, verbose=self.verbose)
        if success:
            await _save_cookies(page, self.config.cookie_file)
            return True

        if self.verbose:
            print(f"  {_R}❌ 所有认证方式均失败{_0}")
        return False


# ════════════════════════════════════════════════════════════
#  独立 Cookie 采集工具（可在 Docker 外或内运行）
# ════════════════════════════════════════════════════════════

async def harvest_cookies(
    login_url: str,
    cookie_file: str = "data/cookies.json",
    headless: bool = False,
    timeout: int = 300,
) -> bool:
    """
    独立的 Cookie 采集流程。

    打开有头浏览器 → 导航到登录页 → 等待用户手动登录 → 保存 Cookie。

    可以在 Docker 外单独运行，采集完的 Cookie 挂载到容器内使用。
    也可以在 Docker 内配合 noVNC 使用。

    用法：
        python browser_auth.py harvest https://pass.sdu.edu.cn/cas/login
        python browser_auth.py harvest https://pass.sdu.edu.cn/cas/login --cookie-file data/cookies.json
    """
    from playwright.async_api import async_playwright

    print(f"\n{_B}{_C}{'═' * 55}{_0}")
    print(f"  {_B}🍪 Cookie 采集工具{_0}")
    print(f"{_C}{'═' * 55}{_0}")
    print(f"  {_D}登录页:  {login_url}{_0}")
    print(f"  {_D}Cookie:  {cookie_file}{_0}")
    print(f"  {_D}有头模式: {not headless}{_0}")
    print(f"  {_D}超时:    {timeout}s{_0}")
    print(f"{_C}{'─' * 55}{_0}\n")

    pw = await async_playwright().start()

    launch_args = ["--no-sandbox", "--disable-dev-shm-usage"]

    browser = await pw.chromium.launch(
        headless=headless,
        args=launch_args,
    )
    context = await browser.new_context(
        viewport={"width": 1280, "height": 900},
        locale="zh-CN",
    )
    page = await context.new_page()

    try:
        print(f"  {_D}⏳ 正在打开登录页...{_0}")
        await page.goto(login_url, wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(1)

        print(f"  {_G}✅ 登录页已打开{_0}")
        print()
        print(f"  {_B}{_Y}请在浏览器中手动完成登录！{_0}")
        if headless:
            print(f"  {_C}📺 Docker 用户请访问 http://localhost:6080 查看浏览器{_0}")
        else:
            print(f"  {_D}浏览器窗口已打开，请在其中操作{_0}")
        print()
        print(f"  {_D}登录成功后系统会自动检测，或按 Enter 手动确认{_0}")
        print(f"{_C}{'─' * 55}{_0}")
        sys.stdout.flush()

        config = AuthConfig(
            cookie_file=cookie_file,
            manual_login_timeout=timeout,
        )

        success = await _manual_login(page, config, verbose=True)

        if success:
            saved = await _save_cookies(page, cookie_file)
            if saved:
                print(f"\n  {_G}{_B}✅ Cookie 已保存到 {cookie_file}{_0}")
                print(f"  {_D}下次运行时将自动使用 Cookie 跳过登录{_0}")

                # 显示保存的 Cookie 概要
                cookies = await context.cookies()
                print(f"\n  {_D}已保存 {len(cookies)} 个 Cookie:{_0}")
                for c in cookies[:10]:
                    print(f"  {_D}  • {c['name']}: {c['value'][:20]}... ({c['domain']}){_0}")
                if len(cookies) > 10:
                    print(f"  {_D}  ... 还有 {len(cookies) - 10} 个{_0}")
            else:
                print(f"\n  {_R}❌ Cookie 保存失败{_0}")
                success = False
        else:
            print(f"\n  {_R}❌ 登录未完成{_0}")

        return success

    except Exception as e:
        print(f"\n  {_R}❌ 异常: {e}{_0}")
        logger.error("Cookie 采集异常: %s", e, exc_info=True)
        return False

    finally:
        await browser.close()
        await pw.stop()
        print(f"\n{_C}{'═' * 55}{_0}\n")


# ════════════════════════════════════════════════════════════
#  CLI 入口
# ════════════════════════════════════════════════════════════

def _cli():
    """
    命令行入口：

    # 采集 Cookie（有头浏览器，手动登录）
    python browser_auth.py harvest https://pass.sdu.edu.cn/cas/login

    # 采集 Cookie（headless，配合 noVNC）
    python browser_auth.py harvest https://pass.sdu.edu.cn/cas/login --headless

    # 指定 Cookie 文件路径
    python browser_auth.py harvest URL --cookie-file data/cookies.json

    # 验证已有 Cookie 是否有效
    python browser_auth.py check https://pass.sdu.edu.cn/cas/login
    """
    import argparse

    parser = argparse.ArgumentParser(
        description="浏览器认证 Cookie 管理工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 本地运行，打开浏览器手动登录并保存 Cookie
  python browser_auth.py harvest https://pass.sdu.edu.cn/cas/login

  # Docker 内运行（配合 noVNC 查看浏览器）
  python browser_auth.py harvest https://pass.sdu.edu.cn/cas/login --headless

  # 验证 Cookie 是否还有效
  python browser_auth.py check https://pass.sdu.edu.cn/cas/login
        """,
    )
    sub = parser.add_subparsers(dest="command")

    # harvest 子命令
    p_harvest = sub.add_parser("harvest", help="打开浏览器手动登录，采集 Cookie")
    p_harvest.add_argument("url", help="登录页面 URL")
    p_harvest.add_argument("--cookie-file", default="data/cookies.json", help="Cookie 保存路径")
    p_harvest.add_argument("--headless", action="store_true", help="无头模式（配合 noVNC）")
    p_harvest.add_argument("--timeout", type=int, default=300, help="超时秒数")

    # check 子命令
    p_check = sub.add_parser("check", help="验证已有 Cookie 是否有效")
    p_check.add_argument("url", help="目标 URL")
    p_check.add_argument("--cookie-file", default="data/cookies.json", help="Cookie 文件路径")

    args = parser.parse_args()

    if args.command == "harvest":
        success = asyncio.run(harvest_cookies(
            login_url=args.url,
            cookie_file=args.cookie_file,
            headless=args.headless,
            timeout=args.timeout,
        ))
        sys.exit(0 if success else 1)

    elif args.command == "check":
        success = asyncio.run(_check_cookies(args.url, args.cookie_file))
        sys.exit(0 if success else 1)

    else:
        parser.print_help()
        sys.exit(1)


async def _check_cookies(url: str, cookie_file: str) -> bool:
    """验证 Cookie 是否有效"""
    from playwright.async_api import async_playwright

    path = Path(cookie_file)
    if not path.is_file():
        print(f"  {_R}❌ Cookie 文件不存在: {cookie_file}{_0}")
        return False

    cookies = json.loads(path.read_text(encoding="utf-8"))
    print(f"  {_D}已加载 {len(cookies)} 个 Cookie{_0}")

    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=True, args=["--no-sandbox"])
    context = await browser.new_context()
    page = await context.new_page()

    try:
        await context.add_cookies(cookies)
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(2)

        config = AuthConfig(cookie_file=cookie_file)
        if _is_login_page(page.url, config.login_url_patterns):
            print(f"  {_R}❌ Cookie 已失效（仍在登录页）{_0}")
            print(f"  {_D}   当前 URL: {page.url}{_0}")
            return False
        else:
            print(f"  {_G}✅ Cookie 有效！已跳过登录{_0}")
            print(f"  {_D}   当前 URL: {page.url}{_0}")
            return True

    finally:
        await browser.close()
        await pw.stop()


if __name__ == "__main__":
    _cli()
