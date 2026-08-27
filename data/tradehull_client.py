from typing import Any, List, Optional

import numpy as np
import pandas as pd
from Dhan_Tradehull import Tradehull

from config import AppConfig
from data.broker import Broker
from data.dhan_native_client import NativeDhanClient
from execution.models import ContractCandidate
from core.position_ownership import BrokerPositionSnapshot


class TradeHullBroker(Broker):
    """Tradehull adapter. Strategy/execution modules never import Tradehull directly."""

    def __init__(self, config: AppConfig):
        c = config.credentials
        self.client = Tradehull(ClientCode=c.client_code, mode="pin_totp", pin=c.pin, totp_secret=c.totp_secret)
        self.native_dhan = NativeDhanClient(
            client_id=c.client_code,
            access_token=getattr(c, "access_token", ""),
            tradehull_root=self.client,
        )

    def get_balance(self) -> float:
        value = float(self.client.get_balance())
        if not np.isfinite(value) or value < 0: raise ValueError(f"Invalid broker balance: {value!r}")
        return value

    def get_live_pnl(self) -> Optional[float]:
        try:
            value = self.client.get_live_pnl()
            if value is None: return None
            value = float(value)
            return value if np.isfinite(value) else None
        except Exception:
            return None

    def get_positions(self) -> Any: return self.client.get_positions()

    def count_open_positions(self) -> int:
        positions = self.get_positions()
        if positions is None: return 0
        try:
            if hasattr(positions, "empty") and positions.empty: return 0
            if hasattr(positions, "columns"):
                cols = {str(c).lower(): c for c in positions.columns}
                for candidate in ("netqty", "net_qty", "quantity", "netquantity"):
                    if candidate in cols:
                        return int((positions[cols[candidate]].fillna(0).astype(float) != 0).sum())
                return len(positions)
        except Exception: pass
        if isinstance(positions, list):
            count = 0
            for row in positions:
                if isinstance(row, dict):
                    qty = row.get("netQty", row.get("net_qty", row.get("quantity", 0)))
                    try: count += int(float(qty) != 0)
                    except Exception: pass
            return count
        return 0

    @staticmethod
    def _first_value(row, names, default=None):
        if not isinstance(row, dict):
            return default
        lower = {str(k).lower(): v for k, v in row.items()}
        for name in names:
            if name in row and row[name] not in (None, ""):
                return row[name]
            value = lower.get(str(name).lower())
            if value not in (None, ""):
                return value
        return default

    def get_position_snapshots(self) -> List[BrokerPositionSnapshot]:
        """Normalize broker positions without assuming ownership.

        Dhan/TradeHull field names have changed across releases, so this adapter
        intentionally recognizes several known aliases. Unknown fields remain in
        `raw` for diagnostics; they are never silently treated as algo-owned.
        """
        positions = self.get_positions()
        if positions is None:
            return []
        if isinstance(positions, pd.DataFrame):
            rows = positions.to_dict(orient="records")
        elif isinstance(positions, list):
            rows = [r for r in positions if isinstance(r, dict)]
        elif isinstance(positions, dict):
            data = positions.get("data")
            rows = data if isinstance(data, list) else [positions]
        else:
            return []

        out = []
        for row in rows:
            qty = self._first_value(row, ["netQty", "net_qty", "netQuantity", "netquantity", "quantity", "netqty"], 0)
            try:
                qty = float(qty or 0)
            except Exception:
                qty = 0.0
            if qty == 0:
                continue

            pnl = self._first_value(row, ["realizedProfit", "realized_profit", "unrealizedProfit", "unrealized_profit", "pnl", "mtm"])
            # If both realized and unrealized are exposed, prefer their sum.
            realized = self._first_value(row, ["realizedProfit", "realized_profit"])
            unrealized = self._first_value(row, ["unrealizedProfit", "unrealized_profit"])
            try:
                if realized is not None or unrealized is not None:
                    pnl = float(realized or 0) + float(unrealized or 0)
                elif pnl is not None:
                    pnl = float(pnl)
            except Exception:
                pnl = None

            avg = self._first_value(row, ["buyAvg", "buy_avg", "costPrice", "cost_price", "averagePrice", "avgPrice"])
            try:
                avg = None if avg is None else float(avg)
            except Exception:
                avg = None

            out.append(BrokerPositionSnapshot(
                security_id=str(self._first_value(row, ["securityId", "security_id", "securityid"], "") or ""),
                trading_symbol=str(self._first_value(row, ["tradingSymbol", "trading_symbol", "tradingsymbol", "symbol"], "") or ""),
                quantity=qty,
                pnl=pnl,
                average_price=avg,
                product_type=str(self._first_value(row, ["productType", "product_type", "product"], "") or ""),
                raw=dict(row),
            ))
        return out

    def get_historical_data(self, symbol: str, exchange: str, timeframe: str):
        return self.client.get_historical_data(tradingsymbol=symbol, exchange=exchange, timeframe=timeframe)

    @staticmethod
    def _format_strike(strike: float) -> str:
        value = float(strike)
        return str(int(value)) if value.is_integer() else (f"{value:.8f}".rstrip("0").rstrip("."))

    def quote_symbol(self, contract: ContractCandidate) -> str:
        """Return the best symbol for TradeHull's *symbol based* convenience APIs.

        Security-ID market quotes are preferred by Sprint 3.1.2.  If we need to
        fall back to TradeHull's name resolver, Dhan's SEM_CUSTOM_SYMBOL is the
        first choice because it comes from the same instrument master TradeHull
        itself downloads.  Only after that do we derive the documented human
        readable alias.  The exact SEM_TRADING_SYMBOL is always preserved on the
        ContractCandidate and is never reconstructed.
        """
        if contract.broker_symbol:
            return contract.broker_symbol
        if contract.custom_symbol and str(contract.custom_symbol).strip():
            return str(contract.custom_symbol).strip()
        try:
            expiry = pd.Timestamp(contract.expiry)
            day_mon = expiry.strftime("%d %b").upper()
            side = "CALL" if contract.option_type == "CE" else "PUT"
            return f"{contract.underlying} {day_mon} {self._format_strike(contract.strike)} {side}"
        except Exception:
            return contract.trading_symbol

    def get_quote_data(self, symbols: List[str]):
        return self.client.get_quote_data(names=symbols)

    def get_ltp_data(self, symbols: List[str]):
        return self.client.get_ltp_data(names=symbols)

    def get_quote_data_by_security_ids(self, security_ids: List[str], exchange_segment: str = "NSE_FNO"):
        """Native Dhan quote snapshot by security ID.

        Sprint 3.3 never asks TradeHull to translate an option trading symbol on
        this path.  Authentication may be supplied by an explicit official SDK
        token or by reusing an authenticated native Dhan object embedded inside
        the TradeHull session.
        """
        ids = []
        for security_id in security_ids:
            text = str(security_id).strip()
            if not text:
                continue
            try:
                ids.append(int(float(text)))
            except Exception:
                continue
        if not ids:
            return {}
        return self.native_dhan.quote_data({exchange_segment: ids})

    def native_dhan_status(self):
        return self.native_dhan.status

    def native_dhan_diagnostic_info(self):
        return self.native_dhan.diagnostic_info()

    def get_lot_size(self, symbol: str) -> int:
        return int(self.client.get_lot_size(tradingsymbol=symbol))

    def get_instrument_master(self):
        """Best-effort discovery of Tradehull's already-downloaded instrument DataFrame.

        This deliberately avoids depending on a private attribute name. If no suitable frame
        is discoverable, Sprint 3 falls back to legacy symbol resolution in DRY RUN only.
        """
        candidates = []
        try:
            for name, value in vars(self.client).items():
                if isinstance(value, pd.DataFrame) and len(value.columns) >= 4:
                    candidates.append((name, value))
        except Exception:
            return None
        keywords = ("strike", "expiry", "symbol")
        for _, frame in candidates:
            cols = " ".join(str(c).lower() for c in frame.columns)
            if sum(k in cols for k in keywords) >= 2:
                return frame
        return None

    @staticmethod
    def _response_items(resp):
        if not isinstance(resp, (list, tuple)): return []
        return list(resp)

    def resolve_nearby_options_legacy(self, underlying: str, direction: str):
        option_type = "CE" if direction == "BULL" else "PE"
        out = []
        seen = set()
        calls = [
            ("ATM", lambda: self.client.ATM_Strike_Selection(Underlying=underlying, Expiry=0)),
            ("ITM1", lambda: self.client.ITM_Strike_Selection(Underlying=underlying, Expiry=0, ITM_count=1)),
            ("ITM2", lambda: self.client.ITM_Strike_Selection(Underlying=underlying, Expiry=0, ITM_count=2)),
            ("OTM1", lambda: self.client.OTM_Strike_Selection(Underlying=underlying, Expiry=0, OTM_count=1)),
        ]
        for label, fn in calls:
            try:
                items = self._response_items(fn())
                if len(items) < 2: continue
                symbol = items[0] if option_type == "CE" else items[1]
                strike = items[2] if len(items) > 2 else 0
                if not symbol or symbol in seen: continue
                seen.add(symbol)
                out.append(ContractCandidate(
                    underlying=underlying,
                    trading_symbol=str(symbol),
                    option_type=option_type,
                    strike=float(strike or 0),
                    expiry="",
                    exchange="NFO",
                    source="LEGACY_TRADEHULL",
                    moneyness=label,
                    broker_symbol=str(symbol),
                ))
            except Exception:
                continue
        return out

    def place_super_order(self, **kwargs): return self.client.place_super_order(**kwargs)
