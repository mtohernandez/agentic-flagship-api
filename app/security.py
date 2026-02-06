import ipaddress
import logging
import secrets
import socket
import time
from collections import deque
from urllib.parse import urlparse

from fastapi import HTTPException, Request, Response, Security
from fastapi.security import APIKeyHeader
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def verify_api_key(
    request: Request,
    api_key: str | None = Security(_api_key_header),
) -> str:
    if api_key is None:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
    for valid_key in request.app.state.settings.get_api_keys():
        if secrets.compare_digest(api_key, valid_key):
            return api_key
    raise HTTPException(status_code=401, detail="Invalid or missing API key")


# ---------------------------------------------------------------------------
# Rate limiting middleware (in-memory sliding window)
# ---------------------------------------------------------------------------

class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, rpm: int):
        super().__init__(app)
        self.rpm = rpm
        self._windows: dict[str, deque[float]] = {}
        self._request_count = 0

    async def dispatch(self, request: Request, call_next) -> Response:
        api_key = request.headers.get("X-API-Key")
        if api_key is None:
            return await call_next(request)

        now = time.time()
        window = self._windows.setdefault(api_key, deque())

        while window and window[0] < now - 60:
            window.popleft()

        if len(window) >= self.rpm:
            retry_after = int(61 - (now - window[0]))
            logger.warning("Rate limit hit for key ending …%s", api_key[-4:])
            return Response(
                content='{"detail":"Rate limit exceeded. Try again later."}',
                status_code=429,
                media_type="application/json",
                headers={"Retry-After": str(max(retry_after, 1))},
            )

        window.append(now)

        self._request_count += 1
        if self._request_count % 100 == 0:
            self._cleanup(now)

        return await call_next(request)

    def _cleanup(self, now: float) -> None:
        stale = [k for k, v in self._windows.items() if not v or v[-1] < now - 60]
        for k in stale:
            del self._windows[k]


# ---------------------------------------------------------------------------
# SSRF protection for fetch_page tool
# ---------------------------------------------------------------------------

_BLOCKED_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
]


def validate_url(url: str) -> str | None:
    """Return an error string if the URL is unsafe, else None."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return f"Blocked: URL scheme '{parsed.scheme}' is not allowed. Use http or https."

    hostname = parsed.hostname
    if not hostname:
        return "Blocked: could not parse hostname from URL."

    try:
        addr_info = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        return f"Blocked: could not resolve hostname '{hostname}'."

    for family, _, _, _, sockaddr in addr_info:
        ip = ipaddress.ip_address(sockaddr[0])
        for network in _BLOCKED_NETWORKS:
            if ip in network:
                return f"Blocked: URL resolves to a private/internal address ({ip})."

    return None
