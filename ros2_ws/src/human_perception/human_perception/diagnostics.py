"""Helpers for building ``diagnostic_msgs`` statuses."""

from typing import Any, Mapping

from diagnostic_msgs.msg import DiagnosticArray, DiagnosticStatus, KeyValue


def _as_int(level: Any) -> int:
    return level[0] if isinstance(level, (bytes, bytearray)) else int(level)


# Plain ints internally; Humble exposes the constants (and the field) as bytes.
OK = _as_int(DiagnosticStatus.OK)
WARN = _as_int(DiagnosticStatus.WARN)
ERROR = _as_int(DiagnosticStatus.ERROR)
STALE = _as_int(DiagnosticStatus.STALE)


def _fmt(value: Any) -> str:
    if value is None:
        return 'n/a'
    if isinstance(value, float):
        return f'{value:.3f}'
    return str(value)


def make_status(name: str, level: int, message: str, values: Mapping[str, Any],
                hardware_id: str = 'human_perception') -> DiagnosticStatus:
    status = DiagnosticStatus()
    status.name = name
    status.hardware_id = hardware_id
    level = _as_int(level)
    status.level = bytes([level]) if isinstance(status.level, bytes) else level
    status.message = message
    status.values = [KeyValue(key=str(k), value=_fmt(v)) for k, v in values.items()]
    return status


def make_array(stamp: Any, *statuses: DiagnosticStatus) -> DiagnosticArray:
    arr = DiagnosticArray()
    arr.header.stamp = stamp
    arr.status = list(statuses)
    return arr


def level_value(status: DiagnosticStatus) -> int:
    return _as_int(status.level)
