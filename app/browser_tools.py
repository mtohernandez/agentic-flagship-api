from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from langchain_core.tools import tool
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

if TYPE_CHECKING:
    from app.browser import BrowserManager

logger = logging.getLogger(__name__)

_MAX_CHARS = 20_000

_CONTEXT_DESTROYED = "Execution context was destroyed"


def create_browser_tools(manager: BrowserManager) -> list:
    @tool
    async def navigate_browser(url: str) -> str:
        """Navigate browser to a URL. Use for JS-heavy pages."""
        try:
            page = await manager.get_page()
            resp = await page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=manager.nav_timeout,
            )
            status = resp.status if resp else "unknown"
            return f"Navigated to {url} (status {status})"
        except PlaywrightTimeoutError:
            return f"Timed out navigating to {url} after {manager.nav_timeout}ms. The page may be partially loaded — try extract_text to see what's available."
        except PlaywrightError as exc:
            if _CONTEXT_DESTROYED in str(exc):
                await manager.reset_page()
                return "Browser context was destroyed and has been reset. Please navigate to the URL again."
            return f"Browser error navigating to {url}: {exc}"
        except Exception as exc:
            return f"Unexpected error navigating to {url}: {exc}"

    @tool
    async def click_element(selector: str) -> str:
        """Click a visible element matching a CSS selector."""
        try:
            page = await manager.get_page()
            await page.click(
                f"{selector} >> visible=true",
                timeout=manager.action_timeout,
            )
            return f"Clicked element matching '{selector}'"
        except PlaywrightTimeoutError:
            return f"Timed out clicking '{selector}' after {manager.action_timeout}ms. The element may not be visible or may not exist."
        except PlaywrightError as exc:
            if _CONTEXT_DESTROYED in str(exc):
                await manager.reset_page()
                return "Browser context was destroyed and has been reset. Please navigate to the URL again."
            return f"Browser error clicking '{selector}': {exc}"
        except Exception as exc:
            return f"Unexpected error clicking '{selector}': {exc}"

    @tool
    async def get_elements(
        selector: str, attributes: list[str] = ["innerText"]
    ) -> str:
        """Get elements matching a CSS selector. Returns JSON list of attributes."""
        try:
            page = await manager.get_page()
            elements = await page.query_selector_all(selector)
            results = []
            for el in elements:
                item: dict[str, str | None] = {}
                for attr in attributes:
                    if attr == "innerText":
                        item[attr] = await el.inner_text()
                    else:
                        item[attr] = await el.get_attribute(attr)
                results.append(item)
            output = json.dumps(results, ensure_ascii=False)
            if len(output) > _MAX_CHARS:
                output = output[:_MAX_CHARS] + "\n\n[Truncated]"
            return output
        except PlaywrightTimeoutError:
            return f"Timed out querying elements for '{selector}'."
        except PlaywrightError as exc:
            if _CONTEXT_DESTROYED in str(exc):
                await manager.reset_page()
                return "Browser context was destroyed and has been reset. Please navigate to the URL again."
            return f"Browser error querying '{selector}': {exc}"
        except Exception as exc:
            return f"Unexpected error querying '{selector}': {exc}"

    @tool
    async def extract_text() -> str:
        """Extract all visible text from the current page."""
        try:
            page = await manager.get_page()
            html = await page.content()
            soup = BeautifulSoup(html, "lxml")
            text = " ".join(soup.stripped_strings)
            if len(text) > _MAX_CHARS:
                text = text[:_MAX_CHARS] + "\n\n[Truncated]"
            return text
        except PlaywrightError as exc:
            if _CONTEXT_DESTROYED in str(exc):
                await manager.reset_page()
                return "Browser context was destroyed and has been reset. Please navigate to the URL again."
            return f"Browser error extracting text: {exc}"
        except Exception as exc:
            return f"Unexpected error extracting text: {exc}"

    @tool
    async def extract_hyperlinks(absolute_urls: bool = False) -> str:
        """Extract all hyperlinks from the current page as JSON."""
        try:
            page = await manager.get_page()
            html = await page.content()
            base_url = page.url
            soup = BeautifulSoup(html, "lxml")
            links = []
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if absolute_urls:
                    href = urljoin(base_url, href)
                text = a.get_text(separator=" ", strip=True)
                links.append({"text": text, "href": href})
            output = json.dumps(links, ensure_ascii=False)
            if len(output) > _MAX_CHARS:
                output = output[:_MAX_CHARS] + "\n\n[Truncated]"
            return output
        except PlaywrightError as exc:
            if _CONTEXT_DESTROYED in str(exc):
                await manager.reset_page()
                return "Browser context was destroyed and has been reset. Please navigate to the URL again."
            return f"Browser error extracting hyperlinks: {exc}"
        except Exception as exc:
            return f"Unexpected error extracting hyperlinks: {exc}"

    @tool
    async def current_webpage() -> str:
        """Return the current page URL."""
        try:
            page = await manager.get_page()
            return page.url
        except PlaywrightError as exc:
            if _CONTEXT_DESTROYED in str(exc):
                await manager.reset_page()
                return "Browser context was destroyed and has been reset. Please navigate to the URL again."
            return f"Browser error getting current URL: {exc}"
        except Exception as exc:
            return f"Unexpected error getting current URL: {exc}"

    @tool
    async def previous_webpage() -> str:
        """Go back to the previous page."""
        try:
            page = await manager.get_page()
            resp = await page.go_back(
                wait_until="domcontentloaded",
                timeout=manager.nav_timeout,
            )
            if resp is None:
                return "No previous page in browser history."
            return f"Navigated back to {page.url} (status {resp.status})"
        except PlaywrightTimeoutError:
            return f"Timed out navigating back after {manager.nav_timeout}ms."
        except PlaywrightError as exc:
            if _CONTEXT_DESTROYED in str(exc):
                await manager.reset_page()
                return "Browser context was destroyed and has been reset. Please navigate to the URL again."
            return f"Browser error navigating back: {exc}"
        except Exception as exc:
            return f"Unexpected error navigating back: {exc}"

    return [
        navigate_browser,
        click_element,
        get_elements,
        extract_text,
        extract_hyperlinks,
        current_webpage,
        previous_webpage,
    ]
