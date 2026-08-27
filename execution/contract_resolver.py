from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import pandas as pd

from execution.models import ContractCandidate


# Canonical fields -> known aliases across our tests, older TradeHull frames, and
# Dhan's current instrument master schema observed on the user's runtime.
ALIASES: Dict[str, Sequence[str]] = {
    "trading_symbol": [
        "trading_symbol", "tradingsymbol", "security_symbol", "symbol", "display_name",
        "sem_trading_symbol",
    ],
    "underlying": [
        "underlying", "underlying_symbol", "underlying_security_name", "sm_symbol_name",
        "sem_custom_symbol",
    ],
    "custom_symbol": ["custom_symbol", "sem_custom_symbol"],
    "expiry": ["expiry", "expiry_date", "sm_expiry_date", "sem_expiry_date"],
    "strike": ["strike", "strike_price", "sm_strike_price", "sem_strike_price"],
    "option_type": ["option_type", "instrument_type", "optiontype", "sem_option_type"],
    "exchange_id": ["exchange_id", "exchange", "sem_exm_exch_id"],
    "segment_code": ["segment_code", "segment", "sem_segment"],
    "instrument_name": ["instrument_name", "sem_instrument_name", "sem_exch_instrument_type"],
    "security_id": ["security_id", "sem_smst_security_id"],
    "lot_size": ["lot_size", "lot_units", "sem_lot_units"],
}


def _find_col(df: pd.DataFrame, names: Sequence[str]) -> Optional[str]:
    lower = {str(c).strip().lower(): c for c in df.columns}
    for name in names:
        if str(name).lower() in lower:
            return lower[str(name).lower()]
    return None


def _normalize_option_type(value: object) -> Optional[str]:
    s = str(value).upper().strip()
    if s in ("CE", "CALL") or "CALL" in s:
        return "CE"
    if s in ("PE", "PUT") or "PUT" in s:
        return "PE"
    return None


def _clean_underlying(value: object) -> str:
    s = str(value).upper().strip()
    # Dhan SM_SYMBOL_NAME is normally exactly the underlying. Keep this
    # conservative: no tokenization that could damage symbols containing '-'.
    return s


@dataclass(frozen=True)
class ResolverStats:
    raw_rows: int
    normalized_rows: int
    option_rows: int
    underlyings: int
    expiries: int
    earliest_expiry: Optional[str]
    latest_expiry: Optional[str]
    cache_used: bool = False
    cache_path: str = ""


