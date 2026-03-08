"""Recirculation detector — identifies circular fund flows across transaction hops."""

from __future__ import annotations

import hashlib
import time
from collections import defaultdict
from dataclasses import dataclass, field


@dataclass
class Transfer:
    tx_hash: str
    from_address: str
    to_address: str
    value_eth: float
    timestamp: float = field(default_factory=time.time)


@dataclass
class RecirculationResult:
    detected: bool
    path: list[str]         # wallet addresses in the cycle
    tx_hashes: list[str]
    total_value_eth: float
    hop_count: int
    time_span_seconds: float
    path_hash: str          # unique ID for this cycle


class RecirculationDetector:
    """
    Tracks ETH flows between wallets within a time window.
    Detects cycles: A -> B -> C -> A (funds returning to origin).

    Uses DFS graph traversal to find cycles in the flow graph.
    """

    def __init__(
        self,
        time_window_seconds: int = 3600,
        max_hop_depth: int = 5,
        min_value_eth: float = 1.0,
    ) -> None:
        self._window = time_window_seconds
        self._max_depth = max_hop_depth
        self._min_value = min_value_eth
        # adjacency: from_address -> list of Transfer
        self._graph: dict[str, list[Transfer]] = defaultdict(list)
        self._detected_paths: set[str] = set()

    def record(self, transfer: Transfer) -> RecirculationResult | None:
        """Record a transfer and check if it completes a cycle."""
        if transfer.value_eth < self._min_value:
            return None

        self._prune_old_transfers()
        self._graph[transfer.from_address].append(transfer)

        # Check if this new transfer completes a cycle back to its origin
        cycle = self._find_cycle(
            start=transfer.from_address,
            current=transfer.to_address,
            path_addresses=[transfer.from_address],
            path_txs=[transfer.tx_hash],
            path_values=[transfer.value_eth],
            depth=0,
            start_time=transfer.timestamp,
        )

        return cycle

    def _find_cycle(
        self,
        start: str,
        current: str,
        path_addresses: list[str],
        path_txs: list[str],
        path_values: list[float],
        depth: int,
        start_time: float,
    ) -> RecirculationResult | None:
        if depth > self._max_depth:
            return None

        # Cycle found — current node has a path back to start
        outgoing = self._graph.get(current, [])
        for transfer in outgoing:
            if transfer.to_address == start and len(path_addresses) >= 2:
                full_path = path_addresses + [current, start]
                full_txs = path_txs + [transfer.tx_hash]
                path_hash = self._hash_path(full_path)

                if path_hash in self._detected_paths:
                    return None  # Already reported this cycle

                self._detected_paths.add(path_hash)
                time_span = transfer.timestamp - start_time

                return RecirculationResult(
                    detected=True,
                    path=full_path,
                    tx_hashes=full_txs,
                    total_value_eth=sum(path_values) + transfer.value_eth,
                    hop_count=len(full_path) - 1,
                    time_span_seconds=time_span,
                    path_hash=path_hash,
                )

            # Continue DFS if not revisiting
            if transfer.to_address not in path_addresses:
                result = self._find_cycle(
                    start=start,
                    current=transfer.to_address,
                    path_addresses=path_addresses + [current],
                    path_txs=path_txs + [transfer.tx_hash],
                    path_values=path_values + [transfer.value_eth],
                    depth=depth + 1,
                    start_time=start_time,
                )
                if result:
                    return result

        return None

    def _prune_old_transfers(self) -> None:
        now = time.time()
        for addr in list(self._graph.keys()):
            self._graph[addr] = [
                t for t in self._graph[addr]
                if now - t.timestamp <= self._window
            ]
            if not self._graph[addr]:
                del self._graph[addr]

    @staticmethod
    def _hash_path(path: list[str]) -> str:
        return hashlib.sha256("->".join(path).encode()).hexdigest()[:16]
