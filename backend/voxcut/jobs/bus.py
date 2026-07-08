"""In-process pub/sub for progress events, bridged to SSE (spec §12).

The job runner publishes dicts; SSE endpoints subscribe and stream them to the
browser. No external broker — a plain asyncio fan-out.
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator


class ProgressBus:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[dict]] = set()

    async def publish(self, event: dict) -> None:
        for q in list(self._subscribers):
            # Never let a slow/stuck subscriber block the runner.
            if q.qsize() < 1000:
                q.put_nowait(event)

    def publish_threadsafe(self, loop: asyncio.AbstractEventLoop, event: dict) -> None:
        """Publish from a worker thread/process callback."""
        asyncio.run_coroutine_threadsafe(self.publish(event), loop)

    async def subscribe(self) -> AsyncIterator[dict]:
        q: asyncio.Queue[dict] = asyncio.Queue()
        self._subscribers.add(q)
        try:
            # Send a hello so clients know the stream is live.
            yield {"type": "connected"}
            while True:
                yield await q.get()
        finally:
            self._subscribers.discard(q)


def sse_format(event: dict) -> str:
    """Serialize a dict as one Server-Sent Event frame."""
    return f"data: {json.dumps(event)}\n\n"


bus = ProgressBus()
