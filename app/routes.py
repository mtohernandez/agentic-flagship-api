import asyncio
import json
import logging

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from langgraph.errors import GraphRecursionError

from app.security import verify_api_key

logger = logging.getLogger(__name__)

router = APIRouter()


def _sse(event: str, data: str) -> str:
    return f"data: {json.dumps({'type': event, 'content': data})}\n\n"


@router.get("/run-mission")
async def run_mission(
    request: Request,
    prompt: str = Query(min_length=1, max_length=2000),
    _key: str = Depends(verify_api_key),
):
    agent = request.app.state.agent
    settings = request.app.state.settings
    timeout = settings.agent_request_timeout
    truncated = prompt[:80] + "…" if len(prompt) > 80 else prompt
    logger.info("Request: %s", truncated)

    async def event_generator():
        try:
            async with asyncio.timeout(timeout):
                async for event in agent.astream_events(
                    {"messages": [("user", prompt)]},
                    version="v2",
                    config={"recursion_limit": settings.agent_recursion_limit},
                ):
                    kind = event["event"]
                    if kind == "on_chat_model_stream":
                        content = event["data"]["chunk"].content
                        if content:
                            yield _sse("token", content)
                    elif kind == "on_tool_start":
                        yield _sse("tool_start", event["name"])
                    elif kind == "on_tool_end":
                        yield _sse("tool_end", event["name"])

            yield _sse("done", "")
            logger.info("Completed: %s", truncated)

        except GraphRecursionError:
            logger.warning("Recursion limit hit: %s", truncated)
            yield _sse("error", "Agent exceeded maximum reasoning steps. Try a simpler prompt.")
        except TimeoutError:
            logger.warning("Timeout after %ds: %s", timeout, truncated)
            yield _sse("error", f"Request timed out after {timeout} seconds.")
        except Exception:
            logger.exception("Stream error: %s", truncated)
            yield _sse("error", "An internal error occurred.")

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.get("/health")
async def health(request: Request):
    browser_ok = request.app.state.browser_manager.is_alive
    return {"status": "healthy", "browser": browser_ok}
