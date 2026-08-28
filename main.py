from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
import time

from config import AppConfig, BASE_DIR, LOG_DIR, STATE_DIR, DATA_DIR, ensure_runtime_dirs
from core.logger import build_logger
from core.risk import RiskManager
from core.scheduler import Scheduler
from core.state import StateStore
from core.position_ownership import ManagedPositionStore, reconcile_position_ownership
from core.paper_positions import PaperPositionStore, manage_paper_position
from core.journal import CsvJournal
from core.decision_audit import DecisionAuditWriter
from core.strategy_b import (
    PendingSetupStore,
    comparison_report,
    compute_strategy_stats,
    contract_from_setup,
    evaluate_confirmation,
    expire_pending_setups,
    mark_setup_cancelled,
    mark_setup_executed,
)
from data.tradehull_client import TradeHullBroker
from execution.contract_resolver import InstrumentMasterResolver
from execution.diagnostics import ExecutionDiagnostics
from execution.liquidity import LiquidityScorer
from execution.quote_parser import parse_quote_response
from execution.selector import ContractSelector
from strategy.diagnostics import DiagnosticsWriter
from strategy.indicators import normalize_ohlcv, latest_completed_candle, add_5m_indicators, add_15m_indicators
from strategy.settings import load_strategy_settings
from strategy.signal_engine import SignalEngine




def _failed_checks(details):
    if not isinstance(details, dict):
        return []
    return [str(k).upper() for k, v in details.items() if isinstance(v, bool) and not v]


def _entry_trigger_summary(result):
    if "ENTRY_TRIGGER" not in result.blockers:
        return ""
    key = "entry_trigger_bull_details" if result.direction == "BULL" else "entry_trigger_bear_details"
    detail = result.metrics.get(key, {}) or {}
    parts = []
    if not detail.get("direction_ok", False):
        parts.append("DIRECTION_SANITY")
    for model_key, label in (("vwap", "VWAP"), ("breakout", "BREAKOUT"), ("trend_continuation", "TREND_CONT")):
        failed = _failed_checks(detail.get(model_key, {}))
        if failed:
            parts.append(f"{label}[{','.join(failed)}]")
    return " | ".join(parts[:3])


