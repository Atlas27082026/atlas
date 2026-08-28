# Atlas Trading Engine v4.4.0 — RUN 4

RUN 4 keeps Strategy A as the immediate 5-minute paper baseline and uses Strategy B as a paper-only multi-timeframe research model.

## Paper lifecycle

1. Signal + verified option candidate -> simulated entry.
2. Position is persisted in `state/paper_positions.json` and survives restart.
3. Native Dhan security-ID quotes mark the position each scan.
4. Stop loss exits the full remaining position.
5. Target1 exits 50%, moves stop to breakeven, and leaves a runner.
6. Runner trails 5% below the highest observed option premium.
7. Any open paper position is force-exited at 15:15.
8. Duplicate entries in the same underlying are blocked while a paper position is open.

Official test command:

```bash
python3 -m unittest discover -s tests
```

RUN 4 paper outputs use run-specific filenames such as `runtime_data/run_4_YYYYMMDD_strategy_a_trades.csv`, `runtime_data/run_4_YYYYMMDD_strategy_b_trades.csv`, `runtime_data/run_4_YYYYMMDD_strategy_b_setups.csv`, and `runtime_data/run_4_YYYYMMDD_decisions.csv`.

## Monday RUN 4 research profile

| Setting | Default | Recommended RUN 4 Monday value | Applies to | Effect | Safety |
| --- | --- | --- | --- | --- | --- |
| `dry_run` | `True` | `True` | both | Keeps all trading paper-only | production-safe guard |
| `enable_strategy_b` | `False` | `True` | B | Enables MTF research model alongside A | research |
| `paper_ignore_daily_loss_lock` | `False` | `True` | both paper gates | Allows paper entries after latched daily-loss lock for sample collection | research-only; ignored when `dry_run=False` |
| `max_daily_trades` | `5` | `5` unless reviewed | both, independent state | Caps entries per strategy state | production-safe |
| `max_open_positions` | `2` | `2` unless reviewed | both, independent stores | Caps open paper positions per strategy | production-safe |
| `new_entry_cutoff` | `14:45` | `14:45` | both | Stops new entries after cutoff | production-safe |
| `force_exit_time` | `15:15` | `15:15` | both | Force-exits open paper positions | production-safe |
| `strategy_b_setup_max_minutes` | `10` | `10` | B | Expires stale MTF setups | research |
| `strategy_b_monitor_interval_seconds` | `60` | `60` | B | Rechecks completed 1m candles while setups are pending | research |
| `strategy_b_max_adverse_atr_1m` | `0.5` | `0.5` | B | Blocks excessive 1m extension at trigger | research |
| `native_quote_diagnostics` | `False` | `False` | execution diagnostics | Raw quote payload dumps disabled by default | production-safe |
| `opening_protection.observe_mode` | `True` | `True` | both diagnostics | Records opening-window diagnostics without blocking trades | research |
| `market_state` | observe via diagnostics | observe | both diagnostics | Correlates market state with outcomes | research |

Safe paper reset before Monday:

```bash
python3 tools/reset_paper_run.py --backup --confirm
```

The reset utility refuses to run when `dry_run=False`, never calls broker order APIs, does not delete `.env`, logs, credentials, broker positions, or instrument master caches, and prints each file it backs up or resets.

**LIVE orders are still disabled. Keep `DRY_RUN=True`.**
