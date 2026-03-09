"""
Standalone demo runner — no API key, no DB, no Docker required.
Replays real Ethereum mainnet transactions with scripted anomaly
and recirculation events for demo purposes.

Usage:
    python3 demo/run_demo.py
    python3 demo/run_demo.py --speed 5
    python3 demo/run_demo.py --mode A --speed 10
"""

from __future__ import annotations

import asyncio
import argparse
import time
from collections import deque
from datetime import datetime, timezone
from typing import Deque

from rich.console import Console
from rich.text import Text
from rich.live import Live
from rich.table import Table
from rich.panel import Panel
from rich import box

from demo.demo_data import DEMO_TRANSACTIONS

ETH_USD = 1932.00
console = Console()

# ── Scripted events ───────────────────────────────────────────────────────────
# Fire at these tx index positions (loops, so mod len(DEMO_TRANSACTIONS))

SCRIPTED_ANOMALIES = {
    8:  ("VOLUME_SPIKE", "medium", "TX rate 9.6/s vs baseline 1.0/s  |  x9.6"),
    22: ("HIGH_GAS",     "high",   "Avg gas 312 gwei vs baseline 18 gwei  |  x17.3"),
    35: ("VOLUME_SPIKE", "high",   "TX rate 13.2/s vs baseline 2.3/s  |  x5.7"),
    50: ("HIGH_GAS",     "medium", "Avg gas 187 gwei vs baseline 22 gwei  |  x8.5"),
    62: ("VOLUME_SPIKE", "critical","TX rate 28.4/s vs baseline 2.1/s  |  x13.5"),
}

SCRIPTED_RECIRCULATIONS = {
    15: {
        "hops": 3,
        "value_eth": 98.2,
        "path": "0x21a31e… → 0xe7f1c1… → 0xf30ba1… → 0x21a31e…",
        "usd": "$189,722.40",
    },
    40: {
        "hops": 4,
        "value_eth": 312.4,
        "path": "0x9696f5… → 0x95ae79… → 0xbea9f7… → 0x6455327… → 0x9696f5…",
        "usd": "$603,356.80",
    },
    58: {
        "hops": 3,
        "value_eth": 49.9,
        "path": "0xdfaa75… → 0x7e2d31… → 0xa9d1e0… → 0xdfaa75…",
        "usd": "$96,388.80",
    },
}

# ── Shared state ──────────────────────────────────────────────────────────────

class DemoState:
    def __init__(self):
        self.tx_count: int = 0
        self.whale_count: int = 0
        self.large_count: int = 0
        self.gas_spike_count: int = 0
        self.anomaly_count: int = 0
        self.recirc_count: int = 0
        self.block_number: int = 21_847_200
        self.avg_gas_gwei: float = 0.0
        self.recent_gas: Deque[float] = deque(maxlen=50)
        self.recent_txs: Deque[dict] = deque(maxlen=12)
        self.recent_events: Deque[dict] = deque(maxlen=6)
        self.start_time: float = time.time()
        self.loop_count: int = 0

    @property
    def tx_per_second(self) -> float:
        elapsed = max(time.time() - self.start_time, 1)
        return round(self.tx_count / elapsed, 2)

    @property
    def uptime(self) -> str:
        secs = int(time.time() - self.start_time)
        h, m, s = secs // 3600, (secs % 3600) // 60, secs % 60
        return f"{h:02d}:{m:02d}:{s:02d}"


state = DemoState()

# ── Classifiers ───────────────────────────────────────────────────────────────

LEVEL_STYLE = {
    "WHALE":     ("bold red",     "🔴 WHALE"),
    "LARGE":     ("bold yellow",  "🟡 LARGE"),
    "GAS_SPIKE": ("bold orange3", "🟠 GAS  "),
    "NORMAL":    ("dim",          "   NORM "),
}

def usd(eth: float) -> str:
    return f"${eth * ETH_USD:>12,.2f}"

# ── Scripted event handlers ───────────────────────────────────────────────────

def handle_scripted(idx: int, mode: str) -> None:
    """Fire scripted anomaly or recirculation at given tx index."""
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")

    if idx in SCRIPTED_ANOMALIES:
        atype, severity, desc = SCRIPTED_ANOMALIES[idx]
        state.anomaly_count += 1

        sev_style = {"critical": "bold red", "high": "bold orange3", "medium": "bold yellow"}.get(severity, "yellow")

        if mode == "A":
            console.print(
                f"[{sev_style}]{ts}  ⚠  ANOMALY  {atype}  [{severity.upper()}]  {desc}[/{sev_style}]"
            )
        else:
            state.recent_events.appendleft({
                "ts": ts,
                "type": "ANOMALY",
                "label": atype,
                "severity": severity.upper(),
                "detail": desc,
                "style": sev_style,
            })

    if idx in SCRIPTED_RECIRCULATIONS:
        r = SCRIPTED_RECIRCULATIONS[idx]
        state.recirc_count += 1

        if mode == "A":
            console.print(
                f"[bold red]{ts}  🔄 RECIRCULATION  {r['hops']} hops  "
                f"{r['value_eth']:.1f} ETH  {r['usd']}[/bold red]"
            )
            console.print(f"[dim]     Path: {r['path']}[/dim]")
        else:
            state.recent_events.appendleft({
                "ts": ts,
                "type": "RECIRC",
                "label": f"🔄 {r['hops']} hops  {r['value_eth']:.1f} ETH",
                "severity": "ALERT",
                "detail": r["path"],
                "style": "bold red",
            })


