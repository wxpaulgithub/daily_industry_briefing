"""
浏览器发现源通用基类（Playwright）

用于：
1. 统一浏览器启动与页面等待
2. 统一结果抽取流程
3. 让具体站点 Skill 只关注 selector / 数据清洗
"""
import logging
import os
from typing import Any

from services.skills.discover_base import DiscoverSearchSkillBase

logger = logging.getLogger(__name__)


class BrowserDiscoverSkillBase(DiscoverSearchSkillBase):
    """基于 Playwright 的发现源通用基类"""

    async def _extract_with_playwright(
        self,
        search_url: str,
        extract_script: str,
        timeout_ms: int = 25000,
        settle_ms: int = 1500,
        scroll_rounds: int = 2,
    ) -> list[dict[str, Any]]:
        """
        打开页面后执行 JS 抽取脚本，返回结构化结果列表。
        如果 Playwright 不可用或页面失败，返回空列表。
        """
        try:
            from playwright.async_api import async_playwright
        except Exception as e:
            logger.warning(f"[{self.name}] Playwright 不可用，跳过浏览器抓取: {e}")
            return []

        try:
            async with async_playwright() as p:
                launch_args = [
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled",
                ]
                browser = None
                # 优先尝试系统浏览器，避免必须下载 Playwright Chromium
                preferred_channels = [
                    os.getenv("PLAYWRIGHT_BROWSER_CHANNEL", "").strip(),
                    "msedge",
                    "chrome",
                ]
                for channel in [c for c in preferred_channels if c]:
                    try:
                        browser = await p.chromium.launch(
                            headless=True,
                            channel=channel,
                            args=launch_args,
                        )
                        logger.info(f"[{self.name}] 使用系统浏览器通道: {channel}")
                        break
                    except Exception:
                        browser = None
                if browser is None:
                    browser = await p.chromium.launch(
                        headless=True,
                        args=launch_args,
                    )
                context = await browser.new_context(
                    viewport={"width": 1440, "height": 900},
                    locale="zh-CN",
                )
                page = await context.new_page()
                await page.goto(search_url, wait_until="domcontentloaded", timeout=timeout_ms)
                await page.wait_for_timeout(settle_ms)

                # 轻量滚动，触发懒加载
                for _ in range(max(scroll_rounds, 0)):
                    await page.mouse.wheel(0, 1800)
                    await page.wait_for_timeout(500)

                data = await page.evaluate(extract_script)
                await context.close()
                await browser.close()
                if isinstance(data, list):
                    return [d for d in data if isinstance(d, dict)]
                return []
        except Exception as e:
            logger.warning(
                f"[{self.name}] 浏览器抓取失败 [{search_url}]: {e}. "
                "若为本地环境，请确认已执行: python -m playwright install chromium"
            )
            return []
