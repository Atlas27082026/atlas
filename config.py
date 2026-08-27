from dataclasses import dataclass, field
from pathlib import Path
from typing import List


BASE_DIR = Path(__file__).resolve().parent
LOG_DIR = BASE_DIR / "logs"
STATE_DIR = BASE_DIR / "state"
DATA_DIR = BASE_DIR / "runtime_data"


@dataclass(frozen=True)
class CredentialsConfig:
    client_code: str = "1111752561"
    pin: str = "197015"
    totp_secret: str = "CRX47FK4OR6TXCPNZ3GZPONGMNGGJ2OL"
    # Optional. Prefer environment variable DHAN_ACCESS_TOKEN; never commit a real token.
    access_token: str = ""


@dataclass(frozen=True)
class MarketConfig:
    exchange_cash: str = "NSE"
    exchange_fno: str = "NFO"
    market_open: str = "09:15"
    morning_route_end: str = "09:30"
    new_entry_cutoff: str = "14:45"
    force_exit_time: str = "15:15"
    market_close: str = "15:30"
    scan_offset_seconds: int = 4


@dataclass(frozen=True)
class RiskConfig:
    dry_run: bool = True
    max_open_positions: int = 2
    max_daily_trades: int = 5
    capital_per_trade_fraction: float = 0.20
    daily_max_loss_pct: float = 0.015
    max_consecutive_losses: int = 3
    fail_closed_on_risk_data_error: bool = True
    # Fixed capital assigned to this strategy. Set 0 to use session-start balance.
    # Keeping this separate from broker available balance prevents manual positions
    # from shrinking the strategy daily-loss threshold after a restart.
    strategy_capital_base: float = 150000.0


@dataclass(frozen=True)
class StrategyConfig:
    supertrend_period: int = 7
    supertrend_multiplier: float = 3.0
    ema_period_5m: int = 21
    ema_period_15m: int = 21
    rsi_period: int = 14
    roc_period: int = 12
    adx_period: int = 14
    adx_min: float = 22.0
    rsi_bull_5m: float = 55.0
    rsi_bear_5m: float = 45.0
    rsi_macro_bull: float = 50.0
    rsi_macro_bear: float = 50.0
    rvol_min_morning: float = 1.20
    rvol_min_midday: float = 1.05
    rvol_min_afternoon: float = 1.10
    vwap_touch_upper_pct: float = 0.0015
    vwap_touch_lower_pct: float = 0.0030
    enable_vwap_model: bool = True
    enable_breakout_model: bool = True


@dataclass(frozen=True)
class ExecutionConfig:
    premium_min: float = 20.0
    premium_max: float = 350.0
    catastrophic_spread_pct: float = 15.0
    min_health_score_live: float = 65.0
    min_health_score_dry_run: float = 55.0
    limit_price_buffer_pct: float = 0.002
    target_multiplier: float = 1.15
    stop_multiplier: float = 0.925
    trailing_jump: float = 0.5
    instrument_master_path: str = ""
    nearby_strikes_each_side: int = 4
    preferred_option_exchange: str = "NSE"
    native_quote_diagnostics: bool = True
    native_quote_diagnostics_once: bool = True
    native_quote_diagnostics_max_chars: int = 6000
    # Sprint 4 paper-position management.
    paper_partial_exit_fraction: float = 0.50
    paper_trailing_pct: float = 0.05


@dataclass(frozen=True)
class AppConfig:
    credentials: CredentialsConfig = field(default_factory=CredentialsConfig)
    market: MarketConfig = field(default_factory=MarketConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)
    watchlist: List[str] = field(default_factory=lambda: [
        "BEL", "TATACONSUM", "BAJAJ-AUTO", "NESTLEIND", "BAJFINANCE",
        "HINDALCO", "NTPC", "HDFCLIFE", "SBILIFE", "HINDUNILVR", "MARUTI",
        "ITC", "TMPV", "ADANIPORTS", "DRREDDY", "WIPRO", "ONGC", "SBIN",
        "EICHERMOT", "COALINDIA", "BAJAJFINSV", "MAXHEALTH", "KOTAKBANK",
        "APOLLOHOSP", "CIPLA", "JSWSTEEL", "HCLTECH", "TCS", "INDIGO",
        "BHARTIARTL", "ETERNAL", "LT", "SHRIRAMFIN", "GRASIM", "AXISBANK",
        "POWERGRID", "ADANIENT", "TECHM", "ICICIBANK", "JIOFIN", "TATASTEEL",
        "TITAN", "INFY", "TRENT", "ASIANPAINT", "ULTRACEMCO", "HDFCBANK",
        "RELIANCE", "M&M", "SUNPHARMA",
    ])


def ensure_runtime_dirs() -> None:
    for path in (LOG_DIR, STATE_DIR, DATA_DIR):
        path.mkdir(parents=True, exist_ok=True)
