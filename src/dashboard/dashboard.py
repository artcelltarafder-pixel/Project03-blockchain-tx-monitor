"""Rich terminal dashboard — live blockchain monitor UI."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime

from rich import box
from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

# ── Data store ────────────────────────────────────────────────────────────────

@dataclass
class DashboardState:
    eth_usd: float = 0.0
    latest_block: int = 0
    latest_block_gas: int = 0
    tx_total: int = 0
    tx_per_second: float = 0.0
    whale_count: int = 0
    large_count: int = 0
    anomaly_count: int = 0
    recirc_count: int = 0
    avg_gas_gwei: float = 0.0
    private_tx_count: int = 0

    # Rolling window for tx/s calculation
    tx_timestamps: deque = field(default_factory=lambda: deque(maxlen=1000))

    # Live feed — last 20 notable transactions
    recent_txs: deque = field(default_factory=lambda: deque(maxlen=20))

    # Anomaly/recirculation log — last 10 events
    event_log: deque = field(default_factory=lambda: deque(maxlen=10))

    def record_tx(self) -> None:
        import time
        self.tx_total += 1
        now = time.time()
        self.tx_timestamps.append(now)
        # Calculate tx/s over last 10 seconds
        recent = [t for t in self.tx_timestamps if now - t <= 10]
        self.tx_per_second = len(recent) / 10.0

    def add_tx(self, tx_data: dict) -> None:
        self.recent_txs.append(tx_data)

    def add_event(self, event: dict) -> None:
        self.event_log.append(event)


# ── Layout builders ───────────────────────────────────────────────────────────

def build_header(state: DashboardState) -> Panel:
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    text = Text()
    text.append("⛓  BLOCKCHAIN TX MONITOR", style="bold cyan")
    text.append("   │   ", style="dim")
    text.append("BLOCK ", style="dim")
    text.append(f"#{state.latest_block:,}", style="bold white")
    text.append("   │   ", style="dim")
    text.append("ETH ", style="dim")
    text.append(f"${state.eth_usd:,.2f}", style="bold green")
    text.append("   │   ", style="dim")
    text.append(f"{state.tx_per_second:.1f} tx/s", style="bold yellow")
    text.append("   │   ", style="dim")
    text.append(now, style="dim")
    return Panel(text, style="bold", box=box.HORIZONTALS)


def build_tx_table(state: DashboardState) -> Panel:
    table = Table(
        box=box.SIMPLE,
        show_header=True,
        header_style="bold cyan",
        expand=True,
        padding=(0, 1),
    )
    table.add_column("TX Hash", style="dim", width=14)
    table.add_column("ETH", justify="right", width=12)
    table.add_column("USD", justify="right", width=14)
    table.add_column("Fee ETH", justify="right", width=10)
    table.add_column("Gwei", justify="right", width=6)
    table.add_column("From", width=16)
    table.add_column("Tags", width=28)

    for tx in reversed(list(state.recent_txs)):
        level = tx.get("level", "info")
        if level == "critical":
            row_style = "bold red"
        elif level == "gas_spike":
            row_style = "bold orange3"
        elif level == "warning":
            row_style = "bold yellow"
        else:
            row_style = "cyan"

        tags = tx.get("tags", "")
        table.add_row(
            tx.get("hash", "")[:12] + "…",
            f"{tx.get('eth', 0):.4f}",
            tx.get("usd", "n/a"),
            f"{tx.get('fee_eth', 0):.6f}",
            f"{tx.get('gwei', 0):.1f}",
            (tx.get("from", "")[:14] + "…") if tx.get("from") else "unknown",
            tags,
            style=row_style,
        )

    return Panel(
        table,
        title="[bold cyan]LIVE TRANSACTIONS[/bold cyan]",
        border_style="cyan",
        box=box.ROUNDED,
    )


def build_stats(state: DashboardState) -> Panel:
    table = Table(box=box.SIMPLE, show_header=False, expand=True, padding=(0, 1))
    table.add_column("Metric", style="dim", width=18)
    table.add_column("Value", style="bold white")

    table.add_row("Total TX Seen", f"{state.tx_total:,}")
    table.add_row("TX / Second", f"{state.tx_per_second:.1f}")
    table.add_row("Latest Block", f"#{state.latest_block:,}")
    table.add_row("Block Gas Used", f"{state.latest_block_gas:,}")
    table.add_row("Avg Gas", f"{state.avg_gas_gwei:.1f} gwei")
    table.add_row("", "")
    table.add_row("🐋 Whales (>100 ETH)", f"[bold red]{state.whale_count}[/bold red]")
    table.add_row("🔴 Large (>10 ETH)", f"[yellow]{state.large_count}[/yellow]")
    table.add_row("🔒 Private TX", f"[dim]{state.private_tx_count}[/dim]")
    table.add_row("", "")
    table.add_row("⚠  Anomalies", f"[magenta]{state.anomaly_count}[/magenta]")
    table.add_row("🔄 Recirculations", f"[bold red]{state.recirc_count}[/bold red]")

    return Panel(
        table,
        title="[bold cyan]STATS[/bold cyan]",
        border_style="cyan",
        box=box.ROUNDED,
    )


def build_event_log(state: DashboardState) -> Panel:
    table = Table(box=box.SIMPLE, show_header=False, expand=True, padding=(0, 1))
    table.add_column("Time", style="dim", width=10)
    table.add_column("Event", style="bold")
    table.add_column("Detail")

    for event in reversed(list(state.event_log)):
        etype = event.get("type", "")
        detail = event.get("detail", "")
        ts = event.get("time", "")

        if "RECIRC" in etype:
            style = "bold red"
        elif "ANOMALY" in etype:
            style = "bold magenta"
        elif "WHALE" in etype:
            style = "bold red"
        else:
            style = "yellow"

        table.add_row(ts, etype, detail, style=style)

    return Panel(
        table,
        title="[bold magenta]ANOMALY & EVENT LOG[/bold magenta]",
        border_style="magenta",
        box=box.ROUNDED,
    )


def build_layout(state: DashboardState) -> Layout:
    layout = Layout()
    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="body"),
        Layout(name="footer", size=8),
    )
    layout["body"].split_row(
        Layout(name="transactions", ratio=3),
        Layout(name="stats", ratio=1),
    )
    layout["header"].update(build_header(state))
    layout["body"]["transactions"].update(build_tx_table(state))
    layout["body"]["stats"].update(build_stats(state))
    layout["footer"].update(build_event_log(state))
    return layout


# ── Dashboard runner ──────────────────────────────────────────────────────────

class Dashboard:
    def __init__(self) -> None:
        self.state = DashboardState()
        self._console = Console()
        self._live: Live | None = None

    def start(self) -> Live:
        self._live = Live(
            build_layout(self.state),
            console=self._console,
            refresh_per_second=2,
            screen=True,
        )
        return self._live

    def update(self) -> None:
        if self._live:
            self._live.update(build_layout(self.state))

    def add_transaction(self, tx_data: dict) -> None:
        self.state.record_tx()
        level = tx_data.get("level", "none")
        if level != "none":
            self.state.add_tx(tx_data)
            tags = tx_data.get("tags", [])
            if "WHALE" in tags:
                self.state.whale_count += 1
                self.state.add_event({
                    "type": "🐋 WHALE TX",
                    "detail": f"{tx_data.get('eth', 0):.2f} ETH ({tx_data.get('usd', 'n/a')})",
                    "time": datetime.now(UTC).strftime("%H:%M:%S"),
                })
            if "LARGE_TX" in tags:
                self.state.large_count += 1
            if "PRIVATE_TX" in tags:
                self.state.private_tx_count += 1
        self.update()

    def add_anomaly(self, anomaly_type: str, description: str) -> None:
        self.state.anomaly_count += 1
        self.state.add_event({
            "type": f"⚠ {anomaly_type}",
            "detail": description,
            "time": datetime.now(UTC).strftime("%H:%M:%S"),
        })
        self.update()

    def add_recirculation(self, hops: int, value_eth: float, usd: str) -> None:
        self.state.recirc_count += 1
        self.state.add_event({
            "type": "🔄 RECIRCULATION",
            "detail": f"{hops} hops | {value_eth:.2f} ETH ({usd})",
            "time": datetime.now(UTC).strftime("%H:%M:%S"),
        })
        self.update()

    def update_block(self, block_number: int, gas_used: int) -> None:
        self.state.latest_block = block_number
        self.state.latest_block_gas = gas_used
        self.update()

    def update_price(self, eth_usd: float) -> None:
        self.state.eth_usd = eth_usd
        self.update()

    def update_gas(self, gwei: float) -> None:
        # Rolling average approximation
        if self.state.avg_gas_gwei == 0:
            self.state.avg_gas_gwei = gwei
        else:
            self.state.avg_gas_gwei = (self.state.avg_gas_gwei * 0.95) + (gwei * 0.05)
        self.update()
