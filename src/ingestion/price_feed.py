"""CoinMarketCap price feed — fetches ETH/USD every 60 seconds."""

from __future__ import annotations

import asyncio
import logging

import aiohttp

logger = logging.getLogger(__name__)

CMC_URL = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/quotes/latest"
ETH_CMC_ID = 1027  # Ethereum


class PriceFeed:
    def __init__(self, api_key: str, refresh_interval: int = 60) -> None:
        self._api_key = api_key
        self._refresh_interval = refresh_interval
        self._eth_usd: float | None = None
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        await self._fetch()  # fetch immediately on start
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()

    @property
    def eth_usd(self) -> float | None:
        return self._eth_usd

    def eth_to_usd(self, eth: float) -> str:
        if self._eth_usd is None:
            return "n/a"
        return f"${eth * self._eth_usd:,.2f}"

    async def _fetch(self) -> None:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    CMC_URL,
                    headers={"X-CMC_PRO_API_KEY": self._api_key},
                    params={"id": str(ETH_CMC_ID), "convert": "USD"},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    data = await resp.json()
                    price = data["data"][str(ETH_CMC_ID)]["quote"]["USD"]["price"]
                    self._eth_usd = float(price)
                    logger.info("ETH/USD updated: $%.2f", self._eth_usd)
        except Exception as exc:
            logger.warning("Price fetch failed: %s", exc)

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self._refresh_interval)
            await self._fetch()
