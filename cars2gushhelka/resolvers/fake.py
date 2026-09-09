"""Deterministic in-memory resolver, for unit/end-to-end tests.

No network, no filesystem beyond what the test hands it directly. Lets tests
exercise the full matcher tier-selection logic (which is the part with real
business risk) without depending on any live geocoding backend.
"""

from __future__ import annotations

from typing import Dict, Optional

from .base import ResolvedParcel, ResolveQuery


class FakeResolver:
    def __init__(self, answers: Optional[Dict[str, ResolvedParcel]] = None):
        """`answers` maps `ResolveQuery.cache_key` -> ResolvedParcel. A key
        with no entry resolves to None (tier fails, matcher tries the next
        one)."""
        self.answers = dict(answers or {})
        self.calls: list[ResolveQuery] = []

    def resolve(self, query: ResolveQuery) -> Optional[ResolvedParcel]:
        self.calls.append(query)
        return self.answers.get(query.cache_key)
