import json
import logging

import httpx
from bs4 import BeautifulSoup
from langchain_core.tools import tool

from app.security import validate_url

logger = logging.getLogger(__name__)

http_client = httpx.AsyncClient(
    follow_redirects=True,
    timeout=30.0,
    headers={"User-Agent": "Mozilla/5.0 (compatible; ScrapingAgent/1.0)"},
)

_FETCH_MAX_CHARS = 20_000
_PARSE_MAX_CHARS = 10_000
_PARSE_MAX_ELEMENTS = 50


@tool
async def fetch_page(url: str) -> str:
    """Fetch raw HTML via HTTP. Fast. Use first; fall back to browser tools for JS-heavy sites."""
    ssrf_error = validate_url(url)
    if ssrf_error:
        return ssrf_error
    try:
        resp = await http_client.get(url)
        resp.raise_for_status()
        text = resp.text
        if len(text) > _FETCH_MAX_CHARS:
            text = text[:_FETCH_MAX_CHARS] + f"\n\n[Truncated — showing first {_FETCH_MAX_CHARS} characters]"
        return text
    except httpx.HTTPStatusError as exc:
        return f"HTTP error {exc.response.status_code} fetching {url}: {exc.response.reason_phrase}"
    except httpx.RequestError as exc:
        return f"Request error fetching {url}: {exc}"


@tool
async def parse_html(html: str, selector: str, extract: str = "text") -> str:
    """Parse HTML with a CSS selector. extract: 'text', 'html', or 'attrs'."""
    soup = BeautifulSoup(html, "lxml")
    elements = soup.select(selector, limit=_PARSE_MAX_ELEMENTS)

    if not elements:
        return f"No elements found matching selector '{selector}'."

    results: list[str] = []
    for el in elements:
        if extract == "html":
            results.append(str(el))
        elif extract == "attrs":
            results.append(json.dumps(dict(el.attrs)))
        else:
            results.append(el.get_text(separator=" ", strip=True))

    output = "\n---\n".join(results)
    if len(output) > _PARSE_MAX_CHARS:
        output = output[:_PARSE_MAX_CHARS] + f"\n\n[Truncated — showing first {_PARSE_MAX_CHARS} characters]"
    return output


@tool
async def extract_table_data(html: str, table_index: int = 0) -> str:
    """Extract a table from HTML into markdown. table_index is 0-based."""
    soup = BeautifulSoup(html, "lxml")
    tables = soup.find_all("table")

    if not tables:
        return "No tables found in the HTML."
    if table_index < 0 or table_index >= len(tables):
        return f"table_index {table_index} is out of range. Found {len(tables)} table(s) (indices 0–{len(tables) - 1})."

    table = tables[table_index]
    rows = table.find_all("tr")
    if not rows:
        return "Table has no rows."

    md_rows: list[str] = []
    for i, row in enumerate(rows):
        cells = row.find_all(["th", "td"])
        values = [c.get_text(separator=" ", strip=True) for c in cells]
        md_rows.append("| " + " | ".join(values) + " |")
        if i == 0:
            md_rows.append("| " + " | ".join("---" for _ in values) + " |")

    output = "\n".join(md_rows)
    if len(output) > _PARSE_MAX_CHARS:
        output = output[:_PARSE_MAX_CHARS] + f"\n\n[Truncated — showing first {_PARSE_MAX_CHARS} characters]"
    return output


@tool
async def extract_metadata(html: str) -> str:
    """Extract page metadata (title, description, OG tags, link/image/table counts) as JSON."""
    soup = BeautifulSoup(html, "lxml")

    title_tag = soup.find("title")
    meta_desc = soup.find("meta", attrs={"name": "description"})
    canonical = soup.find("link", attrs={"rel": "canonical"})

    og_tags: dict[str, str] = {}
    for tag in soup.find_all("meta", attrs={"property": True}):
        prop = tag.get("property", "")
        if prop.startswith("og:"):
            og_tags[prop] = tag.get("content", "")

    metadata = {
        "title": title_tag.get_text(strip=True) if title_tag else None,
        "description": meta_desc.get("content") if meta_desc else None,
        "canonical_url": canonical.get("href") if canonical else None,
        "og": og_tags or None,
        "counts": {
            "links": len(soup.find_all("a")),
            "images": len(soup.find_all("img")),
            "tables": len(soup.find_all("table")),
        },
    }
    return json.dumps(metadata, indent=2)
