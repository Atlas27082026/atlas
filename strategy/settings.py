from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

import yaml


@dataclass(frozen=True)
class StrategySettings:
    raw: Dict[str, Any]

    @property
    def strategy(self) -> Dict[str, Any]:
        return self.raw["strategy"]

    def rvol_minimum(self, hhmm: str) -> float:
        buckets = self.strategy["rvol"]
        for key in ("morning", "midday", "afternoon"):
            bucket = buckets[key]
            if bucket["start"] <= hhmm < bucket["end"]:
                return float(bucket["minimum"])
        return float(buckets["afternoon"]["minimum"])


def load_strategy_settings(path: Path) -> StrategySettings:
    with path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    if "strategy" not in raw:
        raise ValueError(f"Missing top-level 'strategy' section in {path}")
    return StrategySettings(raw=raw)