def main() -> int:
    ensure_runtime_dirs()
    config = AppConfig()
    logger = build_logger("tradehull_pro", LOG_DIR / "tradehull_pro.log")
    strategy_settings = load_strategy_settings(BASE_DIR / "strategy.yaml")

    try:
        app_version = (BASE_DIR / "VERSION").read_text().strip()
    except Exception:
        app_version = "unknown"
    logger.info("TradeHull Pro Platform v3 — Sprint 4.0 paper-position lifecycle starting")
    logger.info("Platform version=%s", app_version)
    logger.info("Config module=%s", __import__("config").__file__)
    logger.info("DRY_RUN=%s", config.risk.dry_run)
    logger.info("Sprint 4.0 scope: paper entries + persistent positions + partial exits + breakeven/trailing + 15:15 square-off; NO LIVE ORDERS")

    try:
        broker = TradeHullBroker(config)
        native_status = broker.native_dhan_status()
        if native_status is not None:
            if native_status.available:
                logger.info(
                    "Native Dhan market-data adapter: READY | source=%s | detail=%s",
                    native_status.source, native_status.detail or "-",
                )
            else:
                logger.warning(
                    "Native Dhan market-data adapter: UNAVAILABLE | %s",
                    native_status.detail,
                )
        balance = broker.get_balance()
    except Exception as exc:
        logger.exception("Broker initialization failed: %s", exc)
        return 1

    state_store = StateStore(STATE_DIR / "daily_state.json")
    state = state_store.load_or_create(balance)
    managed_store = ManagedPositionStore(STATE_DIR / "managed_positions.json")
    paper_store = PaperPositionStore(STATE_DIR / "paper_positions.json")
    strategy_b_enabled = bool(getattr(config.execution, "enable_strategy_b", False))
    strategy_a_log_prefix = "[A] " if strategy_b_enabled else ""
    strategy_b_state_store = StateStore(STATE_DIR / "daily_state_strategy_b.json") if strategy_b_enabled else None
    strategy_b_state = strategy_b_state_store.load_or_create(balance) if strategy_b_state_store else None
    strategy_b_paper_store = PaperPositionStore(STATE_DIR / "paper_positions_strategy_b.json") if strategy_b_enabled else None
    strategy_b_pending_store = PendingSetupStore(STATE_DIR / "pending_setups_strategy_b.json") if strategy_b_enabled else None
    risk = RiskManager(config)
    scheduler = Scheduler(offset_seconds=config.market.scan_offset_seconds)
    engine = SignalEngine(strategy_settings)
    selector = ContractSelector(config)
    scorer = LiquidityScorer(config)

    day = datetime.now().strftime("%Y-%m-%d")
    diagnostics = DiagnosticsWriter(DATA_DIR / day / "diagnostics.csv")
    exec_diag = ExecutionDiagnostics(DATA_DIR / day / "execution_candidates.csv")
    decision_audit = DecisionAuditWriter(DATA_DIR / day / "decision_audit.csv")
    paper_journal = CsvJournal(DATA_DIR / day / "paper_trade_journal.csv", [
        "timestamp", "event", "trade_id", "underlying", "direction", "model",
        "contract_symbol", "security_id", "quantity", "price", "pnl", "reason",
        "open_quantity", "entry_price", "stop_price", "target_price", "strategy_pnl"
    ])
    strategy_b_paper_journal = CsvJournal(DATA_DIR / day / "paper_trade_journal_strategy_b.csv", [
        "timestamp", "event", "trade_id", "underlying", "direction", "model",
        "contract_symbol", "security_id", "quantity", "price", "pnl", "reason",
        "open_quantity", "entry_price", "stop_price", "target_price", "strategy_pnl"
    ]) if strategy_b_enabled else None

    resolver = None
    configured_master = Path(config.execution.instrument_master_path).expanduser() if config.execution.instrument_master_path else None
    instrument_cache = STATE_DIR / f"instrument_master_{day}.pkl"
    try:
        if configured_master and configured_master.exists():
            resolver = InstrumentMasterResolver.from_csv(configured_master)
            logger.info("Instrument resolver: configured CSV %s", configured_master)
        elif instrument_cache.exists():
            try:
                resolver = InstrumentMasterResolver.from_cache(instrument_cache)
                logger.info("Instrument resolver: normalized cache loaded %s", instrument_cache)
            except Exception as cache_exc:
                logger.warning("Instrument cache ignored: %s", cache_exc)
                resolver = None

        if resolver is None:
            master = broker.get_instrument_master()
            if master is not None:
                resolver = InstrumentMasterResolver(master)
                resolver.write_cache(instrument_cache, master)
                logger.info("Instrument resolver: TradeHull/Dhan instrument master normalized and cached")

        if resolver is not None:
            st = resolver.stats
            mapping = resolver.schema_mapping()
            logger.info(
                "Instrument master | raw rows=%d | options=%d | underlyings=%d | expiries=%d | range=%s..%s | cache=%s",
                st.raw_rows, st.option_rows, st.underlyings, st.expiries,
                st.earliest_expiry or "N/A", st.latest_expiry or "N/A",
                "HIT" if st.cache_used else "BUILT",
            )
            logger.info(
                "Instrument schema | underlying=%s | trading_symbol=%s | expiry=%s | strike=%s | option_type=%s",
                mapping.get("underlying"), mapping.get("trading_symbol"), mapping.get("expiry"),
                mapping.get("strike"), mapping.get("option_type"),
            )
        else:
            logger.warning("Instrument master unavailable; dry-run legacy resolver fallback remains available")
    except Exception as exc:
        logger.warning("Instrument master unavailable: %s", exc)
        resolver = None

    pnl = broker.get_live_pnl()
    broker_positions = broker.get_position_snapshots()
    ownership = reconcile_position_ownership(broker_positions, managed_store.open_records(), account_pnl=pnl)
    logger.info("Session start balance: ₹%.2f", state.session_start_balance)
    logger.info("Strategy capital base: ₹%.2f", risk.strategy_capital_base(state))
    logger.info("Strategy daily loss threshold: ₹%.2f", risk.daily_loss_limit(state))
    logger.info("Account P&L: %s", "N/A" if pnl is None else f"₹{pnl:.2f}")
    logger.info(
        "Position ownership | managed=%d | external=%d | strategy P&L=%s | external P&L=%s",
        ownership.managed_open_count, ownership.external_open_count,
        "N/A" if ownership.managed_pnl is None else f"₹{ownership.managed_pnl:.2f}",
        "N/A" if ownership.external_pnl is None else f"₹{ownership.external_pnl:.2f}",
    )
    for pos in ownership.external_positions:
        logger.info(
            "External position (observe only) | symbol=%s | sec=%s | qty=%s | pnl=%s | engine_will_not_manage=true",
            pos.trading_symbol or "N/A", pos.security_id or "N/A", pos.quantity,
            "N/A" if pos.pnl is None else f"₹{pos.pnl:.2f}",
        )
    if ownership.orphaned_managed:
        logger.warning("Managed-position registry has %d open record(s) not present at broker; reconciliation required before LIVE mode", len(ownership.orphaned_managed))
    paper_open = paper_store.open_positions()
    logger.info(
        "%sPaper portfolio | open=%d | today's P&L=₹%.2f | cumulative P&L=₹%.2f | daily trades=%d/%d",
        "[A] " if strategy_b_enabled else "",
        len(paper_open), paper_store.strategy_pnl_for_date(state.trading_date),
        paper_store.strategy_pnl(), state.daily_trade_count, config.risk.max_daily_trades,
    )
    if strategy_b_enabled:
        strategy_b_open = strategy_b_paper_store.open_positions()
        logger.info(
            "[B] Paper portfolio | pending=%d | open=%d | today's P&L=₹%.2f | cumulative P&L=₹%.2f | daily trades=%d/%d",
            len(strategy_b_pending_store.pending()), len(strategy_b_open),
            strategy_b_paper_store.strategy_pnl_for_date(strategy_b_state.trading_date),
            strategy_b_paper_store.strategy_pnl(), strategy_b_state.daily_trade_count,
            config.risk.max_daily_trades,
        )
    logger.info("Execution path: normalized instrument master -> native Dhan security-ID quotes -> verified scoring; symbol quote fallback is diagnostics-only")
    native_diag = broker.native_dhan_diagnostic_info()
    logger.info(
        "Native Dhan diagnostic | type=%s | source=%s | methods=%s",
        native_diag.get("client_type") or "N/A",
        native_diag.get("source") or "N/A",
        ", ".join(native_diag.get("candidate_methods") or []) or "NONE",
    )
    native_quote_debug_done = False

    if not config.risk.dry_run:
        logger.error("Sprint 4.0 is PAPER ONLY and intentionally refuses LIVE mode. Keep dry_run=True.")
        return 2

    processed_candles = {}
    try:
        while True:
            now = datetime.now()
            hhmm = now.strftime("%H:%M")
            if hhmm > config.market.market_close:
                if strategy_b_enabled:
                    logger.info(
                        "\n%s",
                        comparison_report(
                            compute_strategy_stats(paper_store, trading_date=state.trading_date),
                            compute_strategy_stats(
                                strategy_b_paper_store,
                                strategy_b_pending_store,
                                strategy_b_state.trading_date,
                            ),
                        ),
                    )
                logger.info("Market closed at %s. Sprint 3 engine stopping.", hhmm)
                return 0
            if hhmm < config.market.market_open:
                wait = min(30, scheduler.seconds_until_next_5m_scan())
                logger.info("Market inactive at %s; sleeping %ss", hhmm, wait)
                time.sleep(wait)
                continue

            # Never evaluate the prior session at 09:15. The first valid 5-minute
            # intraday candle is 09:15-09:20, so execution research begins only
            # after that candle has closed plus the configured scan offset.
            first_scan_hhmm = "09:20" if config.market.market_open == "09:15" else config.market.market_open
            if hhmm < first_scan_hhmm:
                wait = min(30, scheduler.seconds_until_next_5m_scan())
                logger.info("Waiting for first completed 5m candle (%s); sleeping %ss", first_scan_hhmm, wait)
                time.sleep(wait)
                continue

            pnl = broker.get_live_pnl()
            broker_positions = broker.get_position_snapshots()
            ownership = reconcile_position_ownership(broker_positions, managed_store.open_records(), account_pnl=pnl)

            # Sprint 4: mark and manage all open paper positions before evaluating new entries.
            paper_positions = paper_store.open_positions()
            if paper_positions:
                by_segment = {}
                for pp in paper_positions:
                    by_segment.setdefault(pp.exchange_segment, []).append(pp)
                for segment, rows in by_segment.items():
                    ids = [p.security_id for p in rows if p.security_id]
                    payload = {}
                    try:
                        payload = broker.get_quote_data_by_security_ids(ids, exchange_segment=segment) if ids else {}
                    except Exception as exc:
                        logger.warning("%sPaper MTM quote failed | segment=%s | error=%s", strategy_a_log_prefix, segment, exc)
                    for pp in rows:
                        quote = parse_quote_response(payload, pp.security_id)
                        mark = quote.bid if quote.bid is not None and quote.bid > 0 else quote.ltp
                        if mark is None or mark <= 0:
                            logger.warning("%sPaper position mark unavailable | %s | sec=%s", strategy_a_log_prefix, pp.underlying, pp.security_id)
                            continue
                        updated, events = manage_paper_position(
                            pp, float(mark), hhmm, config.market.force_exit_time,
                            getattr(config.execution, "paper_partial_exit_fraction", 0.50),
                            getattr(config.execution, "paper_trailing_pct", 0.05),
                        )
                        paper_store.replace(updated)
                        for ev in events:
                            logger.info(
                                "%s📘 PAPER %s | %s | %s | qty %d @ %.2f | pnl ₹%.2f | reason=%s | remaining=%d | stop=%.2f",
                                strategy_a_log_prefix, ev.event, updated.underlying, updated.contract_symbol, ev.quantity, ev.price, ev.pnl, ev.reason, updated.open_quantity, updated.stop_price,
                            )
                            paper_journal.append({
                                "event": ev.event, "trade_id": ev.trade_id, "underlying": ev.underlying,
                                "direction": updated.direction, "model": updated.model, "contract_symbol": ev.contract_symbol,
                                "security_id": updated.security_id, "quantity": ev.quantity, "price": ev.price, "pnl": ev.pnl,
                                "reason": ev.reason, "open_quantity": updated.open_quantity, "entry_price": updated.entry_price,
                                "stop_price": updated.stop_price, "target_price": updated.target_price, "strategy_pnl": paper_store.strategy_pnl(),
                            })
                            if ev.event == "EXIT" and updated.status == "CLOSED":
                                if updated.total_pnl < 0:
                                    state.consecutive_losses += 1
                                else:
                                    state.consecutive_losses = 0
                                state_store.save(state)

            if strategy_b_enabled:
                strategy_b_positions = strategy_b_paper_store.open_positions()
                if strategy_b_positions:
                    by_segment = {}
                    for pp in strategy_b_positions:
                        by_segment.setdefault(pp.exchange_segment, []).append(pp)
                    for segment, rows in by_segment.items():
                        ids = [p.security_id for p in rows if p.security_id]
                        payload = {}
                        try:
                            payload = broker.get_quote_data_by_security_ids(ids, exchange_segment=segment) if ids else {}
                        except Exception as exc:
                            logger.warning("[B] Paper MTM quote failed | segment=%s | error=%s", segment, exc)
                        for pp in rows:
                            quote = parse_quote_response(payload, pp.security_id)
                            mark = quote.bid if quote.bid is not None and quote.bid > 0 else quote.ltp
                            if mark is None or mark <= 0:
                                logger.warning("[B] Paper position mark unavailable | %s | sec=%s", pp.underlying, pp.security_id)
                                continue
                            updated, events = manage_paper_position(
                                pp, float(mark), hhmm, config.market.force_exit_time,
                                getattr(config.execution, "paper_partial_exit_fraction", 0.50),
                                getattr(config.execution, "paper_trailing_pct", 0.05),
                            )
                            strategy_b_paper_store.replace(updated)
                            for ev in events:
                                logger.info(
                                    "[B] PAPER %s | %s | %s | qty %d @ %.2f | pnl ₹%.2f | reason=%s | remaining=%d | stop=%.2f",
                                    ev.event, updated.underlying, updated.contract_symbol, ev.quantity, ev.price, ev.pnl, ev.reason, updated.open_quantity, updated.stop_price,
                                )
                                strategy_b_paper_journal.append({
                                    "event": ev.event, "trade_id": ev.trade_id, "underlying": ev.underlying,
                                    "direction": updated.direction, "model": updated.model, "contract_symbol": ev.contract_symbol,
                                    "security_id": updated.security_id, "quantity": ev.quantity, "price": ev.price, "pnl": ev.pnl,
                                    "reason": ev.reason, "open_quantity": updated.open_quantity, "entry_price": updated.entry_price,
                                    "stop_price": updated.stop_price, "target_price": updated.target_price, "strategy_pnl": strategy_b_paper_store.strategy_pnl(),
                                })
                                if ev.event == "EXIT" and updated.status == "CLOSED":
                                    if updated.total_pnl < 0:
                                        strategy_b_state.consecutive_losses += 1
                                    else:
                                        strategy_b_state.consecutive_losses = 0
                                    strategy_b_state_store.save(strategy_b_state)

                expired = expire_pending_setups(strategy_b_pending_store)
                if expired:
                    logger.info("[B] Pending setups expired | count=%d", expired)

                strategy_b_pnl = strategy_b_paper_store.strategy_pnl_for_date(strategy_b_state.trading_date)
                if (
                    not getattr(strategy_b_state, "daily_loss_locked", False)
                    and strategy_b_pnl <= -risk.daily_loss_limit(strategy_b_state)
                ):
                    strategy_b_state.daily_loss_locked = True
                    strategy_b_state_store.save(strategy_b_state)
                    logger.error(
                        "[B] Daily loss circuit breaker latched | Today's Paper P&L ₹%.2f <= -₹%.2f | new entries locked for trading date %s",
                        strategy_b_pnl, risk.daily_loss_limit(strategy_b_state), strategy_b_state.trading_date,
                    )

                for setup in strategy_b_pending_store.pending():
                    try:
                        raw1 = broker.get_historical_data(setup.symbol, config.market.exchange_cash, "1")
                        df1 = normalize_ohlcv(raw1, setup.symbol, "1m")
                        c1 = latest_completed_candle(df1, 1)
                        df1 = df1.iloc[: c1.index + 1].copy()
                        confirmation = evaluate_confirmation(
                            setup, df1, getattr(config.execution, "strategy_b_max_adverse_atr_1m", 0.5)
                        )
                        if confirmation.cancel:
                            mark_setup_cancelled(strategy_b_pending_store, setup, confirmation.reason)
                            logger.info("[B] Pending setup cancelled | %s | reason=%s", setup.symbol, confirmation.reason)
                            continue
                        if not confirmation.confirmed:
                            logger.info("[B] Waiting for 1-minute confirmation | %s | reason=%s", setup.symbol, confirmation.reason)
                            continue

                        strategy_b_open = strategy_b_paper_store.open_positions()
                        strategy_b_pnl = strategy_b_paper_store.strategy_pnl_for_date(strategy_b_state.trading_date)
                        if (
                            not getattr(strategy_b_state, "daily_loss_locked", False)
                            and strategy_b_pnl <= -risk.daily_loss_limit(strategy_b_state)
                        ):
                            strategy_b_state.daily_loss_locked = True
                            strategy_b_state_store.save(strategy_b_state)
                        strategy_b_decision = risk.can_open_new_trade(
                            state=strategy_b_state,
                            managed_open_positions=len(strategy_b_open),
                            strategy_pnl=strategy_b_pnl,
                        )
                        if not strategy_b_decision.allowed:
                            logger.info("[B] Waiting for 1-minute confirmation | %s | risk=%s", setup.symbol, strategy_b_decision.reason)
                            continue
                        if strategy_b_paper_store.has_open_underlying(setup.symbol):
                            logger.info("[B] Waiting for 1-minute confirmation | %s | PAPER_POSITION_ALREADY_OPEN", setup.symbol)
                            continue

                        contract = contract_from_setup(setup)
                        candidate = SimpleNamespace(
                            contract=contract,
                            lot_size=setup.lot_size,
                            entry_limit=setup.entry_limit,
                            quantity=setup.quantity,
                            stop_price=setup.stop_price,
                            target_price=setup.target_price,
                        )
                        paper = strategy_b_paper_store.add_from_candidate(setup.symbol, setup.direction, setup.model, candidate)
                        mark_setup_executed(strategy_b_pending_store, setup, confirmation.close_price)
                        strategy_b_state.daily_trade_count += 1
                        if setup.symbol not in strategy_b_state.traded_underlyings:
                            strategy_b_state.traded_underlyings.append(setup.symbol)
                        strategy_b_state_store.save(strategy_b_state)
                        strategy_b_paper_journal.append({
                            "event": "ENTRY", "trade_id": paper.trade_id, "underlying": setup.symbol, "direction": setup.direction,
                            "model": setup.model, "contract_symbol": paper.contract_symbol, "security_id": paper.security_id,
                            "quantity": paper.initial_quantity, "price": paper.entry_price, "pnl": 0.0, "reason": "1M_CONFIRMATION",
                            "open_quantity": paper.open_quantity, "entry_price": paper.entry_price, "stop_price": paper.stop_price,
                            "target_price": paper.target_price, "strategy_pnl": strategy_b_paper_store.strategy_pnl(),
                        })
                        logger.info(
                            "[B] Confirmation satisfied | %s | close %.2f | atr_1m %.2f",
                            setup.symbol, confirmation.close_price, confirmation.atr_1m,
                        )
                        logger.info(
                            "[B] PAPER ENTRY | %s | %s | qty %d | entry %.2f | target1 %.2f | stop %.2f | trade_id=%s",
                            setup.symbol, paper.contract_symbol, paper.initial_quantity, paper.entry_price,
                            paper.target_price, paper.stop_price, paper.trade_id,
                        )
                    except Exception as exc:
                        logger.warning("[B] Pending setup monitor skipped | %s | %s", setup.symbol, exc)

            paper_open = paper_store.open_positions()
            strategy_pnl = paper_store.strategy_pnl_for_date(state.trading_date)
            cumulative_strategy_pnl = paper_store.strategy_pnl()
            if (
                not getattr(state, "daily_loss_locked", False)
                and strategy_pnl <= -risk.daily_loss_limit(state)
            ):
                state.daily_loss_locked = True
                state_store.save(state)
                logger.error(
                    "%sDaily loss circuit breaker latched | Today's Paper P&L ₹%.2f <= -₹%.2f | new entries locked for trading date %s",
                    strategy_a_log_prefix, strategy_pnl, risk.daily_loss_limit(state), state.trading_date,
                )
            decision = risk.can_open_new_trade(
                state=state,
                managed_open_positions=len(paper_open),
                strategy_pnl=strategy_pnl,
            )
            logger.info(
                "%sScan %s | Account P&L %s | Today's Paper P&L ₹%.2f | Cumulative Paper P&L ₹%.2f | Paper %d/%d | External %d | Trades %d/%d | Risk=%s",
                strategy_a_log_prefix, hhmm,
                "N/A" if pnl is None else f"₹{pnl:.2f}",
                strategy_pnl, cumulative_strategy_pnl, len(paper_open), config.risk.max_open_positions,
                ownership.external_open_count, state.daily_trade_count, config.risk.max_daily_trades, decision.reason,
            )
            if decision.daily_loss_override:
                logger.warning(
                    "%sPAPER_RESEARCH_OVERRIDE | Daily loss lock bypassed | Today's P&L ₹%.2f",
                    strategy_a_log_prefix, strategy_pnl,
                )

            full_signals = near_signals = exec_candidates = 0
            for symbol in config.watchlist:
                try:
                    raw5 = broker.get_historical_data(symbol, config.market.exchange_cash, "5")
                    raw15 = broker.get_historical_data(symbol, config.market.exchange_cash, "15")
                    df5 = add_5m_indicators(normalize_ohlcv(raw5, symbol, "5m"), strategy_settings)
                    df15 = add_15m_indicators(normalize_ohlcv(raw15, symbol, "15m"), strategy_settings)
                    result = engine.evaluate(symbol, df5, df15, hhmm)

                    if processed_candles.get(symbol) == result.candle_time:
                        continue
                    processed_candles[symbol] = result.candle_time
                    diagnostics.append(result)

                    if result.decision == "NEAR":
                        near_signals += 1
                        trigger_detail = _entry_trigger_summary(result)
                        if trigger_detail:
                            logger.info("Near %s %s: %.0f%% | Blocked by: %s | Trigger detail: %s", result.direction, symbol, result.score_pct, ", ".join(result.blockers), trigger_detail)
                        else:
                            logger.info("Near %s %s: %.0f%% | Blocked by: %s", result.direction, symbol, result.score_pct, ", ".join(result.blockers))
                        continue
                    if result.decision != "SIGNAL":
                        continue

                    full_signals += 1
                    logger.info("%s🌟 %s signal %s | score %.0f | model=%s", strategy_a_log_prefix, result.direction, symbol, result.score_pct, result.model)

                    # Research signals are still logged after cutoff, but execution resolution is stopped.
                    if hhmm >= config.market.new_entry_cutoff:
                        logger.info("%s%s execution skipped: ENTRY_CUTOFF %s reached (signal retained for research)", strategy_a_log_prefix, symbol, config.market.new_entry_cutoff)
                        decision_audit.append(result, decision.reason, "ENTRY_REJECTED", "ENTRY_CUTOFF")
                        continue
                    strategy_a_reject_reason = ""
                    if not decision.allowed:
                        logger.info("%s%s execution skipped: risk gate %s", strategy_a_log_prefix, symbol, decision.reason)
                        decision_audit.append(result, decision.reason, "ENTRY_REJECTED", decision.reason)
                        strategy_a_reject_reason = decision.reason
                    if not strategy_a_reject_reason and state.daily_trade_count >= config.risk.max_daily_trades:
                        logger.info("%s%s execution skipped: MAX_DAILY_TRADES %d reached", strategy_a_log_prefix, symbol, config.risk.max_daily_trades)
                        decision_audit.append(result, "MAX_DAILY_TRADES", "ENTRY_REJECTED", "MAX_DAILY_TRADES")
                        strategy_a_reject_reason = strategy_a_reject_reason or "MAX_DAILY_TRADES"
                    if not strategy_a_reject_reason and paper_store.has_open_underlying(symbol):
                        logger.info("%s%s execution skipped: PAPER_POSITION_ALREADY_OPEN", strategy_a_log_prefix, symbol)
                        decision_audit.append(result, decision.reason, "ENTRY_REJECTED", "PAPER_POSITION_ALREADY_OPEN")
                        strategy_a_reject_reason = strategy_a_reject_reason or "PAPER_POSITION_ALREADY_OPEN"
                    if strategy_a_reject_reason and not strategy_b_enabled:
                        continue

                    contracts = []
                    if resolver is not None:
                        contracts = resolver.resolve(
                            symbol, result.direction, float(result.metrics["close_5m"]),
                            config.execution.nearby_strikes_each_side,
                            preferred_exchange=config.execution.preferred_option_exchange,
                        )
                    if not contracts:
                        contracts = broker.resolve_nearby_options_legacy(symbol, result.direction)
                        if contracts:
                            logger.info("%s resolver fallback: %d legacy contracts", symbol, len(contracts))
                    else:
                        logger.info("%s resolver: %d nearby contracts from instrument master", symbol, len(contracts))
                    if not contracts:
                        logger.warning("%s%s: no option contracts resolved", strategy_a_log_prefix, symbol)
                        decision_audit.append(result, decision.reason, "ENTRY_REJECTED", "NO_OPTION_CONTRACTS")
                        continue

                    # Security-ID quotes are authoritative and bypass TradeHull's
                    # symbol resolver.  This is the primary path for Dhan F&O.
                    security_ids = [c.security_id for c in contracts if str(c.security_id).strip()]
                    security_quote_payload = {}
                    security_quote_error = None
                    quote_segments = sorted({str(c.exchange_segment or "").strip() for c in contracts if str(c.exchange_segment or "").strip()})
                    quote_segment = quote_segments[0] if len(quote_segments) == 1 else ""
                    if len(quote_segments) > 1:
                        logger.warning("%s resolver returned mixed quote segments: %s; execution fails closed", symbol, quote_segments)
                    if security_ids and quote_segment:
                        try:
                            security_quote_payload = broker.get_quote_data_by_security_ids(
                                security_ids, exchange_segment=quote_segment
                            )
                            if config.execution.native_quote_diagnostics and (
                                not config.execution.native_quote_diagnostics_once or not native_quote_debug_done
                            ):
                                raw = repr(security_quote_payload)
                                max_chars = int(config.execution.native_quote_diagnostics_max_chars)
                                if len(raw) > max_chars:
                                    raw = raw[:max_chars] + "...<truncated>"
                                logger.warning(
                                    "NATIVE_QUOTE_DEBUG | symbol=%s | segment=%s | requested_ids=%s | response_type=%s | raw=%s",
                                    symbol, quote_segment, security_ids, type(security_quote_payload).__name__, raw,
                                )
                                native_quote_debug_done = True
                        except Exception as exc:
                            security_quote_error = str(exc)
                            if config.execution.native_quote_diagnostics and (
                                not config.execution.native_quote_diagnostics_once or not native_quote_debug_done
                            ):
                                logger.warning(
                                    "NATIVE_QUOTE_DEBUG_EXCEPTION | symbol=%s | segment=%s | requested_ids=%s | exc_type=%s | error=%s",
                                    symbol, quote_segment, security_ids, type(exc).__name__, exc,
                                )
                                native_quote_debug_done = True
                            logger.warning("%s security-ID quote path unavailable: %s", symbol, exc)

                    # Symbol based TradeHull calls remain only as a fallback for any
                    # contract not returned by the native security-ID quote endpoint.
                    query_symbols = [broker.quote_symbol(c) for c in contracts]
                    symbol_quote_payload = None
                    ltp_payload = None
                    accepted = []
                    capital = risk.capital_for_trade(risk.strategy_capital_base(state))

                    for contract in contracts:
                        broker_symbol = broker.quote_symbol(contract)
                        quote_source = "DHAN_SECURITY_ID"
                        quote = parse_quote_response(security_quote_payload, str(contract.security_id))

                        if quote.ltp is None and quote.bid is None and quote.ask is None:
                            # Sprint 3.3 fails closed for execution-quality data.
                            # TradeHull symbol APIs are intentionally not used to score
                            # an option after the native security-ID quote path fails.
                            quote_source = "NATIVE_DHAN_UNAVAILABLE"

                        # Preserve the quote source in the immutable snapshot.
                        quote = type(quote)(**{**quote.__dict__, "source": quote_source})

                        try:
                            lot_size = broker.lot_size_for_contract(contract)
                        except Exception:
                            lot_size = int(contract.lot_size or 0)

                        assessment = scorer.assess(quote, lot_size)
                        candidate = selector.build_candidate(contract, quote, lot_size, capital, assessment)
                        exec_diag.append_assessment(
                            symbol, result.direction, result.model, result.score_pct, contract, quote, assessment,
                            lot_size, candidate, broker_symbol=broker_symbol,
                        )
                        spread_text = "N/A" if assessment.spread_pct is None else f"{assessment.spread_pct:.1f}%"
                        logger.info(
                            "%s | master=%s | broker=%s | exch=%s/%s | sec=%s | strike=%s | quote=%s | grade=%s health=%.0f conf=%s spread=%s | %s",
                            symbol, contract.trading_symbol, broker_symbol, contract.exchange_id or "N/A", contract.exchange_segment or "N/A", contract.security_id or "N/A",
                            contract.strike, quote_source, assessment.grade, assessment.health_score, assessment.confidence,
                            spread_text, ("ACCEPT" if candidate else "REJECT: " + (",".join(assessment.reasons) if assessment.reasons else "NOT_EXECUTABLE")),
                        )
                        if candidate:
                            accepted.append(candidate)

                    best = selector.choose_best(accepted)
                    if best is None:
                        logger.info("%s%s: no contract passed verified-data execution threshold", strategy_a_log_prefix, symbol)
                        decision_audit.append(result, decision.reason, "ENTRY_REJECTED", "NO_EXECUTABLE_CONTRACT")
                        continue
                    exec_candidates += 1
                    if not strategy_a_reject_reason:
                        paper = paper_store.add_from_candidate(symbol, result.direction, result.model, best)
                        state.daily_trade_count += 1
                        if symbol not in state.traded_underlyings:
                            state.traded_underlyings.append(symbol)
                        state_store.save(state)
                        paper_journal.append({
                            "event": "ENTRY", "trade_id": paper.trade_id, "underlying": symbol, "direction": result.direction,
                            "model": result.model, "contract_symbol": paper.contract_symbol, "security_id": paper.security_id,
                            "quantity": paper.initial_quantity, "price": paper.entry_price, "pnl": 0.0, "reason": "SIGNAL",
                            "open_quantity": paper.open_quantity, "entry_price": paper.entry_price, "stop_price": paper.stop_price,
                            "target_price": paper.target_price, "strategy_pnl": paper_store.strategy_pnl(),
                        })
                        logger.info(
                            "%s🏆 PAPER ENTRY %s | %s | %s health %.0f conf %s | qty %d | entry %.2f | target1 %.2f | stop %.2f | trade_id=%s",
                            strategy_a_log_prefix, symbol, best.contract.trading_symbol, best.liquidity.grade, best.liquidity.health_score, best.liquidity.confidence,
                            best.quantity, best.entry_limit, best.target_price, best.stop_price, paper.trade_id,
                        )
                        decision_audit.append(result, decision.reason, "ENTRY_ACCEPTED", "OK")
                    if strategy_b_enabled:
                        if strategy_b_pending_store.has_pending_symbol(symbol):
                            logger.info("[B] Pending setup skipped | %s | PENDING_SETUP_ALREADY_OPEN", symbol)
                        elif strategy_b_paper_store.has_open_underlying(symbol):
                            logger.info("[B] Pending setup skipped | %s | PAPER_POSITION_ALREADY_OPEN", symbol)
                        else:
                            setup = strategy_b_pending_store.add_from_candidate(
                                result, best, getattr(config.execution, "strategy_b_pending_expiry_minutes", 5)
                            )
                            logger.info(
                                "[B] Pending setup created | %s | %s | %s | qty %d | signal %.2f | expires=%s",
                                setup.symbol, setup.direction, setup.model, setup.quantity,
                                setup.signal_price, setup.expires_at,
                            )

                except Exception as exc:
                    logger.warning("Skipping %s: %s", symbol, exc)

            logger.info("Scan complete | full signals=%d | near signals=%d | execution candidates=%d", full_signals, near_signals, exec_candidates)
            wait = scheduler.seconds_until_next_5m_scan()
            if strategy_b_enabled and strategy_b_pending_store.pending():
                wait = min(wait, int(getattr(config.execution, "strategy_b_monitor_interval_seconds", 60)))
            logger.info("Next completed-candle scan in %ds", wait)
            time.sleep(wait)

    except KeyboardInterrupt:
        logger.info("Engine stopped manually")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
