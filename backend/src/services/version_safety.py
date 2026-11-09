import re
from typing import Literal


VersionRelation = Literal[
    "lower_than_current", "equal_to_current", "higher_than_current", "unknown"
]

_NUMERIC_VERSION = re.compile(r"^(?:0|[1-9][0-9]*)(?:\.(?:0|[1-9][0-9]*))*$")


def compare_obvious_versions(current: str, target: str) -> VersionRelation:
    """Classify only versions whose ordering is unambiguous from numeric parts."""
    current_value, target_value = current.strip(), target.strip()
    if not current_value or not target_value:
        return "unknown"
    if not _NUMERIC_VERSION.fullmatch(current_value) or not _NUMERIC_VERSION.fullmatch(target_value):
        return "unknown"
    current_parts = _parts(current_value)
    target_parts = _parts(target_value)
    if len(current_parts) != len(target_parts):
        return "unknown"
    if target_parts < current_parts:
        return "lower_than_current"
    if target_parts == current_parts:
        return "equal_to_current"
    return "higher_than_current"


def _parts(value: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", value))
