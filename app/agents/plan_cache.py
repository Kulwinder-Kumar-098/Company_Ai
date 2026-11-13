"""Thread-safe in-process cache for validated, user-independent plans."""

import hashlib
import json
import time
import unicodedata
from collections import OrderedDict
from threading import Lock
from typing import Callable

from pydantic import ValidationError

from app.agents.workflows import RequestPlan


def _normalize_query(query: str) -> str:
    normalized = " ".join(query.casefold().split())
    while normalized and unicodedata.category(normalized[-1]).startswith("P"):
        normalized = normalized[:-1].rstrip()
    return normalized


def plan_cache_key(
    query: str,
    fingerprint: str,
    provider: str,
    model: str,
) -> str:
    identity = json.dumps(
        [
            _normalize_query(query),
            fingerprint,
            provider,
            model,
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


class PlanCache:
    """Bounded LRU plan cache with expiration based on an injectable clock."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = Lock()
        self._entries: OrderedDict[str, tuple[float, str]] = OrderedDict()

    def get(self, key: str) -> RequestPlan | None:
        now = self._clock()
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            expires_at, serialized_plan = entry
            if expires_at <= now:
                del self._entries[key]
                return None
            self._entries.move_to_end(key)
        try:
            return RequestPlan.model_validate_json(serialized_plan)
        except (ValidationError, ValueError):
            self.discard(key)
            return None

    def put(
        self,
        key: str,
        plan: RequestPlan,
        *,
        ttl_seconds: int,
        max_entries: int,
    ) -> None:
        serialized_plan = plan.model_dump_json()
        expires_at = self._clock() + ttl_seconds
        with self._lock:
            self._entries[key] = (expires_at, serialized_plan)
            self._entries.move_to_end(key)
            while len(self._entries) > max_entries:
                self._entries.popitem(last=False)

    def discard(self, key: str) -> None:
        with self._lock:
            self._entries.pop(key, None)


plan_cache = PlanCache()
