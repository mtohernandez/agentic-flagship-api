import logging

from langchain_community.agent_toolkits import PlayWrightBrowserToolkit
from playwright.async_api import Browser, Playwright, async_playwright

logger = logging.getLogger(__name__)


class BrowserManager:
    def __init__(self, headless: bool = True):
        self._headless = headless
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None

    async def start(self) -> None:
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=self._headless)
        logger.info("Browser started (headless=%s)", self._headless)

    async def stop(self) -> None:
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
        logger.info("Browser stopped")

    @property
    def is_alive(self) -> bool:
        return self._browser is not None and self._browser.is_connected()

    def get_playwright_tools(self) -> list:
        if self._browser is None:
            raise RuntimeError("Browser not started")
        toolkit = PlayWrightBrowserToolkit.from_browser(async_browser=self._browser)
        return toolkit.get_tools()
