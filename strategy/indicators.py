from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

import numpy as np
import pandas as pd
import talib

from strategy.settings import StrategySettings


REQUIRED_OHLCV = ("open", "high", "low", "close", "volume")
TIME_COLUMNS = ("date", "datetime", "timestamp")


@dataclass(frozen=True)
class CompletedCandle:
    row: pd.Series
    index: int
    timestamp: pd.Timestamp


def normalize_ohlcv(df: pd.DataFrame, symbol: str, timeframe: str) -> pd.DataFrame:
    if df is None or len(df) == 0:
        raise ValueError(f"No {timeframe} data returned for {symbol}")
    out = df.copy()
    out.columns = [str(c).strip().lower() for c in out.columns]
    missing = [c for c in REQUIRED_OHLCV if c not in out.columns]
    if missing:
        raise ValueError(f"{symbol} {timeframe} missing OHLCV columns: {missing}")

    time_col = next((c for c in TIME_COLUMNS if c in out.columns), None)
    if time_col is None:
        raise ValueError(f"{symbol} {timeframe} has no date/datetime/timestamp column")

    for col in REQUIRED_OHLCV:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    out["datetime_parsed"] = pd.to_datetime(out[time_col], errors="coerce")
    out.dropna(subset=["datetime_parsed", "high", "low", "close", "volume"], inplace=True)
    out.sort_values("datetime_parsed", inplace=True)
    out.reset_index(drop=True, inplace=True)
    if len(out) < 3:
        raise ValueError(f"Not enough valid {timeframe} candles for {symbol}")
    return out


def latest_completed_candle(df: pd.DataFrame, timeframe_minutes: int) -> CompletedCandle:
    if len(df) < 2:
        raise ValueError("Need at least two candles")

    last_ts = pd.Timestamp(df.iloc[-1]["datetime_parsed"])
    if last_ts.tzinfo is None:
        now = pd.Timestamp.now()
    else:
        now = pd.Timestamp.now(tz=last_ts.tzinfo)

    age_seconds = (now - last_ts).total_seconds()
    # Dhan/TradeHull commonly returns a currently-forming candle timestamped at bar start.
    idx = len(df) - 2 if age_seconds < timeframe_minutes * 60 else len(df) - 1
    ts = pd.Timestamp(df.iloc[idx]["datetime_parsed"])
    return CompletedCandle(row=df.iloc[idx], index=idx, timestamp=ts)


def _supertrend(df: pd.DataFrame, period: int, multiplier: float) -> pd.DataFrame:
    out = df.copy()
    atr = talib.ATR(
        out["high"].to_numpy(float),
        out["low"].to_numpy(float),
        out["close"].to_numpy(float),
        timeperiod=period,
    )
    out["atr"] = atr
    hl2 = (out["high"] + out["low"]) / 2.0
    basic_ub = hl2 + multiplier * out["atr"]
    basic_lb = hl2 - multiplier * out["atr"]

    final_ub = np.full(len(out), np.nan)
    final_lb = np.full(len(out), np.nan)
    st = np.full(len(out), np.nan)
    direction = np.full(len(out), "", dtype=object)

    valid = np.where(~np.isnan(atr))[0]
    if len(valid) == 0:
        out["supertrend"] = st
        out["st_direction"] = direction
        return out

    first = int(valid[0])
    final_ub[first] = float(basic_ub.iloc[first])
    final_lb[first] = float(basic_lb.iloc[first])
    if float(out.iloc[first]["close"]) >= final_lb[first]:
        st[first] = final_lb[first]
        direction[first] = "BULL"
    else:
        st[first] = final_ub[first]
        direction[first] = "BEAR"

    close = out["close"].to_numpy(float)
    for i in range(first + 1, len(out)):
        if np.isnan(atr[i]):
            continue
        prev = i - 1
        if np.isnan(final_ub[prev]) or np.isnan(final_lb[prev]):
            final_ub[i] = float(basic_ub.iloc[i])
            final_lb[i] = float(basic_lb.iloc[i])
        else:
            final_ub[i] = (
                float(basic_ub.iloc[i])
                if float(basic_ub.iloc[i]) < final_ub[prev] or close[prev] > final_ub[prev]
                else final_ub[prev]
            )
            final_lb[i] = (
                float(basic_lb.iloc[i])
                if float(basic_lb.iloc[i]) > final_lb[prev] or close[prev] < final_lb[prev]
                else final_lb[prev]
            )

        prev_st = st[prev]
        if np.isnan(prev_st):
            st[i] = final_lb[i] if close[i] >= final_lb[i] else final_ub[i]
        elif np.isclose(prev_st, final_ub[prev], rtol=1e-9, atol=1e-9):
            st[i] = final_ub[i] if close[i] <= final_ub[i] else final_lb[i]
        else:
            st[i] = final_lb[i] if close[i] >= final_lb[i] else final_ub[i]
        direction[i] = "BULL" if np.isclose(st[i], final_lb[i], rtol=1e-9, atol=1e-9) else "BEAR"

    out["supertrend"] = st
    out["st_direction"] = direction
    return out


