"""Async PostgreSQL/TimescaleDB client with connection pooling and batch writes."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import asyncpg

logger = logging.getLogger(__name__)


@dataclass
class TransactionRecord:
    chain: str
    tx_hash: str
    ts: datetime = field(default_factory=lambda: datetime.now(UTC))
    block_number: int | None = None
    from_address: str | None = None
    to_address: str | None = None
    value_wei: int = 0
    gas_price_wei: int | None = None
    gas_limit: int | None = None
    gas_used: int | None = None
    is_contract: bool = False
    contract_type: str | None = None
    status: str = "pending"
    mempool_age_ms: int | None = None
    raw_data: dict[str, Any] | None = None


@dataclass
class BlockRecord:
    chain: str
    block_number: int
    block_hash: str
    ts: datetime = field(default_factory=lambda: datetime.now(UTC))
    parent_hash: str | None = None
    tx_count: int | None = None
    gas_used: int | None = None
    gas_limit: int | None = None
    base_fee_wei: int | None = None
    miner: str | None = None


@dataclass
class AnomalyRecord:
    chain: str
    anomaly_type: str
    severity: str
    ts: datetime = field(default_factory=lambda: datetime.now(UTC))
    description: str | None = None
    tx_hash: str | None = None
    from_address: str | None = None
    value_eth: float | None = None
    metadata: dict[str, Any] | None = None
    alerted: bool = False


class Database:
    def __init__(self, dsn: str, batch_size: int = 100, flush_interval: float = 5.0) -> None:
        self._dsn = dsn
        self._pool: asyncpg.Pool | None = None
        self._batch_size = batch_size
        self._flush_interval = flush_interval
        self._tx_buffer: list[TransactionRecord] = []
        self._flush_task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()

    async def connect(self) -> None:
        self._pool = await asyncpg.create_pool(
            self._dsn,
            min_size=2,
            max_size=10,
            command_timeout=30,
        )
        logger.info("Database pool established")
        self._flush_task = asyncio.create_task(self._flush_loop())

    async def close(self) -> None:
        if self._flush_task:
            self._flush_task.cancel()
        await self._flush_pending()
        if self._pool:
            await self._pool.close()

    async def apply_schema(self, schema_path: str) -> None:
        assert self._pool
        sql = open(schema_path).read()  # noqa: PTH123, WPS515
        async with self._pool.acquire() as conn:
            await conn.execute(sql)
        logger.info("Schema applied")

    # ── Transactions ──────────────────────────────────────────────────────

    async def insert_transaction(self, rec: TransactionRecord) -> None:
        async with self._lock:
            self._tx_buffer.append(rec)
            if len(self._tx_buffer) >= self._batch_size:
                await self._flush_transactions()

    async def _flush_transactions(self) -> None:
        if not self._tx_buffer or not self._pool:
            return
        batch = self._tx_buffer[:]
        self._tx_buffer.clear()
        records = [
            (
                r.ts, r.chain, r.tx_hash, r.block_number,
                r.from_address, r.to_address,
                str(r.value_wei) if r.value_wei else "0",
                str(r.gas_price_wei) if r.gas_price_wei else None,
                r.gas_limit, r.gas_used, r.is_contract,
                r.contract_type, r.status, r.mempool_age_ms,
            )
            for r in batch
        ]
        async with self._pool.acquire() as conn:
            await conn.executemany(
                """
                INSERT INTO transactions
                    (ts, chain, tx_hash, block_number, from_address, to_address,
                     value_wei, gas_price_wei, gas_limit, gas_used,
                     is_contract, contract_type, status, mempool_age_ms)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)
                ON CONFLICT DO NOTHING
                """,
                records,
            )
        logger.debug("Flushed %d transactions to DB", len(batch))

    # ── Blocks ────────────────────────────────────────────────────────────

    async def insert_block(self, rec: BlockRecord) -> None:
        assert self._pool
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO blocks
                    (ts, chain, block_number, block_hash, parent_hash,
                     tx_count, gas_used, gas_limit, base_fee_wei, miner)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
                ON CONFLICT DO NOTHING
                """,
                rec.ts, rec.chain, rec.block_number, rec.block_hash,
                rec.parent_hash, rec.tx_count, rec.gas_used, rec.gas_limit,
                str(rec.base_fee_wei) if rec.base_fee_wei else None,
                rec.miner,
            )

    # ── Anomalies ─────────────────────────────────────────────────────────

    async def insert_anomaly(self, rec: AnomalyRecord) -> None:
        assert self._pool
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO anomalies
                    (ts, chain, anomaly_type, severity, description,
                     tx_hash, from_address, value_eth, metadata, alerted)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
                """,
                rec.ts, rec.chain, rec.anomaly_type, rec.severity,
                rec.description, rec.tx_hash, rec.from_address,
                rec.value_eth, rec.metadata, rec.alerted,
            )

    # ── Queries ───────────────────────────────────────────────────────────

    async def get_recent_transactions(
        self, chain: str, limit: int = 50
    ) -> Sequence[asyncpg.Record]:
        assert self._pool
        async with self._pool.acquire() as conn:
            return await conn.fetch(
                """
                SELECT * FROM transactions
                WHERE chain = $1
                ORDER BY ts DESC
                LIMIT $2
                """,
                chain, limit,
            )

    async def get_tx_rate_1min(self, chain: str) -> float:
        """Transactions per second over last 60s."""
        assert self._pool
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT COUNT(*) AS cnt FROM transactions
                WHERE chain = $1
                  AND ts > NOW() - INTERVAL '60 seconds'
                """,
                chain,
            )
        return (row["cnt"] / 60.0) if row else 0.0

    async def get_avg_gas_price_10min(self, chain: str) -> float | None:
        assert self._pool
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT AVG(gas_price_wei::NUMERIC) AS avg_gas
                FROM transactions
                WHERE chain = $1
                  AND ts > NOW() - INTERVAL '10 minutes'
                  AND gas_price_wei IS NOT NULL
                """,
                chain,
            )
        return float(row["avg_gas"]) if row and row["avg_gas"] else None

    # ── Internal ──────────────────────────────────────────────────────────

    async def _flush_loop(self) -> None:
        while True:
            await asyncio.sleep(self._flush_interval)
            async with self._lock:
                await self._flush_transactions()

    async def _flush_pending(self) -> None:
        async with self._lock:
            await self._flush_transactions()
