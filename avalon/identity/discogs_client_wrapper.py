"""Wraps `python3-discogs-client` (import name `discogs_client` -- the
actively maintained community fork, not the abandoned original
`discogs-client` package) for release/label/genre search.

Reads fields directly off each result's raw `.data` dict rather than the
library's higher-level model attributes: those attributes (`.genres`,
`.styles`, `.labels`, ...) are `SimpleField`/`ListField` descriptors keyed
to a *release detail* fetch's field names, which don't all match a
*search result*'s flatter shape (confirmed live against Discogs' own API --
search results have `genre`/`style`/`label` as plain string lists, not the
`genres`/`styles`/`labels` object lists a full release fetch returns).
Reading a mismatched attribute silently triggers an extra network fetch
per result rather than raising, so this avoids both a hidden N+1 and
depending on data shapes only confirmed for a different endpoint.

Discogs has no fingerprinting -- matches are text search only, so unlike
AcoustID's numeric score, matches here are gated by a plain string
similarity check against the top result.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from difflib import SequenceMatcher

from avalon.identity.rate_limit import RateLimiter

logger = logging.getLogger(__name__)

_MIN_REQUEST_INTERVAL_SECONDS = 1.1

_MIN_TITLE_SIMILARITY = 0.6


@dataclass(slots=True)
class DiscogsMatch:
    release_id: str
    title: str
    genre: str | None = None
    label: str | None = None
    catalog_number: str | None = None


class DiscogsClientWrapper:
    """Constructed once per CLI run (mirrors `EssentiaAnalyzer`'s shape);
    `search()` is called once per file."""

    def __init__(self, token: str, user_agent: str = "avalon/0.1.0") -> None:
        import discogs_client

        self._exceptions = discogs_client.exceptions
        self._client = discogs_client.Client(user_agent, user_token=token)
        self._rate_limiter = RateLimiter(_MIN_REQUEST_INTERVAL_SECONDS)

    def search(self, artist: str, title: str) -> DiscogsMatch | None:
        """Searches for a release by `artist` + `title` (track or album
        title -- Discogs' search matches reasonably against either).
        Returns the top result if it's similar enough to the query,
        otherwise None (logged, not raised)."""
        self._rate_limiter.wait()
        try:
            results = self._client.search(title, artist=artist, type="release")
            top = next(iter(results), None)
            if top is None:
                return None
            return self._parse_result(top.data, artist, title)
        except (self._exceptions.DiscogsAPIError, KeyError) as exc:
            logger.warning("Discogs search failed for %r %r: %s", artist, title, exc)
            return None

    @staticmethod
    def _parse_result(
        data: dict, query_artist: str, query_title: str
    ) -> DiscogsMatch | None:
        result_title = data.get("title") or ""
        query = f"{query_artist} {query_title}".lower()
        similarity = SequenceMatcher(None, query, result_title.lower()).ratio()
        if similarity < _MIN_TITLE_SIMILARITY:
            logger.info(
                "Discogs top result %r too dissimilar to %r (%.2f) -- discarding",
                result_title,
                query,
                similarity,
            )
            return None

        genre_parts = []
        if data.get("genre"):
            genre_parts.append(data["genre"][0])
        if data.get("style"):
            genre_parts.append(data["style"][0])

        return DiscogsMatch(
            release_id=str(data["id"]),
            title=result_title,
            genre=" / ".join(genre_parts) or None,
            label=(data.get("label") or [None])[0],
            catalog_number=data.get("catno") or None,
        )
