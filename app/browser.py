import logging
import random

from app.tools import _USER_AGENTS

logger = logging.getLogger(__name__)

try:
    from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    Browser = BrowserContext = Page = Playwright = async_playwright = None


class BrowserManager:
    def __init__(
        self,
        headless: bool = True,
        nav_timeout: int = 60000,
        action_timeout: int = 10000,
    ):
        self._headless = headless
        self._nav_timeout = nav_timeout
        self._action_timeout = action_timeout
        self._playwright: "Playwright | None" = None
        self._browser: "Browser | None" = None
        self._context: "BrowserContext | None" = None
        self._page: "Page | None" = None

    async def start(self) -> None:
        if not PLAYWRIGHT_AVAILABLE:
            logger.warning("Playwright is not installed — browser tools disabled")
            return
        try:
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(headless=self._headless)
            await self._reset_context()
            logger.info("Browser started (headless=%s)", self._headless)
        except Exception:
            logger.warning("Failed to start browser — browser tools disabled", exc_info=True)
            self._playwright = None
            self._browser = None

    async def stop(self) -> None:
        if self._context:
            try:
                await self._context.close()
            except Exception:
                pass
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
        logger.info("Browser stopped")

    @property
    def is_alive(self) -> bool:
        return self._browser is not None and self._browser.is_connected()

    @property
    def nav_timeout(self) -> int:
        return self._nav_timeout

    @property
    def action_timeout(self) -> int:
        return self._action_timeout

    async def get_page(self) -> "Page":
        if self._page and not self._page.is_closed():
            return self._page
        await self._reset_context()
        assert self._page is not None
        return self._page

    async def reset_page(self) -> "Page":
        logger.warning("Force-resetting browser context and page")
        await self._reset_context()
        assert self._page is not None
        return self._page

    async def _reset_context(self) -> None:
        if self._context:
            try:
                await self._context.close()
            except Exception:
                pass
        if self._browser is None:
            raise RuntimeError("Browser not started")
        self._context = await self._browser.new_context(user_agent=random.choice(_USER_AGENTS))
        self._page = await self._context.new_page()

    def get_browser_tools(self) -> list:
        if not self.is_alive:
            return []
        from app.browser_tools import create_browser_tools

        return create_browser_tools(self)
