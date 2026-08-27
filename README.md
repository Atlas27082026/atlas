# TradeHull Pro Platform v3 — Sprint 4

Sprint 4 turns the stable Sprint 3.5 signal + option-selection pipeline into a stateful **paper trading engine**.

## Paper lifecycle

1. Signal + verified option candidate -> simulated entry.
2. Position is persisted in `state/paper_positions.json` and survives restart.
3. Native Dhan security-ID quotes mark the position each scan.
4. Stop loss exits the full remaining position.
5. Target1 exits 50%, moves stop to breakeven, and leaves a runner.
6. Runner trails 5% below the highest observed option premium.
7. Any open paper position is force-exited at 15:15.
8. Duplicate entries in the same underlying are blocked while a paper position is open.

Paper journal: `runtime_data/YYYY-MM-DD/paper_trade_journal.csv`.

**LIVE orders are still disabled. Keep `DRY_RUN=True`.**
