from __future__ import annotations

import time


class RateLimiter:
    """Simple process-local rate limiter for API calls."""

    def __init__(self, min_interval_seconds: float):
        self.min_interval_seconds = max(0.0, min_interval_seconds)
        self._last_call_ts = 0.0

    def wait(self) -> None:
        if self.min_interval_seconds <= 0:
            self._last_call_ts = time.monotonic()
            return

        elapsed = time.monotonic() - self._last_call_ts
        sleep_for = self.min_interval_seconds - elapsed
        if sleep_for > 0:
            time.sleep(sleep_for)
        self._last_call_ts = time.monotonic()

