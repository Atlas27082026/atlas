from pathlib import Path
from types import SimpleNamespace

from core.paper_positions import PaperPositionStore, manage_paper_position


def _candidate():
    contract = SimpleNamespace(
        trading_symbol='TEST-Sep2026-100-CE', security_id='123', exchange_segment='NSE_FNO',
        strike=100.0, option_type='CE', expiry='2026-09-29'
    )
    return SimpleNamespace(
        contract=contract, lot_size=10, entry_limit=100.0, quantity=20,
        stop_price=92.5, target_price=115.0
    )


def test_store_and_duplicate_guard(tmp_path: Path):
    store = PaperPositionStore(tmp_path / 'paper.json')
    p = store.add_from_candidate('TEST', 'BULL', 'BREAKOUT_CONTINUATION', _candidate())
    assert p.open_quantity == 20
    assert store.has_open_underlying('TEST')
    try:
        store.add_from_candidate('TEST', 'BULL', 'BREAKOUT_CONTINUATION', _candidate())
        assert False, 'expected duplicate guard'
    except ValueError:
        pass


def test_partial_then_breakeven_and_trailing(tmp_path: Path):
    store = PaperPositionStore(tmp_path / 'paper2.json')
    p = store.add_from_candidate('TESTX', 'BULL', 'VWAP_PULLBACK', _candidate())
    p, events = manage_paper_position(p, 116.0, '12:00', '15:15', 0.5, 0.05)
    assert p.target1_hit
    assert p.open_quantity == 10
    assert p.stop_price >= 100.0
    assert events and events[0].event == 'PARTIAL_EXIT'
    p, _ = manage_paper_position(p, 125.0, '12:05', '15:15', 0.5, 0.05)
    assert p.stop_price >= 118.75


def test_stop_and_force_exit(tmp_path: Path):
    store = PaperPositionStore(tmp_path / 'paper3.json')
    p = store.add_from_candidate('TESTY', 'BULL', 'VWAP_PULLBACK', _candidate())
    p, events = manage_paper_position(p, 90.0, '12:00', '15:15', 0.5, 0.05)
    assert p.status == 'CLOSED' and events[0].reason == 'STOP'

    p2 = store.add_from_candidate('TESTZ', 'BULL', 'VWAP_PULLBACK', _candidate())
    p2, events2 = manage_paper_position(p2, 101.0, '15:15', '15:15', 0.5, 0.05)
    assert p2.status == 'CLOSED' and events2[0].reason == 'FORCE_EXIT'
