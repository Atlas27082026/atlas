from abc import ABC, abstractmethod
from typing import Any, List, Optional

from execution.models import ContractCandidate
from core.position_ownership import BrokerPositionSnapshot


class Broker(ABC):
    @abstractmethod
    def get_balance(self) -> float: raise NotImplementedError
    @abstractmethod
    def get_live_pnl(self) -> Optional[float]: raise NotImplementedError
    @abstractmethod
    def get_positions(self) -> Any: raise NotImplementedError
    @abstractmethod
    def count_open_positions(self) -> int: raise NotImplementedError
    @abstractmethod
    def get_position_snapshots(self) -> List[BrokerPositionSnapshot]: raise NotImplementedError
    @abstractmethod
    def get_historical_data(self, symbol: str, exchange: str, timeframe: str): raise NotImplementedError
    @abstractmethod
    def get_quote_data(self, symbols: List[str]): raise NotImplementedError
    @abstractmethod
    def get_quote_data_by_security_ids(self, security_ids: List[str], exchange_segment: str = "NSE_FNO"): raise NotImplementedError
    @abstractmethod
    def get_ltp_data(self, symbols: List[str]): raise NotImplementedError
    @abstractmethod
    def get_lot_size(self, symbol: str) -> int: raise NotImplementedError
    @abstractmethod
    def get_instrument_master(self): raise NotImplementedError
    @abstractmethod
    def resolve_nearby_options_legacy(self, underlying: str, direction: str): raise NotImplementedError
    @abstractmethod
    def place_super_order(self, **kwargs): raise NotImplementedError


    def native_dhan_status(self):
        """Optional diagnostic describing native security-ID market-data availability."""
        return None

    def quote_symbol(self, contract: ContractCandidate) -> str:
        """Return the symbol syntax expected by this broker adapter's quote APIs."""
        return contract.broker_symbol or contract.trading_symbol

    def lot_size_for_contract(self, contract: ContractCandidate) -> int:
        if contract.lot_size and contract.lot_size > 0:
            return int(contract.lot_size)
        return self.get_lot_size(self.quote_symbol(contract))
