"""Prometheus metrics exporter for the TX monitor."""

from __future__ import annotations

import threading

from prometheus_client import Counter, Gauge, Histogram, start_http_server

# ── Counters ──────────────────────────────────────────────────────────────────
TX_TOTAL = Counter("txmonitor_transactions_total", "Total transactions seen", ["chain"])
TX_FILTERED = Counter("txmonitor_transactions_filtered_total", "Filtered transactions by level", ["level"])
WHALE_TOTAL = Counter("txmonitor_whale_transactions_total", "Whale transactions (>100 ETH)")
LARGE_TOTAL = Counter("txmonitor_large_transactions_total", "Large transactions (>10 ETH)")
PRIVATE_TOTAL = Counter("txmonitor_private_transactions_total", "Private/zero-gas transactions")
ANOMALY_TOTAL = Counter("txmonitor_anomalies_total", "Anomalies detected", ["type"])
RECIRC_TOTAL = Counter("txmonitor_recirculations_total", "Recirculation patterns detected")
BLOCK_TOTAL = Counter("txmonitor_blocks_total", "Blocks processed", ["chain"])

# ── Gauges ────────────────────────────────────────────────────────────────────
ETH_PRICE = Gauge("txmonitor_eth_usd_price", "Current ETH/USD price")
TX_PER_SECOND = Gauge("txmonitor_tx_per_second", "Transactions per second")
AVG_GAS_GWEI = Gauge("txmonitor_avg_gas_gwei", "Rolling average gas price in gwei")
LATEST_BLOCK = Gauge("txmonitor_latest_block", "Latest block number", ["chain"])
DB_TX_COUNT = Gauge("txmonitor_db_transaction_count", "Total transactions in DB")

# ── Histograms ────────────────────────────────────────────────────────────────
TX_VALUE_ETH = Histogram(
    "txmonitor_transaction_value_eth",
    "Transaction value distribution in ETH",
    buckets=[0.1, 0.5, 1, 5, 10, 50, 100, 500, 1000],
)
GAS_PRICE_GWEI = Histogram(
    "txmonitor_gas_price_gwei",
    "Gas price distribution in gwei",
    buckets=[1, 5, 10, 20, 50, 100, 200, 500],
)


def start_metrics_server(port: int = 8000) -> None:
    """Start Prometheus metrics HTTP server in background thread."""
    thread = threading.Thread(
        target=start_http_server,
        args=(port,),
        daemon=True,
    )
    thread.start()
