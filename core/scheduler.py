import time
from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass
class Scheduler:
    offset_seconds: int = 4

    def seconds_until_next_5m_scan(self) -> int:
        now = datetime.now()
        minute_bucket = (now.minute // 5 + 1) * 5
        hour = now.hour
        day = now.date()
        if minute_bucket >= 60:
            minute_bucket = 0
            target = datetime.combine(day, datetime.min.time()).replace(hour=hour) + timedelta(hours=1)
        else:
            target = now.replace(minute=minute_bucket, second=self.offset_seconds, microsecond=0)
        if target <= now:
            target += timedelta(minutes=5)
        return max(1, int((target - now).total_seconds()))

    def sleep_until_next_5m_scan(self) -> None:
        time.sleep(self.seconds_until_next_5m_scan())
