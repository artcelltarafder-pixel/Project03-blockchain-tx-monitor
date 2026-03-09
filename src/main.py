"""Main entrypoint — Mode A (raw feed) / Mode B (dashboard)."""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import UTC, datetime

from dotenv import load_dotenv
from rich.console import Console
from rich.prompt import Prompt
from rich.text import Text

from src.analysis.anomaly import GasAnomalyDetector, VolumeAnomalyDetector
from src.analysis.recirculation import RecirculationDetector, Transfer
from src.dashboard.dashboard import Dashboard
from src.filters.filter_chain import FilterChain
from src.ingestion.alchemy_ws import AlchemyWebSocket, RawBlock, RawTransaction
from src.ingestion.price_feed import PriceFeed
from src.metrics.metrics import (
    ANOMALY_TOTAL,
    AVG_GAS_GWEI,
    BLOCK_TOTAL,
    ETH_PRICE,
    GAS_PRICE_GWEI,
    LARGE_TOTAL,
    LATEST_BLOCK,
    PRIVATE_TOTAL,
    RECIRC_TOTAL,
    TX_FILTERED,
    TX_PER_SECOND,
    TX_TOTAL,
    TX_VALUE_ETH,
    WHALE_TOTAL,
    start_metrics_server,
)
from src.storage.db import AnomalyRecord, BlockRecord, Database, TransactionRecord

load_dotenv()
logging.basicConfig(level=logging.WARNING)

console = Console()
price_feed: PriceFeed | None = None
db: Database | None = None
filter_chain = FilterChain(medium_eth=0.5, large_eth=10.0, whale_eth=100.0)
volume_detector = VolumeAnomalyDetector()
gas_detector = GasAnomalyDetector()
recirc_detector = RecirculationDetector(min_value_eth=1.0)
dashboard = Dashboard()

# Active display mode
DISPLAY_MODE: str = "B"


# ── Mode A — Raw feed output ──────────────────────────────────────────────────

LEVEL_STYLES = {
    "critical": ("bold red",    "🔴"),
    "gas_spike": ("bold orange3","🟠"),
    "warning":   ("bold yellow", "🟡"),
    "info":      ("cyan",        "🔵"),
    "none":      ("dim",         "  "),
}

def raw_print_tx(tx: RawTransaction, result) -> None:
    style, icon = LEVEL_STYLES.get(result.alert_level, ("dim", "  "))
    usd = price_feed.eth_to_usd(result.value.value_eth) if price_feed else "n/a"
    tags = " ".join(f"[{t}]" for t in result.tags)
    ts = datetime.now(UTC).strftime("%H:%M:%S")

    text = Text()
    text.append(f"{ts} ", style="dim")
    text.append(f"{icon} ", style=style)
    text.append(f"{tx.tx_hash[:12]}…  ", style="dim")
    text.append(f"{result.value.value_eth:>10.4f} ETH  ", style=style)
    text.append(f"{usd:>12}  ", style="green")
    text.append(f"{result.gas.gas_price_gwei:>6.1f} gwei  ", style="yellow")
    text.append(tags, style=style)
    console.print(text)


def raw_print_anomaly(anomaly_type: str, description: str) -> None:
    ts = datetime.now(UTC).strftime("%H:%M:%S")
    console.print(f"[bold magenta]{ts}  ⚠  {anomaly_type}  {description}[/bold magenta]")


def raw_print_recirc(hops: int, value_eth: float, usd: str) -> None:
    ts = datetime.now(UTC).strftime("%H:%M:%S")
    console.print(
        f"[bold red]{ts}  🔄 RECIRCULATION  {hops} hops  {value_eth:.2f} ETH  {usd}[/bold red]"
    )


def raw_print_block(block_number: int) -> None:
    ts = datetime.now(UTC).strftime("%H:%M:%S")
    console.print(f"[dim]{ts}  ⛓  Block #{block_number:,}[/dim]")


# ── Shared pipeline ───────────────────────────────────────────────────────────

