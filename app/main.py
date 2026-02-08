import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.agent import build_agent
from app.browser import BrowserManager
from app.config import Settings
from app.logging import setup_logging
from app.routes import router
from app.security import RateLimitMiddleware
from app.tools import cache_clear, http_client

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings: Settings = app.state.settings
    setup_logging(settings.debug)
    logger.info("Settings loaded")

    browser_manager = BrowserManager(
        headless=settings.browser_headless,
        nav_timeout=settings.browser_nav_timeout,
        action_timeout=settings.browser_action_timeout,
    )
    if settings.browser_enabled:
        await browser_manager.start()
    else:
        logger.info("Browser disabled via BROWSER_ENABLED=false")

    agent = build_agent(settings, browser_manager)

    app.state.browser_manager = browser_manager
    app.state.agent = agent

    logger.info("Startup complete")
    yield

    cache_clear()
    await http_client.aclose()
    await browser_manager.stop()
    logger.info("Shutdown complete")


def create_app() -> FastAPI:
    settings = Settings()

    app = FastAPI(title="Agentic Flagship API", lifespan=lifespan)
    app.state.settings = settings

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.get_cors_origins(),
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(RateLimitMiddleware, rpm=settings.rate_limit_rpm)

    app.include_router(router)

    @app.exception_handler(Exception)
    async def _global_exception_handler(request: Request, exc: Exception):
        logger.exception("Unhandled exception")
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})

    return app


app = create_app()
