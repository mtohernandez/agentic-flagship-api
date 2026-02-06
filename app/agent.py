import logging

from langchain_groq import ChatGroq
from langgraph.prebuilt import create_react_agent

from app.browser import BrowserManager
from app.config import Settings
from app.tools import extract_metadata, extract_table_data, fetch_page, parse_html

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are a helpful web research assistant. You have access to both lightweight HTTP tools and a full browser.

Tool selection strategy:
- For static pages, ALWAYS prefer fetch_page over browser navigation — it's much faster.
- Use parse_html and extract_table_data to extract structured data from HTML returned by fetch_page.
- Use extract_metadata to quickly understand what a page is about before deeper scraping.
- Only use browser tools (navigate_browser, click_element, etc.) when:
  - The page requires JavaScript rendering
  - You need to interact with the page (click, fill forms)
  - fetch_page returns empty/broken content (JS-rendered SPA)
- Always summarize large outputs before presenting to the user.
"""


def build_agent(settings: Settings, browser_manager: BrowserManager):
    llm = ChatGroq(
        api_key=settings.groq_api_key,
        model=settings.groq_model,
        temperature=settings.groq_temperature,
    )

    playwright_tools = browser_manager.get_playwright_tools()
    custom_tools = [fetch_page, parse_html, extract_table_data, extract_metadata]
    all_tools = custom_tools + playwright_tools

    logger.info(
        "Building agent with %d tools: %s",
        len(all_tools),
        ", ".join(t.name for t in all_tools),
    )

    agent = create_react_agent(
        model=llm,
        tools=all_tools,
        prompt=SYSTEM_PROMPT,
    )
    return agent
