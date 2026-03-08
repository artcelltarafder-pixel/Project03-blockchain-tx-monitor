"""Anomaly detection — volume spikes and unusual transaction patterns."""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass


@dataclass
class AnomalyResult:
    is_anomaly: bool
    anomaly_type: str | None
    severity: str           # 'low' | 'medium' | 'high' | 'critical'
    description: str
    spike_multiplier: float = 1.0


class VolumeAnomalyDetector:
    """
    Detects transaction volume spikes by comparing a short window
    against a longer baseline window.
    """

    def __init__(
        self,
        spike_window_seconds: int = 60,
        baseline_window_seconds: int = 600,
        spike_multiplier: float = 5.0,
    ) -> None:
        self._spike_window = spike_window_seconds
        self._baseline_window = baseline_window_seconds
        self._spike_multiplier = spike_multiplier
        # deque of timestamps
        self._events: deque[float] = deque()
        self._last_alert_time: float = 0
        self._alert_cooldown: float = 60.0

    def record(self) -> AnomalyResult:
        """Record a new transaction event and check for anomaly."""
        now = time.time()
        self._events.append(now)

        # Prune events outside baseline window
        while self._events and now - self._events[0] > self._baseline_window:
            self._events.popleft()

        # Count events in each window
        spike_count = sum(1 for t in self._events if now - t <= self._spike_window)
        baseline_count = len(self._events)

        if baseline_count == 0:
            return AnomalyResult(False, None, "low", "Insufficient data")

        # Normalise to per-second rates
        spike_rate = spike_count / self._spike_window
        baseline_rate = baseline_count / self._baseline_window

        if baseline_rate == 0:
            return AnomalyResult(False, None, "low", "Baseline not established")

        multiplier = spike_rate / baseline_rate

        if multiplier >= self._spike_multiplier:
            # Cooldown check — don't fire repeatedly
            if now - self._last_alert_time < self._alert_cooldown:
                return AnomalyResult(False, None, "low", "Cooldown active")

            self._last_alert_time = now

            if multiplier >= self._spike_multiplier * 2:
                severity = "critical"
            elif multiplier >= self._spike_multiplier * 1.5:
                severity = "high"
            else:
                severity = "medium"

            return AnomalyResult(
                is_anomaly=True,
                anomaly_type="VOLUME_SPIKE",
                severity=severity,
                description=f"TX rate {spike_rate:.1f}/s vs baseline {baseline_rate:.1f}/s",
                spike_multiplier=multiplier,
            )

        return AnomalyResult(False, None, "low", "Normal volume")


class GasAnomalyDetector:
    """Detects sustained high gas periods — different from single tx gas spikes."""

    def __init__(
        self,
        window_seconds: int = 120,
        high_gwei_threshold: float = 200.0,
        min_sample_count: int = 10,
    ) -> None:
        self._window = window_seconds
        self._threshold = high_gwei_threshold
        self._min_samples = min_sample_count
        self._samples: deque[tuple[float, float]] = deque()  # (ts, gwei)
        self._last_alert_time: float = 0
        self._alert_cooldown: float = 120.0

    def record(self, gas_price_gwei: float) -> AnomalyResult:
        now = time.time()
        if gas_price_gwei > 0:
            self._samples.append((now, gas_price_gwei))

        while self._samples and now - self._samples[0][0] > self._window:
            self._samples.popleft()

        if len(self._samples) < self._min_samples:
            return AnomalyResult(False, None, "low", "Insufficient samples")

        avg_gwei = sum(g for _, g in self._samples) / len(self._samples)

        if avg_gwei >= self._threshold:
            if now - self._last_alert_time < self._alert_cooldown:
                return AnomalyResult(False, None, "low", "Cooldown active")
            self._last_alert_time = now
            return AnomalyResult(
                is_anomaly=True,
                anomaly_type="HIGH_GAS_PERIOD",
                severity="high",
                description=f"Avg gas {avg_gwei:.1f} gwei over last {self._window}s",
                spike_multiplier=avg_gwei / 20.0,
            )

        return AnomalyResult(False, None, "low", f"Avg gas {avg_gwei:.1f} gwei — normal")
