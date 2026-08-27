# Sprint 4.0.1 hotfix

- Backward-compatible defaults for paper_partial_exit_fraction (0.50) and paper_trailing_pct (0.05).
- Logs the exact config.py path at startup to expose stale/mixed-folder imports.
- Prevents a paper-position monitoring crash if an older ExecutionConfig is accidentally imported.

# Changelog

## 4.0.0-sprint4 — Paper Position Lifecycle

- Adds persistent paper positions in `state/paper_positions.json`.
- A valid execution candidate now opens a simulated position instead of only logging a dry-run candidate.
- Prevents duplicate entries for an underlying while its paper position remains open.
- Paper positions are marked using native Dhan security-ID quotes.
- Initial stop and target use the existing Sprint 3.5 execution prices.
- At target1, exits 50% (lot-aligned where possible), moves the remaining stop to breakeven, and keeps a runner.
- Runner uses a 5% premium trailing stop from the highest observed premium.
- Automatically squares off all remaining paper positions at 15:15.
- Paper P&L, position count, and daily trade count now drive dry-run risk decisions.
- Adds `runtime_data/<date>/paper_trade_journal.csv` for ENTRY/PARTIAL_EXIT/EXIT events.
- Keeps broker/manual positions external and observe-only.
- LIVE mode remains explicitly disabled.
