import json
import logging
import random
import time
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from langchain_core.tools import tool

from app.security import validate_url

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Anti-detection: realistic browser User-Agents + standard headers
# ---------------------------------------------------------------------------

_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

_BASE_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive",
}

http_client = httpx.AsyncClient(
    follow_redirects=True,
    timeout=30.0,
    headers=_BASE_HEADERS,
)

_MAX_CHARS = 10_000
_MAX_ELEMENTS = 50
_CACHE_TTL = 300  # 5 minutes


def _random_headers() -> dict[str, str]:
    return {"User-Agent": random.choice(_USER_AGENTS)}


# ---------------------------------------------------------------------------
# In-memory URL response cache
# ---------------------------------------------------------------------------

_cache: dict[str, tuple[str, float]] = {}


def _cache_get(url: str) -> str | None:
    entry = _cache.get(url)
    if entry is None:
        return None
    html, ts = entry
    if time.monotonic() - ts > _CACHE_TTL:
        del _cache[url]
        return None
    return html


def _cache_set(url: str, html: str) -> None:
    _cache[url] = (html, time.monotonic())


def cache_clear() -> None:
    """Clear the URL response cache."""
    _cache.clear()


# ---------------------------------------------------------------------------
# Core fetch (with SSRF protection, caching, anti-detection)
# ---------------------------------------------------------------------------


async def _fetch(url: str) -> tuple[str | None, str | None]:
    """Fetch a URL and return (html, error). Validates SSRF first."""
    ssrf_error = validate_url(url)
    if ssrf_error:
        return None, ssrf_error

    cached = _cache_get(url)
    if cached is not None:
        return cached, None

    try:
        resp = await http_client.get(url, headers=_random_headers())
        resp.raise_for_status()
        html = resp.text
        _cache_set(url, html)
        return html, None
    except httpx.HTTPStatusError as exc:
        return None, f"HTTP error {exc.response.status_code} fetching {url}: {exc.response.reason_phrase}"
    except httpx.RequestError as exc:
        return None, f"Request error fetching {url}: {exc}"


def _truncate(text: str, limit: int = _MAX_CHARS) -> str:
    if len(text) > limit:
        return text[:limit] + f"\n\n[Truncated — showing first {limit} characters]"
    return text


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@tool
async def scrape(url: str, selector: str = "body", extract: str = "text") -> str:
    """Fetch a URL and extract content using a CSS selector.

    Args:
        url: The URL to fetch.
        selector: CSS selector to target elements (default: "body").
        extract: What to extract — "text", "html", or "attrs".
    """
    html, error = await _fetch(url)
    if error:
        return error

    soup = BeautifulSoup(html, "lxml")
    elements = soup.select(selector, limit=_MAX_ELEMENTS)

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
    return _truncate(output)


@tool
async def scrape_table(url: str, table_index: int = 0) -> str:
    """Fetch a URL and extract a table as markdown.

    Args:
        url: The URL to fetch.
        table_index: 0-based index of the table to extract.
    """
    html, error = await _fetch(url)
    if error:
        return error

    soup = BeautifulSoup(html, "lxml")
    tables = soup.find_all("table")

    if not tables:
        return "No tables found on the page."
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
    return _truncate(output)


@tool
async def page_info(url: str) -> str:
    """Fetch a URL and return page metadata as compact JSON.

    Returns title, description, OG tags, canonical URL, and link/image/table counts.
    """
    html, error = await _fetch(url)
    if error:
        return error

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


# ---------------------------------------------------------------------------
# Structured data extraction (JSON-LD, OpenGraph, meta tags)
# ---------------------------------------------------------------------------


def _filter_structured(data, fields: set[str]):
    """Recursively filter nested dicts/lists to only keep matching field names."""
    if isinstance(data, dict):
        filtered = {}
        for k, v in data.items():
            key_lower = k.lower().lstrip("@")
            if key_lower in fields:
                filtered[k] = v
            else:
                child = _filter_structured(v, fields)
                if child is not None:
                    filtered[k] = child
        return filtered if filtered else None
    if isinstance(data, list):
        results = []
        for item in data:
            child = _filter_structured(item, fields)
            if child is not None:
                results.append(child)
        return results if results else None
    return None


