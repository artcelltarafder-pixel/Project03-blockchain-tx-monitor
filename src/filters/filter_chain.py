"""Filter chain — runs a raw transaction through all filters, returns enriched result."""

from __future__ import annotations

from dataclasses import dataclass, field

from src.filters.contract_filter import ContractFilter, ContractFilterResult
from src.filters.gas_filter import GasFilter, GasFilterResult
from src.filters.value_filter import ValueFilter, ValueFilterResult
from src.ingestion.alchemy_ws import RawTransaction


@dataclass
class FilteredTransaction:
    tx_hash: str
    from_address: str | None
    to_address: str | None
    value: ValueFilterResult
    gas: GasFilterResult
    contract: ContractFilterResult
    tags: list[str] = field(default_factory=list)
    alert_level: str = "none"   # 'none' | 'info' | 'warning' | 'gas_spike' | 'critical'


class FilterChain:
    def __init__(
        self,
        medium_eth: float = 0.5,
        large_eth: float = 10.0,
        whale_eth: float = 100.0,
        gas_spike_multiplier: float = 3.0,
    ) -> None:
        self._value = ValueFilter(medium_eth, large_eth, whale_eth)
        self._gas = GasFilter(spike_multiplier=gas_spike_multiplier)
        self._contract = ContractFilter()

    def process(self, tx: RawTransaction) -> FilteredTransaction | None:
        value_result = self._value.check(tx.value_hex)
        gas_result = self._gas.check(tx.gas_price_hex, tx.gas_hex)
        contract_result = self._contract.check(tx.input_data, tx.to_address)

        # Collect all tags
        tags = list(contract_result.tags)
        if value_result.is_whale:
            tags.append("WHALE")
        if value_result.is_large:
            tags.append("LARGE_TX")
        if gas_result.is_spike:
            tags.append("GAS_SPIKE")
            # Private transaction detection
        if gas_result.gas_price_gwei == 0 and value_result.value_eth > 0:
            tags.append("PRIVATE_TX")

        # Determine alert level — priority order
        if value_result.is_whale:
            alert_level = "critical"
        elif gas_result.is_spike:
            alert_level = "gas_spike"
        elif value_result.is_large:
            alert_level = "warning"
        elif value_result.is_medium or contract_result.is_contract_call:
            alert_level = "info"
        else:
            alert_level = "none"

        return FilteredTransaction(
            tx_hash=tx.tx_hash,
            from_address=tx.from_address,
            to_address=tx.to_address,
            value=value_result,
            gas=gas_result,
            contract=contract_result,
            tags=tags,
            alert_level=alert_level,
        )
