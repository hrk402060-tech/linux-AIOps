from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.services import scan_engine
from src.services.scan_verification import record_remediation_check, verified_versions


def _previous(package: str = 'kernel') -> SimpleNamespace:
    return SimpleNamespace(
        package_name=package, cve_id='CVE-2026-9001', advisory_id='SA-9001',
        source_name='openEuler Security Advisories', fixed_version='2',
    )


def _check(package: str = 'kernel') -> dict[str, object]:
    return {
        'package_name': package, 'cve_id': 'CVE-2026-9001', 'advisory_id': 'SA-9001',
        'source_name': 'openEuler Security Advisories', 'fixed_version': '2',
        'installed_version': '2', 'outcome': 'not_vulnerable',
    }


def test_verification_requires_all_original_packages() -> None:
    previous = [_previous(), _previous('openssl')]
    assert verified_versions(previous, [_check()], {'kernel': '2'}) is None
    assert verified_versions(previous, [_check(), _check('openssl')], {
        'kernel': '2', 'openssl': '2',
    }) == 'kernel=2; openssl=2'


@pytest.mark.parametrize('field,value', [
    ('advisory_id', 'different'), ('source_name', 'CNVD'),
    ('fixed_version', '1'), ('outcome', 'unknown'), ('installed_version', '3'),
])
def test_changed_or_uncertain_evidence_cannot_close_case(field: str, value: str) -> None:
    check = {**_check(), field: value}
    assert verified_versions([_previous()], [check], {'kernel': '2'}) is None


def test_absence_of_findings_is_not_remediation_evidence() -> None:
    assert verified_versions([_previous()], [], {'kernel': '2'}) is None
    assert verified_versions([_previous()], [_check()], {}) is None
    removed = {**_check(), 'installed_version': None, 'outcome': 'not_installed'}
    assert verified_versions([_previous()], [removed], {}) == 'not_installed'


def test_only_explicit_vendor_resolution_is_recorded() -> None:
    checks: list[dict[str, object]] = []
    advisory = _previous()
    package = {'name': 'kernel', 'fixed_version': '2'}
    record_remediation_check(checks, advisory, package, '2', None)
    record_remediation_check(checks, advisory, package, '1', True)
    record_remediation_check(checks, SimpleNamespace(**{
        **vars(advisory), 'source_name': 'CNVD',
    }), package, '2', False)
    assert checks == []
    record_remediation_check(checks, advisory, package, '2', False)
    assert checks == [_check()]


@pytest.mark.parametrize('manager,code,expected', [
    ('dpkg', 0, True), ('dpkg', 1, False), ('dpkg', 2, None),
    ('rpm', 0, False), ('rpm', 1, True), ('rpm', 2, None),
])
def test_native_comparison_errors_remain_unknown(
    manager: str, code: int, expected: bool | None,
) -> None:
    stdout = SimpleNamespace(channel=SimpleNamespace(recv_exit_status=lambda: code))
    client = SimpleNamespace(exec_command=lambda *args, **kwargs: (None, stdout, None))
    assert scan_engine._is_older(client, manager, 'kernel', '1', '2') is expected


def test_matching_collects_only_applicable_vendor_remediation_checks() -> None:
    advisory = SimpleNamespace(
        **vars(_previous()), os_family='openeuler',
        affected_packages=[{'name': 'kernel', 'fixed_version': '2', 'os_versions': ['24.03']}],
    )
    checks: list[dict[str, object]] = []
    with patch('src.services.scan_engine._is_older', return_value=False):
        assert scan_engine._match_advisories(
            object(), 'rpm', {'kernel': '2'}, [advisory], 'openeuler',
            os_version='24.03', remediation_checks=checks,
        ) == []
    assert checks == [_check()]
    checks.clear()
    scan_engine._match_advisories(
        object(), 'rpm', {}, [advisory], 'openeuler', os_version='22.03',
        remediation_checks=checks,
    )
    assert checks == []
