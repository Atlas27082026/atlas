from __future__ import annotations

import argparse
import json
import shutil
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Iterable, List

from config import BASE_DIR, DATA_DIR, STATE_DIR, RiskConfig, ensure_runtime_dirs
from core.state import DailyState


STATE_FILES = (
    "daily_state.json",
    "daily_state_strategy_b.json",
    "paper_positions.json",
    "paper_positions_strategy_b.json",
    "pending_setups_strategy_b.json",
)


def _read_balance(path: Path) -> float:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return float(payload.get("session_start_balance", 0.0))
    except Exception:
        return 0.0


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(path)


def reset_paper_run(
    *,
    base_dir: Path = BASE_DIR,
    dry_run: bool,
    backup: bool,
    confirm: bool,
    now: datetime | None = None,
) -> List[str]:
    if not confirm:
        raise RuntimeError("Refusing to reset paper state without --confirm")
    if not dry_run:
        raise RuntimeError("Refusing to reset paper state when dry_run=False")

    now = now or datetime.now()
    state_dir = base_dir / "state"
    data_dir = base_dir / "runtime_data"
    actions: List[str] = []
    backup_dir = data_dir / "paper_reset_backups" / now.strftime("%Y%m%d_%H%M%S")

    if backup:
        backup_dir.mkdir(parents=True, exist_ok=True)
        for name in STATE_FILES:
            src = state_dir / name
            if src.exists():
                dst = backup_dir / name
                shutil.copy2(src, dst)
                actions.append(f"BACKED_UP {src} -> {dst}")

    trading_date = now.date().isoformat()
    for name in ("daily_state.json", "daily_state_strategy_b.json"):
        path = state_dir / name
        balance = _read_balance(path)
        _write_json(path, asdict(DailyState(trading_date=trading_date, session_start_balance=balance)))
        actions.append(f"RESET {path}")

    for name in ("paper_positions.json", "paper_positions_strategy_b.json"):
        path = state_dir / name
        _write_json(path, {"positions": []})
        actions.append(f"RESET {path}")

    path = state_dir / "pending_setups_strategy_b.json"
    _write_json(path, {"setups": []})
    actions.append(f"RESET {path}")

    return actions


def _print_actions(actions: Iterable[str]) -> None:
    for action in actions:
        print(action)


def main() -> int:
    parser = argparse.ArgumentParser(description="Safely reset Atlas paper-run state.")
    parser.add_argument("--backup", action="store_true", help="Back up current paper state before resetting.")
    parser.add_argument("--confirm", action="store_true", help="Required confirmation flag.")
    args = parser.parse_args()

    ensure_runtime_dirs()
    actions = reset_paper_run(
        dry_run=RiskConfig().dry_run,
        backup=bool(args.backup),
        confirm=bool(args.confirm),
    )
    _print_actions(actions)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
