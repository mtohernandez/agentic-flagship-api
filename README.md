# Agentic Flagship API

A FastAPI server that exposes an AI-powered web research agent over Server-Sent Events (SSE). You send a natural language prompt, and the agent autonomously browses the web, scrapes pages, extracts data, and streams its reasoning and results back to you in real time.

Under the hood it uses **LangGraph** (ReAct agent loop), **Groq** (fast LLM inference), and **Playwright** (headless browser) combined with lightweight HTTP scraping tools for speed.

---

## Table of Contents

- [Quick Start](#quick-start)
- [How It Works](#how-it-works)
- [Project Structure](#project-structure)
- [Architecture Deep Dive](#architecture-deep-dive)
- [API Reference](#api-reference)
- [Configuration Reference](#configuration-reference)
- [Security Model](#security-model)

---

## Quick Start

### Prerequisites

- **Python 3.13+**
- **[uv](https://docs.astral.sh/uv/)** (package manager)
- A **[Groq API key](https://console.groq.com/)** (free tier works)

### 1. Clone and install

```bash
git clone <your-repo-url>
cd agentic-flagship-api
uv sync
```

### 2. Install the Playwright browser

Playwright needs a Chromium binary. This is a one-time download (~150 MB):

```bash
uv run playwright install chromium
```

### 3. Configure environment

Copy the example env file and fill in your keys:

```bash
cp .env.example .env
```

Open `.env` and set the two **required** values:

```dotenv
# Your Groq API key (get one at https://console.groq.com)
GROQ_API_KEY=gsk_your_actual_key_here

# Comma-separated list of API keys that clients must send to use the agent.
# Generate something random — this is YOUR access control, not Groq's key.
API_KEYS=my-secret-key-1
```

Everything else has sensible defaults. See [Configuration Reference](#configuration-reference) for the full list.

### 4. Start the server

```bash
uv run uvicorn main:app
```

You should see output like:

```
2025-01-15 10:00:00 | INFO     | app.main | Settings loaded
2025-01-15 10:00:01 | INFO     | app.browser | Browser started (headless=True)
2025-01-15 10:00:01 | INFO     | app.agent | Building agent with 11 tools: fetch_page, parse_html, extract_table_data, extract_metadata, click_element, navigate_browser, previous_webpage, extract_text, extract_hyperlinks, get_elements, current_webpage
2025-01-15 10:00:01 | INFO     | app.main | Startup complete
INFO:     Uvicorn running on http://127.0.0.1:8000
```

### 5. Test it

**Health check** (no auth required):

```bash
curl http://localhost:8000/health
```

```json
{"status": "healthy", "browser": true}
```

**Send a prompt** (requires your API key in the `X-API-Key` header):

```bash
curl -N -H "X-API-Key: my-secret-key-1" \
  "http://localhost:8000/run-mission?prompt=What%20is%20the%20title%20of%20example.com"
```

You'll see a stream of SSE events:

```
data: {"type": "tool_start", "content": "fetch_page"}

data: {"type": "tool_end", "content": "fetch_page"}

data: {"type": "token", "content": "The"}

data: {"type": "token", "content": " title"}

data: {"type": "token", "content": " of"}

data: {"type": "token", "content": " example.com is \"Example Domain\"."}

data: {"type": "done", "content": ""}
```

That's it. The agent fetched the page, extracted the title, and streamed the answer back.

---

## How It Works

Here's what happens when a request hits `/run-mission`:

```
Client                        Server                      External
  |                             |                            |
  |-- GET /run-mission -------->|                            |
  |   + X-API-Key header        |                            |
  |   + ?prompt=...             |                            |
  |                             |                            |
  |                        [1] Rate limit check              |
  |                        [2] API key validation            |
  |                        [3] Prompt validation (1-2000 ch) |
  |                             |                            |
  |<--- SSE stream opens -------|                            |
  |                             |                            |
  |                        [4] Agent receives prompt          |
  |                             |                            |
  |                        [5] LLM decides which tool to use |
  |                             |------- fetch_page -------->| (fast HTTP)
  |<-- tool_start: fetch_page --|                            |
  |                             |<------ HTML response ------|
  |<-- tool_end: fetch_page ----|                            |
  |                             |                            |
  |                        [6] LLM reads HTML, calls next tool|
  |                             |------- parse_html -------->| (local, no network)
  |<-- tool_start: parse_html --|                            |
  |<-- tool_end: parse_html ----|                            |
  |                             |                            |
  |                        [7] LLM formulates answer          |
  |<-- token: "The" ------------|                            |
  |<-- token: " title" ---------|                            |
  |<-- token: " is ..." --------|                            |
  |                             |                            |
  |<-- done --------------------|                            |
  |                             |                            |
```

The agent runs a **ReAct loop** (Reason + Act): the LLM thinks about what to do, calls a tool, reads the result, thinks again, and repeats until it has an answer. The entire loop is wrapped in a configurable timeout (default 300s).

---

## Project Structure

```
agentic-flagship-api/
  main.py               # Entry point: just `from app.main import app`
  pyproject.toml         # Dependencies and project metadata
  .env                   # Your secrets (gitignored)
  .env.example           # Template showing all config options

  app/
    __init__.py          # Package marker (empty)
    main.py              # App factory, lifespan (startup/shutdown), middleware
    config.py            # Settings class — reads .env into typed Python objects
    security.py          # API key auth, rate limiting, SSRF protection
    routes.py            # HTTP endpoints (/run-mission, /health)
    agent.py             # Builds the LangGraph ReAct agent with tools + system prompt
    tools.py             # 4 custom scraping tools (fetch_page, parse_html, etc.)
    browser.py           # Playwright browser singleton lifecycle
    logging.py           # Logging configuration
```

---

## Architecture Deep Dive

### Startup Sequence

When `uvicorn main:app` runs, here's what happens:

1. **`create_app()`** (sync) — Loads `Settings` from `.env`, creates the FastAPI instance, attaches CORS and rate limiting middleware. Settings are available immediately for middleware config.

2. **`lifespan()`** (async) — Runs after the app is created:
   - Configures structured logging
   - Starts a **single shared Chromium browser** (Playwright)
   - Builds the **LangGraph agent** (compiles the graph once)
   - Stores everything on `app.state` for request handlers to access

3. **Shutdown** — When the server stops, the lifespan context manager closes the shared HTTP client and the browser cleanly. No resource leaks.

### The Agent

The agent is built with `langgraph.prebuilt.create_react_agent`, which creates a **ReAct loop** (Reasoning + Acting):

```
          +---------+
          |  START  |
          +----+----+
               |
               v
          +----+----+
    +---->|   LLM   |-----> no tool calls -----> END
    |     +----+----+
    |          |
    |     has tool calls
    |          |
    |          v
    |     +----+----+
    +-----|  Tools  |
          +---------+
```

Each loop iteration:
1. The LLM sees the conversation so far (user prompt + previous tool results)
2. It decides to either call a tool or respond to the user
3. If it calls a tool, the result is added to the conversation and the loop repeats
4. If it responds directly, the loop ends

The agent has **11 tools** available:

| Tool | Source | Speed | Use case |
|------|--------|-------|----------|
| `fetch_page` | Custom (httpx) | Fast | Fetch raw HTML from any URL |
| `parse_html` | Custom (BeautifulSoup) | Instant | Extract data with CSS selectors |
| `extract_table_data` | Custom (BeautifulSoup) | Instant | Convert HTML tables to markdown |
| `extract_metadata` | Custom (BeautifulSoup) | Instant | Get title, description, OG tags |
| `navigate_browser` | Playwright | Slow | Load JS-heavy pages |
| `click_element` | Playwright | Slow | Click buttons/links |
| `extract_text` | Playwright | Slow | Get visible text from browser |
| `extract_hyperlinks` | Playwright | Slow | Get all links from browser |
| `get_elements` | Playwright | Slow | Query elements in browser |
| `current_webpage` | Playwright | Instant | Get current browser URL |
| `previous_webpage` | Playwright | Slow | Go back in browser history |

The system prompt teaches the agent to **prefer the fast custom tools** and only fall back to the slower Playwright browser tools when a page requires JavaScript rendering or user interaction (clicking, form filling).

### SSE Event Protocol

The streaming response uses Server-Sent Events with 5 event types:

| Event | Meaning | Example payload |
|-------|---------|-----------------|
| `token` | A chunk of the LLM's response | `"The title is"` |
| `tool_start` | Agent started calling a tool | `"fetch_page"` |
| `tool_end` | Tool finished executing | `"fetch_page"` |
| `done` | Stream completed successfully | `""` |
| `error` | Something went wrong | `"Request timed out after 300 seconds."` |

Every SSE line is a JSON object: `{"type": "<event>", "content": "<data>"}`.

A well-behaved client should listen for `done` to know the stream ended cleanly, and handle `error` events for display.

### Error Handling (6 Layers)

Errors are caught at the most appropriate level so they never crash the server:

| Layer | What it catches | Response |
|-------|----------------|----------|
| **Startup** | Missing `GROQ_API_KEY`, missing `API_KEYS`, bad config | App refuses to start with a clear validation error |
| **Middleware** | Rate limit exceeded | 429 JSON + `Retry-After` header |
| **Auth** | Missing or invalid API key | 401 JSON |
| **Validation** | Empty or oversized prompt | 422 JSON (automatic from FastAPI) |
| **Stream** | Agent recursion limit, timeout, unexpected errors | SSE `error` event |
| **Tool** | HTTP errors, parse failures | Error string returned to LLM (it adapts) |
| **Global** | Anything uncaught | 500 JSON + logged traceback |

Tools **never raise exceptions** — they return error strings so the LLM can see what went wrong and try a different approach (e.g., fall back from `fetch_page` to `navigate_browser`).

---

## API Reference

### `GET /run-mission`

Sends a prompt to the agent and streams the response as SSE.

**Authentication:** Required. Pass `X-API-Key` header.

**Query Parameters:**

| Parameter | Type | Required | Constraints | Description |
|-----------|------|----------|-------------|-------------|
| `prompt` | string | Yes | 1-2000 chars | The task for the agent |

**Example:**

```bash
curl -N -H "X-API-Key: my-secret-key-1" \
  "http://localhost:8000/run-mission?prompt=Summarize%20the%20front%20page%20of%20news.ycombinator.com"
```

**Response:** `text/event-stream` with JSON payloads (see [SSE Event Protocol](#sse-event-protocol)).

**Error Responses:**

| Status | Cause |
|--------|-------|
| 401 | Missing or invalid `X-API-Key` |
| 422 | `prompt` is empty or exceeds 2000 characters |
| 429 | Rate limit exceeded |

---

### `GET /health`

Returns server health status. **No authentication required.**

**Example:**

```bash
curl http://localhost:8000/health
```

**Response:**

```json
{"status": "healthy", "browser": true}
```

`browser` is `true` if the Playwright Chromium instance is connected and responsive.

---

## Configuration Reference

All configuration is done through environment variables in `.env`. Two values are **required** — the rest have defaults.

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `GROQ_API_KEY` | **Yes** | — | Your Groq API key for LLM inference |
| `API_KEYS` | **Yes** | — | Comma-separated list of valid API keys for client auth |
| `GROQ_MODEL` | No | `llama-3.3-70b-versatile` | Groq model identifier |
| `GROQ_TEMPERATURE` | No | `0.0` | LLM sampling temperature (0 = deterministic) |
| `AGENT_RECURSION_LIMIT` | No | `40` | Max LLM-tool round trips before stopping |
| `AGENT_REQUEST_TIMEOUT` | No | `300` | Per-request hard timeout in seconds |
| `BROWSER_HEADLESS` | No | `true` | Run Chromium in headless mode |
| `CORS_ORIGINS` | No | `*` | Comma-separated allowed CORS origins |
| `RATE_LIMIT_RPM` | No | `20` | Max requests per minute per API key |
| `DEBUG` | No | `false` | Enable debug-level logging |

---

## Security Model

### API Key Authentication

Every request to `/run-mission` must include an `X-API-Key` header. The key is validated against the `API_KEYS` list using **timing-safe comparison** (`secrets.compare_digest`) to prevent timing attacks. Invalid or missing keys get a `401`.

The `/health` endpoint is intentionally public for monitoring and load balancer probes.

### Rate Limiting

An in-memory **sliding window** rate limiter runs as middleware. It tracks request timestamps per API key over a 60-second window. When a key exceeds `RATE_LIMIT_RPM` (default 20), subsequent requests receive a `429` with a `Retry-After` header.

Stale entries are cleaned up lazily every 100 requests to prevent memory growth.

### SSRF Protection

The `fetch_page` tool includes Server-Side Request Forgery protection. Before making any HTTP request, it:

1. Validates the URL scheme is `http` or `https` (blocks `file://`, `ftp://`, etc.)
2. Resolves the hostname to an IP address
3. Checks the IP against blocked private ranges: `127.0.0.0/8`, `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `169.254.0.0/16`, `::1/128`

This prevents the agent from being tricked into fetching internal services.

### Request Timeout

Every agent invocation is wrapped in `asyncio.timeout(AGENT_REQUEST_TIMEOUT)`. If the LLM or any tool hangs, the stream terminates with an `error` event after the configured timeout (default 5 minutes). This prevents resource exhaustion from runaway requests.
