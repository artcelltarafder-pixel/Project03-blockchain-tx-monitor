"""Main entrypoint — Phase 5: Rich terminal dashboard."""

from __future__ import annotations

import asyncio
import logging
import os
from dotenv import load_dotenv

from src.ingestion.alchemy_ws import AlchemyWebSocket, RawTransaction, RawBlock
from src.ingestion.price_feed import PriceFeed
from src.filters.filter_chain import FilterChain
from src.analysis.anomaly import VolumeAnomalyDetector, GasAnomalyDetector
from src.analysis.recirculation import RecirculationDetector, Transfer
from src.storage.db import Database, TransactionRecord, BlockRecord, AnomalyRecord
from src.dashboard.dashboard import Dashboard

load_dotenv()
logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

TX_COUNT = 0
price_feed: PriceFeed | None = None
db: Database | None = None
dashboard = Dashboard()
filter_chain = FilterChain(medium_eth=0.5, large_eth=10.0, whale_eth=100.0)
volume_detector = VolumeAnomalyDetector()
gas_detector = GasAnomalyDetector()
recirc_detector = RecirculationDetector(min_value_eth=1.0)


def on_transaction(tx: RawTransaction) -> None:
    global TX_COUNT
    TX_COUNT += 1

    result = filter_chain.process(tx)
    if not result:
        return

    # Update price in dashboard
    if price_feed and price_feed.eth_usd:
        dashboard.update_price(price_feed.eth_usd)

    usd = price_feed.eth_to_usd(result.value.value_eth) if price_feed else "n/a"
    gas_usd = price_feed.eth_to_usd(result.gas.gas_cost_eth) if price_feed else "n/a"

    # Send to dashboard
    dashboard.add_transaction({
        "hash": tx.tx_hash,
        "eth": result.value.value_eth,
        "usd": usd,
        "fee_eth": result.gas.gas_cost_eth,
        "gwei": result.gas.gas_price_gwei,
        "from": tx.from_address,
        "tags": " ".join(f"[{t}]" for t in result.tags),
        "level": result.alert_level,
    })

    if result.gas.gas_price_gwei > 0:
        dashboard.update_gas(result.gas.gas_price_gwei)

    # ── DB write ──────────────────────────────────────────────────────────
    if db:
        asyncio.create_task(db.insert_transaction(TransactionRecord(
            chain="ethereum",
            tx_hash=tx.tx_hash,
            from_address=tx.from_address,
            to_address=tx.to_address,
            value_wei=int(tx.value_hex, 16),
            gas_price_wei=int(tx.gas_price_hex, 16) if tx.gas_price_hex else None,
            gas_limit=result.gas.gas_limit,
            is_contract=result.contract.is_contract_call,
            contract_type=result.contract.contract_type,
            status="pending",
        )))

    # ── Volume anomaly ────────────────────────────────────────────────────
    vol_anomaly = volume_detector.record()
    if vol_anomaly.is_anomaly:
        dashboard.add_anomaly(
            vol_anomaly.anomaly_type or "VOLUME_SPIKE",
            f"{vol_anomaly.description} | x{vol_anomaly.spike_multiplier:.1f}",
        )
        if db:
            asyncio.create_task(db.insert_anomaly(AnomalyRecord(
                chain="ethereum",
                anomaly_type=vol_anomaly.anomaly_type or "VOLUME_SPIKE",
                severity=vol_anomaly.severity,
                description=vol_anomaly.description,
            )))

    # ── Gas anomaly ───────────────────────────────────────────────────────
    gas_anomaly = gas_detector.record(result.gas.gas_price_gwei)
    if gas_anomaly.is_anomaly:
        dashboard.add_anomaly(
            gas_anomaly.anomaly_type or "HIGH_GAS",
            gas_anomaly.description,
        )

    # ── Recirculation ─────────────────────────────────────────────────────
    if result.value.value_eth >= 1.0 and tx.from_address and tx.to_address:
        transfer = Transfer(
            tx_hash=tx.tx_hash,
            from_address=tx.from_address,
            to_address=tx.to_address,
            value_eth=result.value.value_eth,
        )
        recirc = recirc_detector.record(transfer)
        if recirc:
            usd_r = price_feed.eth_to_usd(recirc.total_value_eth) if price_feed else "n/a"
            dashboard.add_recirculation(recirc.hop_count, recirc.total_value_eth, usd_r)
            if db:
                asyncio.create_task(db.insert_anomaly(AnomalyRecord(
                    chain="ethereum",
                    anomaly_type="RECIRCULATION",
                    severity="high",
                    description=f"{recirc.hop_count} hops | span {recirc.time_span_seconds:.0f}s",
                    value_eth=recirc.total_value_eth,
                    metadata={"path": recirc.path, "tx_hashes": recirc.tx_hashes},
                )))


def on_block(block: RawBlock) -> None:
    dashboard.update_block(block.block_number, block.gas_used)
    if db:
        asyncio.create_task(db.insert_block(BlockRecord(
            chain="ethereum",
            block_number=block.block_number,
            block_hash=block.block_hash,
            parent_hash=block.parent_hash,
            gas_used=block.gas_used,
            gas_limit=block.gas_limit,
            base_fee_wei=int(block.base_fee_hex, 16) if block.base_fee_hex else None,
            miner=block.miner,
        )))


async def main() -> None:
    global price_feed, db

    ws_url = os.getenv("ALCHEMY_WS_URL")
    cmc_key = os.getenv("COINMARKETCAP_API_KEY")
    db_url = os.getenv("DATABASE_URL")

    if not ws_url:
        raise ValueError("ALCHEMY_WS_URL not set in .env")

    if db_url:
        db = Database(dsn=db_url)
        await db.connect()

    if cmc_key:
        price_feed = PriceFeed(api_key=cmc_key)
        await price_feed.start()

    client = AlchemyWebSocket(
        ws_url=ws_url,
        on_transaction=on_transaction,
        on_block=on_block,
    )

    with dashboard.start():
        try:
            await client.start()
        except KeyboardInterrupt:
            await client.stop()
            if price_feed:
                await price_feed.stop()
            if db:
                await db.close()


if __name__ == "__main__":
    asyncio.run(main())
