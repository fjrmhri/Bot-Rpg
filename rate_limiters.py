import time
from collections import defaultdict
from typing import DefaultDict, List


class RateLimiter:
    def __init__(self, max_calls: int, period: float):
        self.max_calls = max_calls
        self.period = period
        self.calls: DefaultDict[int, List[float]] = defaultdict(list)

    def is_allowed(self, user_id: int) -> bool:
        now = time.time()
        window = self.calls[user_id]
        window[:] = [t for t in window if now - t < self.period]
        if len(window) >= self.max_calls:
            return False
        window.append(now)
        return True
