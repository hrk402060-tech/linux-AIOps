"""Run inside the backend image; no production data or credentials are used."""
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

from src.models.scan_task import ScanType
from src.services import scan_engine
from src.services.scan_verification import verified_versions


def main() -> None:
    advisory = SimpleNamespace(
        cve_id='CVE-2026-9001', advisory_id='SA-9001',
        source_name='openEuler Security Advisories', os_family='openeuler',
        affected_packages=[{'name': 'kernel', 'fixed_version': '2', 'os_versions': ['24.03']}],
    )
    previous = SimpleNamespace(**vars(advisory), package_name='kernel', fixed_version='2')
    checks: list[dict[str, object]] = []
    with patch('src.services.scan_engine._is_older', return_value=False):
        findings = scan_engine._match_advisories(
            object(), 'rpm', {'kernel': '2'}, [advisory], 'openeuler',
            scan_type=ScanType.COMPREHENSIVE, os_version='24.03', remediation_checks=checks,
        )
    assert findings == [] and verified_versions([previous], checks, {'kernel': '2'}) == '2'
    assert verified_versions([previous], [], {'kernel': '2'}) is None
    assert verified_versions([previous], checks, {'kernel': '1'}) is None
    with tempfile.TemporaryDirectory() as directory:
        environment = {**os.environ, 'DATABASE_URL': f'sqlite:///{Path(directory) / "verify.db"}'}
        subprocess.run(['alembic', 'upgrade', 'head'], env=environment, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print('Docker remediation evidence and fresh migration: passed')


if __name__ == '__main__':
    main()
