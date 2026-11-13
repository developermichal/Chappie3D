"""Gateway facade proxying AI agent requests to internal agent service."""

import logging
from collections.abc import Callable

import httpx
from fastapi import APIRouter, Request, Response

from services.backend.config import get_backend_settings

logger = logging.getLogger("services.backend.gateway")
router = APIRouter(tags=["Gateway Proxy"])

HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "host",
}

_downstream_transport: httpx.AsyncBaseTransport | None = None
_downstream_client_factory: Callable[[], httpx.AsyncClient] | None = None


def set_downstream_transport(transport: httpx.AsyncBaseTransport | None) -> None:
    """Set optional downstream transport for in-process testing or custom routing."""
    global _downstream_transport
    _downstream_transport = transport


def get_downstream_transport() -> httpx.AsyncBaseTransport | None:
    """Get current downstream transport."""
    return _downstream_transport


def set_downstream_client_factory(factory: Callable[[], httpx.AsyncClient] | None) -> None:
    """Set optional custom client factory for downstream proxying."""
    global _downstream_client_factory
    _downstream_client_factory = factory


def get_downstream_client() -> httpx.AsyncClient:
    """Instantiate or retrieve downstream HTTP client."""
    if _downstream_client_factory is not None:
        return _downstream_client_factory()
    if _downstream_transport is not None:
        return httpx.AsyncClient(transport=_downstream_transport, timeout=600.0)
    return httpx.AsyncClient(timeout=600.0)


@router.api_route(
    "/api/{full_path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"],
    include_in_schema=False,
)
async def gateway_proxy(full_path: str, request: Request) -> Response:
    """Proxy non-auth API requests directly to internal agent service."""
    settings = get_backend_settings()
    target_url = f"{settings.agent_service_url.rstrip('/')}/api/{full_path}"

    headers = {
        k: v for k, v in request.headers.items() if k.lower() not in HOP_BY_HOP_HEADERS
    }

    body = await request.body()
    params = dict(request.query_params)

    try:
        async with get_downstream_client() as client:
            downstream_res = await client.request(
                method=request.method,
                url=target_url,
                headers=headers,
                params=params,
                content=body,
            )

            res_headers = {
                k: v
                for k, v in downstream_res.headers.items()
                if k.lower() not in HOP_BY_HOP_HEADERS
            }

            return Response(
                content=downstream_res.content,
                status_code=downstream_res.status_code,
                headers=res_headers,
                media_type=downstream_res.headers.get("content-type"),
            )
    except (httpx.RequestError, httpx.HTTPError, OSError) as exc:
        logger.error("Failed to proxy request to agent service (%s): %s", target_url, exc)
        return Response(
            content=f'{{"detail": "Agent service unreachable: {exc!s}"}}'.encode(),
            status_code=502,
            media_type="application/json",
        )