def on_transaction(tx: RawTransaction) -> None:
    result = filter_chain.process(tx)
    if not result:
        return

    # ── Prometheus ────────────────────────────────────────────────────────
    TX_TOTAL.labels(chain="ethereum").inc()
    TX_FILTERED.labels(level=result.alert_level).inc()
    TX_VALUE_ETH.observe(result.value.value_eth)
    if result.gas.gas_price_gwei > 0:
        GAS_PRICE_GWEI.observe(result.gas.gas_price_gwei)
        AVG_GAS_GWEI.set(result.gas.gas_price_gwei)
    if "WHALE" in result.tags:
        WHALE_TOTAL.inc()
    if "LARGE_TX" in result.tags:
        LARGE_TOTAL.inc()
    if "PRIVATE_TX" in result.tags:
        PRIVATE_TOTAL.inc()

    if price_feed and price_feed.eth_usd:
        ETH_PRICE.set(price_feed.eth_usd)

    usd = price_feed.eth_to_usd(result.value.value_eth) if price_feed else "n/a"

    # ── Display ───────────────────────────────────────────────────────────
    if DISPLAY_MODE == "A":
        if result.alert_level != "none":
            raw_print_tx(tx, result)
    else:
        if price_feed and price_feed.eth_usd:
            dashboard.update_price(price_feed.eth_usd)
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
        TX_PER_SECOND.set(dashboard.state.tx_per_second)
        if result.gas.gas_price_gwei > 0:
            dashboard.update_gas(result.gas.gas_price_gwei)

    # ── DB ────────────────────────────────────────────────────────────────
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

    # ── Anomaly ───────────────────────────────────────────────────────────
    vol_anomaly = volume_detector.record()
    if vol_anomaly.is_anomaly:
        ANOMALY_TOTAL.labels(type="VOLUME_SPIKE").inc()
        desc = f"{vol_anomaly.description} | x{vol_anomaly.spike_multiplier:.1f}"
        if DISPLAY_MODE == "A":
            raw_print_anomaly(vol_anomaly.anomaly_type or "VOLUME_SPIKE", desc)
        else:
            dashboard.add_anomaly(vol_anomaly.anomaly_type or "VOLUME_SPIKE", desc)
        if db:
            asyncio.create_task(db.insert_anomaly(AnomalyRecord(
                chain="ethereum",
                anomaly_type=vol_anomaly.anomaly_type or "VOLUME_SPIKE",
                severity=vol_anomaly.severity,
                description=vol_anomaly.description,
            )))

    gas_anomaly = gas_detector.record(result.gas.gas_price_gwei)
    if gas_anomaly.is_anomaly:
        ANOMALY_TOTAL.labels(type="HIGH_GAS").inc()
        if DISPLAY_MODE == "A":
            raw_print_anomaly(gas_anomaly.anomaly_type or "HIGH_GAS", gas_anomaly.description)
        else:
            dashboard.add_anomaly(gas_anomaly.anomaly_type or "HIGH_GAS", gas_anomaly.description)

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
            RECIRC_TOTAL.inc()
            usd_r = price_feed.eth_to_usd(recirc.total_value_eth) if price_feed else "n/a"
            if DISPLAY_MODE == "A":
                raw_print_recirc(recirc.hop_count, recirc.total_value_eth, usd_r)
            else:
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
    BLOCK_TOTAL.labels(chain="ethereum").inc()
    LATEST_BLOCK.labels(chain="ethereum").set(block.block_number)
    if DISPLAY_MODE == "A":
        raw_print_block(block.block_number)
    else:
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


# ── Startup prompt ────────────────────────────────────────────────────────────

def select_mode() -> str:
    console.print()
    console.print("  [bold cyan]⛓  BLOCKCHAIN TX MONITOR[/bold cyan]")
    console.print()
    console.print("  [bold]Select display mode:[/bold]")
    console.print()
    console.print("  [bold cyan]A[/bold cyan]  —  Raw feed    [dim](live coloured log stream)[/dim]")
    console.print("  [bold cyan]B[/bold cyan]  —  Dashboard   [dim](structured terminal UI)[/dim]")
    console.print()

    while True:
        choice = Prompt.ask("  [bold]>[/bold]", default="B").strip().upper()
        if choice in ("A", "B"):
            return choice
        console.print("  [red]Enter A or B[/red]")


# ── Main ──────────────────────────────────────────────────────────────────────

async def main() -> None:
    global DISPLAY_MODE, price_feed, db

    DISPLAY_MODE = select_mode()

    ws_url = os.getenv("ALCHEMY_WS_URL")
    cmc_key = os.getenv("COINMARKETCAP_API_KEY")
    db_url = os.getenv("DATABASE_URL")
    metrics_port = int(os.getenv("PROMETHEUS_PORT", "8000"))

    if not ws_url:
        raise ValueError("ALCHEMY_WS_URL not set in .env")

    start_metrics_server(port=metrics_port)

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

    if DISPLAY_MODE == "A":
        console.print()
        console.print("[bold cyan]── Raw Feed Mode ─────────────────────────────────────[/bold cyan]")
        console.print("[dim]  Ctrl+C to exit[/dim]")
        console.print()
        try:
            await client.start()
        except KeyboardInterrupt:
            await client.stop()
            if price_feed:
                await price_feed.stop()
            if db:
                await db.close()
    else:
        console.print()
        console.print("[dim]  Loading dashboard...[/dim]")
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
