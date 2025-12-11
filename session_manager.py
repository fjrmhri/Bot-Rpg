import asyncio
import time
from collections import OrderedDict
from typing import Callable, Dict, Optional, Tuple, TypeVar

from config import CONFIG

StateT = TypeVar("StateT")


class SessionManager:
    """LRU + TTL session cache for per-user state and locks."""

    def __init__(
        self,
        ttl_seconds: int = CONFIG.session_ttl,
        max_size: int = CONFIG.session_max_size,
        on_evict: Optional[Callable[[int, StateT], None]] = None,
    ):
        self.ttl = ttl_seconds
        self.max_size = max_size
        self.states: "OrderedDict[int, StateT]" = OrderedDict()
        self.locks: Dict[int, asyncio.Lock] = {}
        self.last_access: Dict[int, float] = {}
        self.on_evict = on_evict

    def _evict(self, user_id: int) -> None:
        state = self.states.pop(user_id, None)
        self.locks.pop(user_id, None)
        self.last_access.pop(user_id, None)
        if state is not None and self.on_evict:
            try:
                self.on_evict(user_id, state)
            except Exception:
                # eviction should never raise in the hot path
                pass

    def _cleanup(self) -> None:
        now = time.time()
        expired = [uid for uid, ts in self.last_access.items() if now - ts > self.ttl]
        for uid in expired:
            self._evict(uid)
        while len(self.states) > self.max_size:
            uid, _ = self.states.popitem(last=False)
            self.locks.pop(uid, None)
            self.last_access.pop(uid, None)
            if self.on_evict:
                try:
                    self.on_evict(uid, _)
                except Exception:
                    pass

    def get_lock(self, user_id: int) -> asyncio.Lock:
        self.last_access[user_id] = time.time()
        self._cleanup()
        lock = self.locks.get(user_id)
        if lock is None:
            lock = asyncio.Lock()
            self.locks[user_id] = lock
        # LRU bump
        if user_id in self.states:
            self.states.move_to_end(user_id)
        return lock

    def get_state(self, user_id: int, factory: Callable[[int], StateT]) -> StateT:
        self.last_access[user_id] = time.time()
        self._cleanup()
        state = self.states.get(user_id)
        if state is None:
            state = factory(user_id)
            self.states[user_id] = state
        else:
            self.states.move_to_end(user_id)
        return state

    def set_state(self, user_id: int, state: StateT) -> None:
        self.last_access[user_id] = time.time()
        self.states[user_id] = state
        self.states.move_to_end(user_id)
        self._cleanup()

    def touch(self, user_id: int) -> None:
        if user_id in self.last_access:
            self.last_access[user_id] = time.time()
            self._cleanup()

    def clear(self) -> None:
        for uid in list(self.states.keys()):
            self._evict(uid)


# A global session manager instance will be created by the bot module
__all__ = ["SessionManager"]
