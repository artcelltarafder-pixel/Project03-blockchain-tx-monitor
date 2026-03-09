"""Stream manager — coordinates all chain streams, exposes unified async queue."""

from __future__ import annotations

import asyncio
import enum
import logging
from dataclasses import dataclass, field
from typing import Any

from src.ingestion.alchemy_ws import AlchemyWebSocket, RawBlock, RawTransaction

logger = logging.getLogger(__name__)


class EventType(enum.StrEnum):
    PENDING_TX = "pending_tx"
    NEW_BLOCK = "new_block"


@dataclass
class StreamEvent:
    event_type: EventType
    chain: str
    data: RawTransaction | RawBlock
    metadata: dict[str, Any] = field(default_factory=dict)


class StreamManager:
    """
    Manages multiple chain streams and exposes a single asyncio.Queue
    for downstream consumers. Handles backpressure by dropping events
    when the queue is full (configurable maxsize).
    """

    def __init__(self, config: dict[str, Any], maxsize: int = 10_000) -> None:
        self._config = config
        self.queue: asyncio.Queue[StreamEvent] = asyncio.Queue(maxsize=maxsize)
        self._streams: list[AlchemyWebSocket] = []
        self._tasks: list[asyncio.Task[None]] = []
        self._dropped = 0

    async def start(self) -> None:
        eth_cfg = self._config.get("chains", {}).get("ethereum", {})
        if eth_cfg.get("enabled"):
            ws = AlchemyWebSocket(
                ws_url=eth_cfg["ws_url"],
                on_transaction=self._make_tx_cb("ethereum"),
                on_block=self._make_block_cb("ethereum"),
                reconnect_delay=eth_cfg.get("reconnect_delay_seconds", 5),
                max_reconnects=eth_cfg.get("max_reconnect_attempts", 0),
            )
            self._streams.append(ws)
            self._tasks.append(asyncio.create_task(ws.start(), name="eth-stream"))
            logger.info("Ethereum stream started")

        if not self._tasks:
            logger.warning("No chain streams enabled — check config.yaml")

    async def stop(self) -> None:
        for s in self._streams:
            await s.stop()
        for t in self._tasks:
            t.cancel()
        logger.info("StreamManager stopped (dropped=%d events)", self._dropped)

    def _make_tx_cb(self, chain: str):  # type: ignore[return]
        def cb(tx: RawTransaction) -> None:
            event = StreamEvent(event_type=EventType.PENDING_TX, chain=chain, data=tx)
            self._enqueue(event)
        return cb

    def _make_block_cb(self, chain: str):  # type: ignore[return]
        def cb(block: RawBlock) -> None:
            event = StreamEvent(event_type=EventType.NEW_BLOCK, chain=chain, data=block)
            self._enqueue(event)
        return cb

    def _enqueue(self, event: StreamEvent) -> None:
        try:
            self.queue.put_nowait(event)
        except asyncio.QueueFull:
            self._dropped += 1
            if self._dropped % 1000 == 0:
                logger.warning("Queue full — %d events dropped so far", self._dropped)

    @property
    def dropped_count(self) -> int:
        return self._dropped
