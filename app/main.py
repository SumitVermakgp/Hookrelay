import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from starlette.concurrency import run_in_threadpool

from app.api import deliveries, endpoints, events, health
from app.core.body_limit import BodySizeLimitMiddleware
from app.core.config import Settings, load_settings
from app.core.url_policy import Resolver, default_resolver
from app.db.database import init_db
from app.services.delivery import run_due_retries

logger = logging.getLogger("hookrelay.retry")


async def _retry_loop(app: FastAPI) -> None:
    settings: Settings = app.state.settings
    while True:
        await asyncio.sleep(settings.retry_interval_seconds)
        try:
            await run_in_threadpool(
                run_due_retries,
                settings,
                resolver=app.state.resolver,
                transport=app.state.transport,
            )
        except Exception as exc:  # noqa: BLE001 - keep the loop alive; log the type only
            logger.error("retry sweep failed type=%s", type(exc).__name__)


def create_app(
    settings: Settings | None = None,
    resolver: Resolver = default_resolver,
    transport: httpx.BaseTransport | None = None,
) -> FastAPI:
    resolved = settings or load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        init_db(resolved.db_path)
        task: asyncio.Task[None] | None = None
        if resolved.retry_interval_seconds > 0:
            task = asyncio.create_task(_retry_loop(app))
        try:
            yield
        finally:
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    app = FastAPI(title="HookRelay", version="0.1.0", lifespan=lifespan)
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=resolved.max_payload_bytes)
    app.state.settings = resolved
    app.state.resolver = resolver
    app.state.transport = transport
    app.include_router(health.router)
    app.include_router(endpoints.router)
    app.include_router(events.router)
    app.include_router(deliveries.router)
    return app


app = create_app()