@tool
async def scrape_json(url: str, fields: str = "") -> str:
    """Extract structured data from a page (JSON-LD, OpenGraph, meta tags).

    Great for product pages (Shopify, Amazon), articles, and recipes that
    embed structured data. Zero extra LLM calls — pure HTML parsing.

    Args:
        url: The URL to fetch.
        fields: Optional comma-separated field names to filter (e.g. "name,price,image").
    """
    html, error = await _fetch(url)
    if error:
        return error

    soup = BeautifulSoup(html, "lxml")
    result: dict = {}

    # JSON-LD
    json_ld_scripts = soup.find_all("script", attrs={"type": "application/ld+json"})
    json_ld_items: list = []
    for script in json_ld_scripts:
        try:
            data = json.loads(script.string or "")
            if isinstance(data, dict) and "@graph" in data:
                json_ld_items.extend(data["@graph"])
            elif isinstance(data, list):
                json_ld_items.extend(data)
            else:
                json_ld_items.append(data)
        except (json.JSONDecodeError, TypeError):
            continue
    if json_ld_items:
        result["json_ld"] = json_ld_items

    # OpenGraph meta tags
    og_tags: dict[str, str] = {}
    for tag in soup.find_all("meta", attrs={"property": True}):
        prop = tag.get("property", "")
        if prop.startswith("og:"):
            og_tags[prop.removeprefix("og:")] = tag.get("content", "")
    if og_tags:
        result["opengraph"] = og_tags

    # Standard meta tags
    meta_tags: dict[str, str] = {}
    for name in ("description", "author", "keywords"):
        tag = soup.find("meta", attrs={"name": name})
        if tag and tag.get("content"):
            meta_tags[name] = tag["content"]
    if meta_tags:
        result["meta"] = meta_tags

    # Title
    title_tag = soup.find("title")
    if title_tag:
        result["title"] = title_tag.get_text(strip=True)

    if not result:
        return "No structured data found on this page."

    # Optional field filtering
    if fields.strip():
        field_set = {f.strip().lower() for f in fields.split(",") if f.strip()}
        filtered = _filter_structured(result, field_set)
        if filtered:
            result = filtered
        else:
            return f"No fields matching '{fields}' found in the structured data."

    output = json.dumps(result, indent=2, ensure_ascii=False)
    return _truncate(output)


# ---------------------------------------------------------------------------
# Multi-page crawl
# ---------------------------------------------------------------------------


@tool
async def crawl(url: str, max_pages: int = 5, selector: str = "body") -> str:
    """Follow internal links breadth-first and extract content from each page.

    Useful for scraping product listings, documentation, or any set of same-domain pages.

    Args:
        url: The starting URL.
        max_pages: Maximum number of pages to visit (1–10, default 5).
        selector: CSS selector to extract content from each page (default: "body").
    """
    max_pages = max(1, min(10, max_pages))
    parsed_start = urlparse(url)
    domain = parsed_start.netloc

    visited: set[str] = set()
    queue: list[str] = [url]
    results: list[str] = []

    while queue and len(visited) < max_pages:
        current_url = queue.pop(0)

        # Normalize for dedup (strip query string)
        normalized = urlparse(current_url)._replace(query="", fragment="").geturl()
        if normalized in visited:
            continue
        visited.add(normalized)

        html, error = await _fetch(current_url)
        if error:
            results.append(f"[{current_url}]\nError: {error}")
            continue

        soup = BeautifulSoup(html, "lxml")

        # Extract content with selector
        elements = soup.select(selector, limit=_MAX_ELEMENTS)
        if elements:
            text = "\n".join(el.get_text(separator=" ", strip=True) for el in elements)
            results.append(f"[{current_url}]\n{text}")
        else:
            results.append(f"[{current_url}]\nNo elements matching '{selector}'.")

        # Discover same-domain links
        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"]
            abs_url = urljoin(current_url, href)
            parsed = urlparse(abs_url)

            if parsed.scheme not in ("http", "https"):
                continue
            if parsed.netloc != domain:
                continue

            norm = parsed._replace(query="", fragment="").geturl()
            if norm not in visited:
                queue.append(abs_url)

    output = "\n\n---\n\n".join(results)
    return _truncate(output, limit=20_000)
