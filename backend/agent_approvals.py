"""Bounded in-memory approval queue for live local agent runs."""
from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass, field


MAX_PENDING_APPROVALS = 32
APPROVAL_TIMEOUT_SECONDS = 180


@dataclass
class _PendingApproval:
    session_id: str
    decision: bool | None = None
    created_at: float = field(default_factory=time.monotonic)
    ready: threading.Event = field(default_factory=threading.Event)


class ApprovalRegistry:
    """Keep pending decisions bounded and scoped to one agent session."""

    def __init__(self, max_pending: int = MAX_PENDING_APPROVALS, timeout: float = APPROVAL_TIMEOUT_SECONDS):
        self.max_pending = max(1, int(max_pending))
        self.timeout = max(1.0, float(timeout))
        self._pending: dict[str, _PendingApproval] = {}
        self._lock = threading.Lock()

    def _prune(self) -> None:
        now = time.monotonic()
        expired = [key for key, item in self._pending.items() if now - item.created_at > self.timeout]
        for key in expired:
            item = self._pending.pop(key)
            item.decision = False
            item.ready.set()

    def request(self, session_id: str, event: dict, emit) -> bool:
        with self._lock:
            self._prune()
            if len(self._pending) >= self.max_pending:
                raise ValueError("The approval queue is full. Resolve a pending action and try again.")
            approval_id = secrets.token_urlsafe(24)
            pending = _PendingApproval(session_id=session_id)
            self._pending[approval_id] = pending

        try:
            emit({**event, "approval_id": approval_id, "session_id": session_id})
            decided = pending.ready.wait(self.timeout)
            if not decided:
                return False
            return pending.decision is True
        finally:
            with self._lock:
                self._pending.pop(approval_id, None)

    def resolve(self, approval_id: str, session_id: str, decision: bool) -> bool:
        if not isinstance(approval_id, str) or not 20 <= len(approval_id) <= 100:
            return False
        with self._lock:
            self._prune()
            pending = self._pending.get(approval_id)
            if pending is None or pending.session_id != session_id or pending.decision is not None:
                return False
            pending.decision = bool(decision)
            pending.ready.set()
            return True


_registry = ApprovalRegistry()


def get_approval_registry() -> ApprovalRegistry:
    return _registry