def add_5m_indicators(df: pd.DataFrame, settings: StrategySettings) -> pd.DataFrame:
    cfg = settings.strategy
    ind = cfg["indicators"]
    out = df.copy()

    out["date_only"] = out["datetime_parsed"].dt.date
    iso = out["datetime_parsed"].dt.isocalendar()
    out["week_id"] = iso["year"].astype(str) + "-" + iso["week"].astype(str).str.zfill(2)
    out["tp"] = (out["high"] + out["low"] + out["close"]) / 3.0
    out["tp_vol"] = out["tp"] * out["volume"]
    out["cum_tp_vol_day"] = out.groupby("date_only")["tp_vol"].cumsum()
    out["cum_vol_day"] = out.groupby("date_only")["volume"].cumsum()
    out["vwap_session"] = out["cum_tp_vol_day"] / out["cum_vol_day"].replace(0, np.nan)
    out["cum_tp_vol_week"] = out.groupby("week_id")["tp_vol"].cumsum()
    out["cum_vol_week"] = out.groupby("week_id")["volume"].cumsum()
    out["vwap_weekly"] = out["cum_tp_vol_week"] / out["cum_vol_week"].replace(0, np.nan)

    close = out["close"].to_numpy(float)
    out["ema_5m"] = talib.EMA(close, timeperiod=int(ind["ema_5m"]))
    out["rsi_5m"] = talib.RSI(close, timeperiod=int(ind["rsi_period"]))
    out["roc_5m"] = talib.ROC(close, timeperiod=int(ind["roc_period"]))

    lookback = int(ind["rvol_lookback"])
    avg_vol = out["volume"].rolling(lookback, min_periods=max(5, lookback // 2)).mean().shift(1)
    out["rvol"] = out["volume"] / avg_vol.replace(0, np.nan)

    out = _supertrend(
        out,
        period=int(ind["supertrend_period"]),
        multiplier=float(ind["supertrend_multiplier"]),
    )
    return out


def add_15m_indicators(df: pd.DataFrame, settings: StrategySettings) -> pd.DataFrame:
    ind = settings.strategy["indicators"]
    out = df.copy()
    high = out["high"].to_numpy(float)
    low = out["low"].to_numpy(float)
    close = out["close"].to_numpy(float)
    out["ema_15m"] = talib.EMA(close, timeperiod=int(ind["ema_15m"]))
    out["rsi_15m"] = talib.RSI(close, timeperiod=int(ind["rsi_period"]))
    out["roc_15m"] = talib.ROC(close, timeperiod=int(ind["roc_period"]))
    out["adx_15m"] = talib.ADX(high, low, close, timeperiod=int(ind["adx_period"]))
    return out
