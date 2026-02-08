import logging

from groq import APIError as GroqAPIError
from langchain.agents import create_agent
from langchain.agents.middleware import ModelRetryMiddleware
from langchain_groq import ChatGroq

from app.browser import BrowserManager
from app.config import Settings
from app.tools import crawl, page_info, scrape, scrape_json, scrape_table

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT_BASE = """\
You are a fast web scraping assistant.

Rules:
- Use page_info(url) first to understand a page before scraping.
- Use scrape(url, selector) for most extraction. Pick a precise CSS selector.
- Use scrape_table(url) for tabular data.
- Use scrape_json(url) to extract structured product/article data (JSON-LD, microdata).
- Use crawl(url, max_pages) to follow links and scrape multiple pages.
- Give a concise final answer. Do not repeat raw scraped data verbatim."""

_BROWSER_ADDENDUM = """
- Only use browser tools (navigate_browser, etc.) when scrape() returns empty content (JS-rendered SPA).
- If a browser tool reports a context error, just navigate again."""


def build_agent(settings: Settings, browser_manager: BrowserManager):
    llm = ChatGroq(
        api_key=settings.groq_api_key,
        model=settings.groq_model,
        temperature=settings.groq_temperature,
        max_retries=1,
        max_tokens=settings.groq_max_tokens,
    )

    retry_middleware = ModelRetryMiddleware(
        max_retries=settings.agent_max_retries,
        retry_on=(GroqAPIError,),
        on_failure="continue",
        initial_delay=1.0,
        backoff_factor=2.0,
    )

    browser_tools = browser_manager.get_browser_tools()
    custom_tools = [scrape, scrape_table, page_info, scrape_json, crawl]
    all_tools = custom_tools + browser_tools

    system_prompt = _SYSTEM_PROMPT_BASE
    if browser_tools:
        system_prompt += _BROWSER_ADDENDUM

    logger.info(
        "Building agent with %d tools: %s",
        len(all_tools),
        ", ".join(t.name for t in all_tools),
    )

    agent = create_agent(
        model=llm,
        tools=all_tools,
        system_prompt=system_prompt,
        middleware=[retry_middleware],
    )
    return agent