class InstrumentMasterResolver:
    """Normalize Dhan/TradeHull instrument masters into a stable local schema.

    Strategy and execution code never need to know Dhan's SEM_* column names.
    """

    CACHE_VERSION = 2

    def __init__(self, frame: pd.DataFrame, *, cache_used: bool = False, cache_path: str = ""):
        if frame is None or frame.empty:
            raise ValueError("Instrument master is empty")

        self.raw_columns = list(frame.columns)
        self.cols = {key: _find_col(frame, aliases) for key, aliases in ALIASES.items()}
        required = ("trading_symbol", "expiry", "strike", "option_type")
        missing = [k for k in required if not self.cols.get(k)]
        if missing:
            raise ValueError(
                f"Instrument master missing columns for: {missing}; columns={list(frame.columns)}"
            )

        normalized = pd.DataFrame(index=frame.index)
        normalized["trading_symbol"] = frame[self.cols["trading_symbol"]].astype(str).str.strip()

        if self.cols.get("custom_symbol"):
            normalized["custom_symbol"] = frame[self.cols["custom_symbol"]].astype(str).str.strip()
        else:
            normalized["custom_symbol"] = ""

        if self.cols.get("underlying"):
            normalized["underlying"] = frame[self.cols["underlying"]].map(_clean_underlying)
        else:
            # Last-resort derivation from trading symbol prefix. This path should
            # not be used for the observed Dhan schema because SM_SYMBOL_NAME is present.
            normalized["underlying"] = normalized["trading_symbol"].str.upper().str.split().str[0]

        normalized["expiry"] = pd.to_datetime(frame[self.cols["expiry"]], errors="coerce")
        normalized["strike"] = pd.to_numeric(frame[self.cols["strike"]], errors="coerce")
        normalized["option_type"] = frame[self.cols["option_type"]].map(_normalize_option_type)

        if self.cols.get("instrument_name"):
            normalized["instrument_name"] = frame[self.cols["instrument_name"]].astype(str).str.upper().str.strip()
        else:
            normalized["instrument_name"] = ""

        if self.cols.get("security_id"):
            normalized["security_id"] = frame[self.cols["security_id"]].astype(str).str.strip()
        else:
            normalized["security_id"] = ""

        if self.cols.get("exchange_id"):
            normalized["exchange_id"] = frame[self.cols["exchange_id"]].astype(str).str.upper().str.strip()
        else:
            normalized["exchange_id"] = ""

        if self.cols.get("segment_code"):
            normalized["segment_code"] = frame[self.cols["segment_code"]].astype(str).str.upper().str.strip()
        else:
            normalized["segment_code"] = ""

        def _quote_segment(row):
            exch = str(row.get("exchange_id", "")).upper().strip()
            seg = str(row.get("segment_code", "")).upper().strip()
            if exch == "NSE" and seg == "D":
                return "NSE_FNO"
            if exch == "BSE" and seg == "D":
                return "BSE_FNO"
            if exch == "MCX" and seg == "M":
                return "MCX_COMM"
            if exch == "NSE" and seg == "E":
                return "NSE_EQ"
            if exch == "BSE" and seg == "E":
                return "BSE_EQ"
            return ""

        normalized["exchange_segment"] = normalized.apply(_quote_segment, axis=1)

        if self.cols.get("lot_size"):
            normalized["lot_size"] = pd.to_numeric(frame[self.cols["lot_size"]], errors="coerce").fillna(0).astype(int)
        else:
            normalized["lot_size"] = 0

        # Only rows that really look like options survive. Dhan's master contains
        # cash/futures/etc. where strike/type may be blank or zero.
        option_mask = (
            normalized["option_type"].isin(["CE", "PE"])
            & normalized["expiry"].notna()
            & normalized["strike"].notna()
            & (normalized["strike"] > 0)
            & normalized["trading_symbol"].ne("")
        )
        normalized = normalized.loc[option_mask].copy()
        normalized.reset_index(drop=True, inplace=True)

        if normalized.empty:
            raise ValueError("Instrument master normalized successfully but contains no option contracts")

        self.frame = normalized

        # Fast in-memory lookup. All strike/expiry choices below come from the
        # exchange instrument master; no synthetic strike is ever generated.
        self._contract_index = {}
        for (u, ot), group in self.frame.groupby(["underlying", "option_type"], sort=False):
            self._contract_index[(str(u).upper().strip(), str(ot))] = group.sort_values(["expiry", "strike"]).reset_index(drop=True)

        expiry_dates = self.frame["expiry"].dropna().dt.normalize()
        self.stats = ResolverStats(
            raw_rows=len(frame),
            normalized_rows=len(self.frame),
            option_rows=len(self.frame),
            underlyings=int(self.frame["underlying"].nunique()),
            expiries=int(expiry_dates.nunique()),
            earliest_expiry=str(expiry_dates.min().date()) if not expiry_dates.empty else None,
            latest_expiry=str(expiry_dates.max().date()) if not expiry_dates.empty else None,
            cache_used=cache_used,
            cache_path=cache_path,
        )

    @classmethod
    def from_csv(cls, path: Path) -> "InstrumentMasterResolver":
        return cls(pd.read_csv(path, low_memory=False))

    @classmethod
    def from_cache(cls, path: Path) -> "InstrumentMasterResolver":
        payload = pd.read_pickle(path)
        if isinstance(payload, dict) and payload.get("cache_version") == cls.CACHE_VERSION:
            frame = payload.get("raw_frame")
            if isinstance(frame, pd.DataFrame):
                return cls(frame, cache_used=True, cache_path=str(path))
        raise ValueError(f"Unsupported or corrupt instrument cache: {path}")

    def write_cache(self, path: Path, raw_frame: pd.DataFrame) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        pd.to_pickle(
            {
                "cache_version": self.CACHE_VERSION,
                "created_at": pd.Timestamp.now().isoformat(),
                "raw_frame": raw_frame,
            },
            path,
        )

    def schema_mapping(self) -> Dict[str, Optional[str]]:
        return dict(self.cols)

    def resolve(self, underlying: str, direction: str, spot: float, max_each_side: int = 4, preferred_exchange: str = "NSE") -> List[ContractCandidate]:
        option_type = "CE" if direction == "BULL" else "PE"
        underlying = underlying.upper().strip()
        df = self.frame

        indexed = self._contract_index.get((underlying, option_type))
        subset = indexed.copy() if indexed is not None else pd.DataFrame()

        # Stock-option execution currently targets a configured exchange (NSE by
        # default).  Never silently cross to another exchange: a BSE contract can
        # have the same underlying/strike but completely different liquidity.
        preferred_exchange = str(preferred_exchange or "").upper().strip()
        has_exchange_metadata = bool((df["exchange_id"].astype(str).str.strip() != "").any())
        if preferred_exchange and has_exchange_metadata:
            exch_subset = subset[subset["exchange_id"] == preferred_exchange].copy() if not subset.empty else pd.DataFrame()

            # Some Dhan compact-master versions do not expose a reliable
            # SM_SYMBOL_NAME for every derivative row.  If the pre-built index did
            # not yield the preferred exchange, search the normalized master by the
            # *actual exchange-provided symbols* instead of accepting another exchange.
            if exch_subset.empty:
                ts = df["trading_symbol"].astype(str).str.upper().str.strip()
                cs = df["custom_symbol"].astype(str).str.upper().str.strip()
                symbol_match = (
                    ts.str.startswith(underlying + "-")
                    | ts.eq(underlying)
                    | cs.str.startswith(underlying + " ")
                    | cs.str.startswith(underlying + "-")
                )
                exch_subset = df[
                    symbol_match
                    & (df["option_type"] == option_type)
                    & (df["exchange_id"] == preferred_exchange)
                ].copy()

            # Fail closed for the configured exchange.  Do not fall back from NSE
            # stock options to BSE stock options just because a row exists there.
            subset = exch_subset
        elif preferred_exchange and not has_exchange_metadata:
            # Generic/test masters may not carry exchange metadata. Preserve the
            # existing resolver behavior in that case; production Dhan masters do.
            pass
        elif subset.empty:
            # Exchange-agnostic compatibility path, only when no preference is set.
            ts = df["trading_symbol"].astype(str).str.upper().str.strip()
            cs = df["custom_symbol"].astype(str).str.upper().str.strip()
            symbol_match = (
                ts.str.startswith(underlying + "-")
                | ts.eq(underlying)
                | cs.str.startswith(underlying + " ")
                | cs.str.startswith(underlying + "-")
            )
            subset = df[symbol_match & (df["option_type"] == option_type)].copy()

        if subset.empty:
            return []

        today = pd.Timestamp(date.today())
        subset = subset[subset["expiry"].dt.normalize() >= today]
        if subset.empty:
            return []

        expiry = subset["expiry"].min()
        subset = subset[subset["expiry"] == expiry].copy()

        strikes = sorted(float(x) for x in subset["strike"].dropna().unique().tolist())
        if not strikes:
            return []

        atm = min(strikes, key=lambda x: abs(x - float(spot)))
        atm_idx = strikes.index(atm)
        lo = max(0, atm_idx - max_each_side)
        hi = min(len(strikes), atm_idx + max_each_side + 1)
        selected = set(strikes[lo:hi])
        subset = subset[subset["strike"].astype(float).isin(selected)]

        out: List[ContractCandidate] = []
        seen = set()
        for _, row in subset.iterrows():
            symbol = str(row["trading_symbol"]).strip()
            if not symbol or symbol in seen:
                continue
            seen.add(symbol)
            strike = float(row["strike"])
            if strike == atm:
                money = "ATM"
            elif option_type == "CE":
                money = "ITM" if strike < atm else "OTM"
            else:
                money = "ITM" if strike > atm else "OTM"
            out.append(
                ContractCandidate(
                    underlying=underlying,
                    # This is the exact value from SEM_TRADING_SYMBOL (or its
                    # detected equivalent). Never reconstruct this identifier.
                    trading_symbol=symbol,
                    option_type=option_type,
                    strike=strike,
                    expiry=str(pd.Timestamp(expiry).date()),
                    exchange="NFO" if str(row.get("exchange_id", "NSE")).upper() == "NSE" else str(row.get("exchange_id", "")),
                    source="INSTRUMENT_MASTER",
                    moneyness=money,
                    security_id=str(row.get("security_id", "") or ""),
                    lot_size=int(row.get("lot_size", 0) or 0),
                    custom_symbol=str(row.get("custom_symbol", "") or "").strip(),
                    exchange_id=str(row.get("exchange_id", "") or "").strip(),
                    exchange_segment=str(row.get("exchange_segment", "") or "").strip(),
                    segment_code=str(row.get("segment_code", "") or "").strip(),
                )
            )

        return sorted(out, key=lambda c: (abs(c.strike - atm), c.strike))
