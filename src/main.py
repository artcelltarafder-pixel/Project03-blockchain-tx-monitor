"""Main entrypoint — Phase 3: anomaly detection + recirculation."""

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

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

TX_COUNT = 0
BLOCK_COUNT = 0
price_feed: PriceFeed | None = None
filter_chain = FilterChain(medium_eth=0.5, large_eth=10.0, whale_eth=100.0)
volume_detector = VolumeAnomalyDetector()
gas_detector = GasAnomalyDetector()
recirc_detector = RecirculationDetector(min_value_eth=1.0)

LEVEL_COLOURS = {
    "critical":  "\033[91m",
    "gas_spike": "\033[38;5;208m",
    "warning":   "\033[93m",
    "info":      "\033[96m",
    "none":      "\033[0m",
}
ANOMALY  = "\033[95m"   # magenta — anomalies
RECIRC   = "\033[91m"   # bright red — recirculation
RESET    = "\033[0m"


def on_transaction(tx: RawTransaction) -> None:
    global TX_COUNT
    TX_COUNT += 1

    result = filter_chain.process(tx)
    if not result:
        return

    # ── Volume anomaly check ──────────────────────────────────────────────
    vol_anomaly = volume_detector.record()
    if vol_anomaly.is_anomaly:
        print(
            f"{ANOMALY}[⚠ ANOMALY] {vol_anomaly.anomaly_type} | "
            f"severity={vol_anomaly.severity.upper()} | "
            f"{vol_anomaly.description} | "
            f"x{vol_anomaly.spike_multiplier:.1f} baseline{RESET}"
        )

    # ── Gas anomaly check ─────────────────────────────────────────────────
    gas_anomaly = gas_detector.record(result.gas.gas_price_gwei)
    if gas_anomaly.is_anomaly:
        print(
            f"{ANOMALY}[⚠ ANOMALY] {gas_anomaly.anomaly_type} | "
            f"severity={gas_anomaly.severity.upper()} | "
            f"{gas_anomaly.description}{RESET}"
        )

    # ── Recirculation check ───────────────────────────────────────────────
    if result.value.value_eth >= 1.0 and tx.from_address and tx.to_address:
        transfer = Transfer(
            tx_hash=tx.tx_hash,
            from_address=tx.from_address,
            to_address=tx.to_address,
            value_eth=result.value.value_eth,
        )
        recirc = recirc_detector.record(transfer)
        if recirc:
            usd = price_feed.eth_to_usd(recirc.total_value_eth) if price_feed else "n/a"
            path_str = " → ".join(f"{a[:8]}…" for a in recirc.path)
            print(
                f"{RECIRC}[🔄 RECIRCULATION DETECTED] "
                f"{recirc.hop_count} hops | "
                f"{recirc.total_value_eth:.2f} ETH ({usd}) | "
                f"span {recirc.time_span_seconds:.0f}s | "
                f"path: {path_str}{RESET}"
            )

    # ── Normal tx display ─────────────────────────────────────────────────
    if result.alert_level == "none":
        return

    usd = price_feed.eth_to_usd(result.value.value_eth) if price_feed else "n/a"
    gas_usd = price_feed.eth_to_usd(result.gas.gas_cost_eth) if price_feed else "n/a"
    colour = LEVEL_COLOURS.get(result.alert_level, RESET)
    tags = " ".join(f"[{t}]" for t in result.tags) if result.tags else ""
    wallet = str(result.from_address)[:14] if result.from_address else "unknown"

    print(
        f"{colour}"
        f"[TX #{TX_COUNT:>4}] {result.tx_hash[:12]}… | "
        f"{result.value.value_eth:>10.4f} ETH ({usd:>14}) | "
        f"fee {result.gas.gas_cost_eth:.6f} ETH ({gas_usd}) | "
        f"{result.gas.gas_price_gwei:.1f} gwei | "
        f"from {wallet}… | "
        f"{tags}"
        f"{RESET}"
    )


def on_block(block: RawBlock) -> None:
    global BLOCK_COUNT
    BLOCK_COUNT += 1
    print(
        f"\n\033[90m[BLOCK #{block.block_number:,}] "
        f"gas used {block.gas_used:>12,} | "
        f"miner {block.miner[:12]}…\033[0m\n"
    )


async def main() -> None:
    global price_feed

    ws_url = os.getenv("ALCHEMY_WS_URL")
    cmc_key = os.getenv("COINMARKETCAP_API_KEY")

    if not ws_url:
        raise ValueError("ALCHEMY_WS_URL not set in .env")

    if cmc_key:
        price_feed = PriceFeed(api_key=cmc_key)
        await price_feed.start()

    logger.info("🔴 Whale  🟠 Gas Spike  🟡 Large TX  🔵 Medium/Contract  🟣 Anomaly  🔴 Recirculation")
    logger.info("Connecting to Alchemy — Ethereum Mainnet\n")

    client = AlchemyWebSocket(
        ws_url=ws_url,
        on_transaction=on_transaction,
        on_block=on_block,
    )

    try:
        await client.start()
    except KeyboardInterrupt:
        await client.stop()
        if price_feed:
            await price_feed.stop()
        logger.info("Stopped. TX seen: %d | Blocks: %d", TX_COUNT, BLOCK_COUNT)


if __name__ == "__main__":
    asyncio.run(main())