# ── Mode A — raw scrolling feed ───────────────────────────────────────────────

async def run_mode_a(speed: float) -> None:
    console.print()
    console.print(Panel(
        "[bold cyan]⛓  BLOCKCHAIN TX MONITOR — DEMO MODE[/bold cyan]\n"
        f"[dim]  Replaying {len(DEMO_TRANSACTIONS)} real Ethereum mainnet transactions  "
        f"|  ETH/USD ${ETH_USD:,.2f}  |  Ctrl+C to exit[/dim]",
        box=box.SIMPLE,
    ))

    block_counter = 0
    global_idx = 0

    while True:
        for i, tx in enumerate(DEMO_TRANSACTIONS):
            local_idx = i % len(DEMO_TRANSACTIONS)
            level = tx["label"]
            style, tag = LEVEL_STYLE[level]
            ts = datetime.now(timezone.utc).strftime("%H:%M:%S")

            # Print transaction (skip boring normals unless low volume)
            if level != "NORMAL":
                t = Text()
                t.append(f"{ts}  ", style="dim")
                t.append(f"{tag}  ", style=style)
                t.append(f"{tx['tx_hash'][:14]}…  ", style="dim")
                t.append(f"{tx['value_eth']:>10.2f} ETH  ", style=style)
                t.append(f"{usd(tx['value_eth'])}  ", style="green")
                t.append(f"{tx['gas_price_gwei']:>7.1f} gwei", style="yellow")
                if tx["is_contract"]:
                    t.append("  [CONTRACT]", style="blue")
                console.print(t)

            # Update state
            state.tx_count += 1
            if level == "WHALE":    state.whale_count += 1
            elif level == "LARGE":  state.large_count += 1
            elif level == "GAS_SPIKE": state.gas_spike_count += 1
            state.recent_gas.append(tx["gas_price_gwei"])
            state.avg_gas_gwei = sum(state.recent_gas) / len(state.recent_gas)

            # Fire scripted events
            handle_scripted(local_idx, "A")

            # Block ticker
            block_counter += 1
            if block_counter % 20 == 0:
                state.block_number += 1
                console.print(
                    f"[dim]{ts}  ⛓  Block #{state.block_number:,}  |  "
                    f"{state.tx_count} tx  |  {state.tx_per_second:.1f} tx/s  |  "
                    f"avg gas {state.avg_gas_gwei:.1f} gwei  |  "
                    f"anomalies {state.anomaly_count}  |  recircs {state.recirc_count}[/dim]"
                )

            global_idx += 1
            await asyncio.sleep(speed)

        state.loop_count += 1
        await asyncio.sleep(0.5)


# ── Mode B — rich dashboard ───────────────────────────────────────────────────

def build_dashboard() -> Table:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    # ── Stats bar ─────────────────────────────────────────────────────────────
    stats = Table(box=box.SIMPLE, show_header=False, padding=(0, 2))
    for _ in range(8):
        stats.add_column(justify="center", min_width=13)

    stats.add_row(
        f"[bold cyan]ETH/USD[/bold cyan]\n[bold green]${ETH_USD:,.2f}[/bold green]",
        f"[bold cyan]TOTAL TX[/bold cyan]\n[bold white]{state.tx_count:,}[/bold white]",
        f"[bold cyan]TX/SEC[/bold cyan]\n[bold white]{state.tx_per_second:.1f}[/bold white]",
        f"[bold cyan]BLOCK[/bold cyan]\n[bold white]{state.block_number:,}[/bold white]",
        f"[bold cyan]WHALES[/bold cyan]\n[bold red]{state.whale_count}[/bold red]",
        f"[bold cyan]LARGE TX[/bold cyan]\n[bold yellow]{state.large_count}[/bold yellow]",
        f"[bold cyan]ANOMALIES[/bold cyan]\n[bold magenta]{state.anomaly_count}[/bold magenta]",
        f"[bold cyan]RECIRCS[/bold cyan]\n[bold red]{state.recirc_count}[/bold red]",
    )

    # ── TX feed ───────────────────────────────────────────────────────────────
    feed = Table(
        title="[bold cyan]LIVE TRANSACTION FEED[/bold cyan]",
        box=box.SIMPLE_HEAD,
        show_header=True,
        header_style="bold cyan",
        padding=(0, 1),
        expand=True,
    )
    feed.add_column("TIME",   width=10)
    feed.add_column("HASH",   width=18)
    feed.add_column("ETH",    justify="right", width=14)
    feed.add_column("USD",    justify="right", width=16)
    feed.add_column("GWEI",   justify="right", width=10)
    feed.add_column("TYPE",   width=12)
    feed.add_column("FROM",   width=16)

    for t in list(state.recent_txs):
        level = t["level"]
        style, tag = LEVEL_STYLE[level]
        feed.add_row(
            t["ts"],
            t["hash"][:16] + "…",
            f"{t['value_eth']:>12.2f}",
            usd(t["value_eth"]),
            f"{t['gas_gwei']:>8.1f}",
            tag,
            t["from"][:14] + "…",
            style=style if level != "NORMAL" else "dim",
        )

    # ── Events panel ──────────────────────────────────────────────────────────
    events = Table(
        title="[bold cyan]ANOMALIES & RECIRCULATIONS[/bold cyan]",
        box=box.SIMPLE_HEAD,
        show_header=True,
        header_style="bold cyan",
        padding=(0, 1),
        expand=True,
    )
    events.add_column("TIME",     width=10)
    events.add_column("TYPE",     width=12)
    events.add_column("EVENT",    width=20)
    events.add_column("SEV",      width=10)
    events.add_column("DETAIL",   min_width=30)

    for e in list(state.recent_events):
        events.add_row(
            e["ts"],
            e["type"],
            e["label"],
            e["severity"],
            e["detail"],
            style=e["style"],
        )

    if not state.recent_events:
        events.add_row("—", "—", "—", "—", "[dim]Monitoring…[/dim]")

    # ── Outer frame ───────────────────────────────────────────────────────────
    outer = Table(box=box.ROUNDED, expand=True, show_header=False, padding=(0, 1))
    outer.add_column()
    outer.add_row(
        f"[bold cyan]⛓  BLOCKCHAIN TX MONITOR[/bold cyan]  "
        f"[dim]DEMO  |  Uptime {state.uptime}  |  avg gas {state.avg_gas_gwei:.1f} gwei  |  {now}[/dim]"
    )
    outer.add_row(stats)
    outer.add_row(feed)
    outer.add_row(events)
    outer.add_row(
        f"[dim]  {len(DEMO_TRANSACTIONS)} real mainnet txs  |  "
        f"Loops: {state.loop_count}  |  Ctrl+C to exit[/dim]"
    )
    return outer


