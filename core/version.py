from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


RUN_LABEL = "RUN 5"
STRATEGY_A_LABEL = "IMMEDIATE_5M_BASELINE"
STRATEGY_B_LABEL = "MTF_15M_5M_1M"
STRATEGY_C_LABEL = "REGIME_MTF_15M_5M_1M"
EXPERIMENT_LABEL = STRATEGY_C_LABEL


@dataclass(frozen=True)
class AtlasRunIdentity:
    version: str
    run: str
    mode: str
    strategy_a: str
    strategy_b: str
    strategy_c: str
    experiment: str
    research_enabled: bool
    trading_date: str

    @property
    def run_id(self) -> str:
        compact_date = self.trading_date.replace("-", "")
        return f"run_5_{compact_date}"


def atlas_version(base_dir: Path) -> str:
    try:
        return (base_dir / "VERSION").read_text(encoding="utf-8").strip()
    except Exception:
        return "unknown"


def build_run_identity(config, base_dir: Path, trading_date: str | None = None) -> AtlasRunIdentity:
    mode = "PAPER" if bool(getattr(config.risk, "dry_run", True)) else "LIVE_DISABLED"
    return AtlasRunIdentity(
        version=atlas_version(base_dir),
        run=RUN_LABEL,
        mode=mode,
        strategy_a=STRATEGY_A_LABEL,
        strategy_b=STRATEGY_B_LABEL,
        strategy_c=STRATEGY_C_LABEL,
        experiment=EXPERIMENT_LABEL,
        research_enabled=bool(
            getattr(config.execution, "enable_strategy_b", False)
            or getattr(config.execution, "enable_strategy_c", False)
        ),
        trading_date=trading_date or datetime.now().date().isoformat(),
    )


def startup_banner(identity: AtlasRunIdentity) -> str:
    research = "ENABLED" if identity.research_enabled else "DISABLED"
    return (
        "============================================================\n"
        "ATLAS TRADING ENGINE\n"
        f"Version      : {identity.version}\n"
        f"Run          : {identity.run}\n"
        f"Mode         : {identity.mode}\n"
        f"Strategy A   : {identity.strategy_a}\n"
        f"Strategy B   : {identity.strategy_b}\n"
        f"Strategy C   : {identity.strategy_c}\n"
        f"Research     : {research}\n"
        f"Trading Date : {identity.trading_date}\n"
        "============================================================"
    )
