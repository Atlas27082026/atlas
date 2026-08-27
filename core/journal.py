import csv
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable


class CsvJournal:
    def __init__(self, path: Path, fieldnames: Iterable[str]):
        self.path = path
        self.fieldnames = list(fieldnames)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, row: Dict) -> None:
        payload = {key: row.get(key, "") for key in self.fieldnames}
        if "timestamp" in payload and not payload["timestamp"]:
            payload["timestamp"] = datetime.now().isoformat(timespec="seconds")

        write_header = not self.path.exists() or self.path.stat().st_size == 0
        with self.path.open("a", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=self.fieldnames)
            if write_header:
                writer.writeheader()
            writer.writerow(payload)
