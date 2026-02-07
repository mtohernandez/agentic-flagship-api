import logging

from langchain_groq import ChatGroq
from langchain.agents import create_agent

from app.browser import BrowserManager
from app.config import Settings
from app.tools import extract_metadata, extract_table_data, fetch_page, parse_html

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are a web research assistant with HTTP tools and a full browser.

Strategy:
- Prefer fetch_page for static pages (much faster). Fall back to browser tools only for JS-heavy sites or interaction.
- Use parse_html / extract_table_data / extract_metadata on HTML from fetch_page.
- If a browser tool reports a context error, just navigate again.
- Summarize large outputs before presenting.
"""


def build_agent(settings: Settings, browser_manager: BrowserManager):
    llm = ChatGroq(
        api_key=settings.groq_api_key,
        model=settings.groq_model,
        temperature=settings.groq_temperature,
    )

    browser_tools = browser_manager.get_browser_tools()
    custom_tools = [fetch_page, parse_html, extract_table_data, extract_metadata]
    all_tools = custom_tools + browser_tools

    logger.info(
        "Building agent with %d tools: %s",
        len(all_tools),
        ", ".join(t.name for t in all_tools),
    )

    agent = create_agent(
        model=llm,
        tools=all_tools,
        system_prompt=SYSTEM_PROMPT,
    )
    return agent
