from threading import Event, Lock


class TerminalConnectionRegistry:
    def __init__(self, maximum: int) -> None:
        self._maximum = maximum
        self._connections: dict[str, Event] = {}
        self._lock = Lock()

    def acquire(self, session_id: str) -> Event | None:
        with self._lock:
            if len(self._connections) >= self._maximum:
                return None
            stop = Event()
            self._connections[session_id] = stop
            return stop

    def release(self, session_id: str) -> None:
        with self._lock:
            self._connections.pop(session_id, None)

    def terminate(self, session_id: str) -> bool:
        with self._lock:
            stop = self._connections.get(session_id)
            if stop is None:
                return False
            stop.set()
            return True
