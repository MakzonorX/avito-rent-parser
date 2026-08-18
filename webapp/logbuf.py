"""Кольцевой буфер логов loguru для отображения в веб-интерфейсе."""
import threading
from collections import deque
from datetime import datetime

from loguru import logger

_MAX_RECORDS = 800
_records: deque = deque(maxlen=_MAX_RECORDS)
_lock = threading.Lock()
_counter = 0
_sink_id = None


def _sink(message) -> None:
    global _counter
    record = message.record
    with _lock:
        _counter += 1
        _records.append(
            {
                "seq": _counter,
                "time": record["time"].strftime("%H:%M:%S"),
                "level": record["level"].name,
                "message": record["message"],
            }
        )


def install() -> None:
    """Подключает буфер к loguru (один раз за процесс)."""
    global _sink_id
    if _sink_id is None:
        _sink_id = logger.add(_sink, level="INFO", format="{message}", enqueue=False)


def tail(after: int = 0, limit: int = 300) -> list[dict]:
    with _lock:
        items = [r for r in _records if r["seq"] > after]
    return items[-limit:]


def last_seq() -> int:
    with _lock:
        return _counter


def clear() -> None:
    with _lock:
        _records.clear()
