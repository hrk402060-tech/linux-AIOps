import pytest

from src.services.version_safety import compare_obvious_versions


@pytest.mark.parametrize(("current", "target", "expected"), [
    ("2.0.0", "1.9.9", "lower_than_current"),
    ("1.9.9", "2.0.0", "higher_than_current"),
    ("1.0", "1.0", "equal_to_current"),
    ("1:1.0", "2.0", "unknown"),
    ("1.0.1", "1.0-2", "unknown"),
    ("1.0-2", "1.0-3", "unknown"),
    ("1.0+1", "1.0+2", "unknown"),
    ("1.0", "1.0.0", "unknown"),
    ("1", "1.0", "unknown"),
    ("1.01", "1.1", "unknown"),
    ("1.1", "1.2.el9", "unknown"),
    ("1.0", "", "unknown"),
    ("1..0", "2.0", "unknown"),
    ("1:2", "2:1", "unknown"),
])
def test_numeric_version_guard_is_conservative(current: str, target: str, expected: str) -> None:
    assert compare_obvious_versions(current, target) == expected
