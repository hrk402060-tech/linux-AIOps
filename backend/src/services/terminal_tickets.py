from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from secrets import token_urlsafe
from threading import Lock


@dataclass(frozen=True)
class TerminalTicket:
    session_id: str
    asset_id: str
    expires_at: datetime


class TerminalTicketRegistry:
    def __init__(self, ttl_seconds: int = 30) -> None:
        self._ttl = ttl_seconds
        self._tickets: dict[str, TerminalTicket] = {}
        self._lock = Lock()

    def issue(self, session_id: str, asset_id: str) -> tuple[str, datetime]:
        raw = token_urlsafe(32)
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=self._ttl)
        with self._lock:
            self._prune()
            self._tickets[self._digest(raw)] = TerminalTicket(
                session_id=session_id, asset_id=asset_id, expires_at=expires_at
            )
        return raw, expires_at

    def consume(self, raw: str) -> TerminalTicket | None:
        with self._lock:
            self._prune()
            ticket = self._tickets.pop(self._digest(raw), None)
        return ticket

    def discard(self, raw: str) -> None:
        with self._lock:
            self._tickets.pop(self._digest(raw), None)

    def _prune(self) -> None:
        now = datetime.now(timezone.utc)
        expired = [key for key, value in self._tickets.items() if value.expires_at <= now]
        for key in expired:
            self._tickets.pop(key, None)

    @staticmethod
    def _digest(raw: str) -> str:
        return sha256(raw.encode("utf-8")).hexdigest()
