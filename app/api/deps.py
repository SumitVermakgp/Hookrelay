import httpx
from fastapi import Request

from app.core.url_policy import Resolver


def get_resolver(request: Request) -> Resolver:
    resolver: Resolver = request.app.state.resolver
    return resolver


def get_transport(request: Request) -> httpx.BaseTransport | None:
    """None means a real HTTP transport. Tests pass a mock to create_app."""
    transport: httpx.BaseTransport | None = request.app.state.transport
    return transport
