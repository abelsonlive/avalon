"""Minimal serialize-with-delay rate limiter, shared by the MusicBrainz and
Discogs clients -- neither guarantees its own request rate the way
pyacoustid does (MusicBrainz requires ~1 req/s per IP for unauthenticated
traffic; `python3-discogs-client`'s self-throttling is reported unreliable
in its own issue tracker). No locking -- avalon's pipeline is single-
threaded (see cli.py's module docstring), so there's no concurrent caller
to guard against.
"""

from __future__ import annotations

import time


class RateLimiter:
    def __init__(self, min_interval_seconds: float) -> None:
        self._min_interval = min_interval_seconds
        self._last_call: float | None = None

    def wait(self) -> None:
        if self._last_call is not None:
            elapsed = time.monotonic() - self._last_call
            remaining = self._min_interval - elapsed
            if remaining > 0:
                time.sleep(remaining)
        self._last_call = time.monotonic()