async def run_mode_b(speed: float) -> None:
    block_counter = 0

    with Live(build_dashboard(), console=console, refresh_per_second=4, screen=True) as live:
        while True:
            for i, tx in enumerate(DEMO_TRANSACTIONS):
                local_idx = i % len(DEMO_TRANSACTIONS)
                level = tx["label"]
                ts = datetime.now(timezone.utc).strftime("%H:%M:%S")

                state.tx_count += 1
                state.recent_gas.append(tx["gas_price_gwei"])
                state.avg_gas_gwei = sum(state.recent_gas) / len(state.recent_gas)

                if level == "WHALE":      state.whale_count += 1
                elif level == "LARGE":    state.large_count += 1
                elif level == "GAS_SPIKE": state.gas_spike_count += 1

                state.recent_txs.appendleft({
                    "ts":        ts,
                    "hash":      tx["tx_hash"],
                    "value_eth": tx["value_eth"],
                    "gas_gwei":  tx["gas_price_gwei"],
                    "from":      tx["from"],
                    "level":     level,
                })

                block_counter += 1
                if block_counter % 20 == 0:
                    state.block_number += 1

                handle_scripted(local_idx, "B")
                live.update(build_dashboard())
                await asyncio.sleep(speed)

            state.loop_count += 1
            await asyncio.sleep(0.3)


# ── Entrypoint ────────────────────────────────────────────────────────────────

async def main() -> None:
    parser = argparse.ArgumentParser(description="Blockchain TX Monitor — Demo Mode")
    parser.add_argument("--mode",  choices=["A", "B"], default=None)
    parser.add_argument("--speed", type=str,           default=None)
    args = parser.parse_args()

    SPEED_MAP = {"1": 1.0, "2": 0.5, "5": 0.2, "10": 0.05}

    if args.mode:
        mode = args.mode.upper()
    else:
        console.print()
        console.print("  [bold cyan]⛓  BLOCKCHAIN TX MONITOR — DEMO[/bold cyan]")
        console.print()
        console.print("  [bold cyan]A[/bold cyan]  —  Raw feed    [dim](scrolling log)[/dim]")
        console.print("  [bold cyan]B[/bold cyan]  —  Dashboard   [dim](structured UI)[/dim]")
        console.print()
        mode = ""
        while mode not in ("A", "B"):
            mode = console.input("  [bold]>[/bold] ").strip().upper()

    if args.speed and args.speed in SPEED_MAP:
        speed = SPEED_MAP[args.speed]
    else:
        console.print()
        console.print("  [cyan]1[/cyan] 2 [cyan]5[/cyan] 10  — replay speed")
        console.print()
        speed_in = ""
        while speed_in not in SPEED_MAP:
            speed_in = console.input("  [bold]Speed[/bold] [dim][default: 5][/dim]: ").strip() or "5"
        speed = SPEED_MAP[speed_in]

    try:
        if mode == "A":
            await run_mode_a(speed)
        else:
            await run_mode_b(speed)
    except KeyboardInterrupt:
        console.print("\n[dim]  Demo stopped.[/dim]")


if __name__ == "__main__":
    asyncio.run(main())
