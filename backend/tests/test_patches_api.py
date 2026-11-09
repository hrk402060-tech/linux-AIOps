from fastapi.testclient import TestClient
from datetime import datetime, timezone

import pytest
from sqlalchemy.orm import Session

from src.models.vulnerability import VulnerabilityCase, VulnerabilityFinding


def test_patch_advice_is_read_only_and_empty_without_findings(client: TestClient) -> None:
    response = client.get('/api/v1/patches')
    assert response.status_code == 200
    assert response.json() == []


def test_patch_advice_links_finding_and_keeps_execution_disabled(
    client: TestClient, database_session
) -> None:
    database_session.add(
        VulnerabilityCase(
            id='case-1', asset_id='asset-1', asset_name='web-01',
            cve_id='CVE-2026-0001', status='open', audit_history=[],
        )
    )
    database_session.add(
        VulnerabilityFinding(
            id='finding-1', case_id='case-1', scan_task_id='scan-1', scan_task_name='nightly',
            asset_id='asset-1', asset_name='web-01', knowledge_version='kb-1',
            cve_id='CVE-2026-0001', advisory_id='ADV-1', title='Test advisory',
            severity='high', package_name='openssl', installed_version='1.0',
            fixed_version='1.1', source_name='Vendor advisory',
            vendor_url='https://vendor.example/advisory', conclusion='confirmed',
            confidence='high', evidence_chain=[{'type': 'source', 'value': 'vendor'}],
            detected_at=datetime.now(timezone.utc),
        )
    )
    database_session.commit()

    response = client.get('/api/v1/patches')

    assert response.status_code == 200
    item = response.json()[0]
    assert item['asset_id'] == 'asset-1'
    assert item['cve_id'] == 'CVE-2026-0001'
    assert item['status'] == 'pending_approval'
    assert item['execution_enabled'] is False
    approved = client.post(
        '/api/v1/patches/finding-1/approve',
        json={
            'operator': 'security-admin',
            'reason': '已完成变更，等待验证',
            'confirmation': 'APPROVE finding-1',
        },
    )
    assert approved.status_code == 200
    assert approved.json()['status'] == 'pending_verification'
    assert approved.json()['execution_enabled'] is False

    rejected = client.post(
        '/api/v1/patches/finding-1/approve',
        json={
            'operator': 'security-admin', 'reason': '再次确认', 'confirmation': 'wrong',
        },
    )
    assert rejected.status_code == 400


def test_patch_advice_returns_latest_finding_per_asset_and_cve(
    client: TestClient, database_session
) -> None:
    case = VulnerabilityCase(
        id='case-2', asset_id='asset-2', asset_name='db-01',
        cve_id='CVE-2026-0002', status='open', audit_history=[],
    )
    database_session.add(case)
    for finding_id, version, detected_at in (
        ('finding-old', '1.0', datetime(2026, 1, 1, tzinfo=timezone.utc)),
        ('finding-new', '1.1', datetime(2026, 1, 2, tzinfo=timezone.utc)),
    ):
        database_session.add(VulnerabilityFinding(
            id=finding_id, case_id='case-2', scan_task_id=f'scan-{finding_id}',
            scan_task_name=finding_id, asset_id='asset-2', asset_name='db-01',
            knowledge_version='kb-1', cve_id='CVE-2026-0002', advisory_id='ADV-2',
            title='Database advisory', severity='medium', package_name='db',
            installed_version=version, fixed_version='2.0', source_name='Vendor',
            vendor_url=None, conclusion='confirmed', confidence='high',
            evidence_chain=[{'type': 'source', 'value': 'vendor'}], detected_at=detected_at,
        ))
    database_session.commit()

    response = client.get('/api/v1/patches')
    rows = [item for item in response.json() if item['cve_id'] == 'CVE-2026-0002']
    assert len(rows) == 1
    assert rows[0]['finding_id'] == 'finding-new'
    assert rows[0]['current_version'] == '1.1'
    assert rows[0]['history_count'] == 2


def test_patch_advice_marks_lower_target_version_and_blocks_approval(
    client: TestClient, database_session
) -> None:
    database_session.add(VulnerabilityCase(
        id='case-invalid-target', asset_id='asset-invalid-target',
        asset_name='web-invalid', cve_id='CVE-2026-0003',
        status='open', audit_history=[],
    ))
    database_session.add(VulnerabilityFinding(
        id='finding-invalid-target', case_id='case-invalid-target',
        scan_task_id='scan-invalid-target', scan_task_name='nightly',
        asset_id='asset-invalid-target', asset_name='web-invalid',
        knowledge_version='kb-1', cve_id='CVE-2026-0003', advisory_id='ADV-3',
        title='Invalid target version', severity='high', package_name='openssl',
        installed_version='2.0.0', fixed_version='1.9.0',
        source_name='Vendor', vendor_url=None, conclusion='confirmed',
        confidence='high', evidence_chain=[], detected_at=datetime.now(timezone.utc),
    ))
    database_session.commit()

    item = next(item for item in client.get('/api/v1/patches').json()
                if item['finding_id'] == 'finding-invalid-target')
    assert item['status'] == 'invalid_target'
    assert item['target_version_relation'] == 'lower_than_current'
    assert item['target_version_issue'] == '目标版本低于当前版本，禁止生成补丁建议'
    rejected = client.post('/api/v1/patches/finding-invalid-target/approve', json={
        'reason': '人工确认', 'confirmation': 'APPROVE finding-invalid-target',
    })
    assert rejected.status_code == 409
    assert rejected.json()['detail'] == '目标版本低于当前版本，禁止审批补丁建议'


@pytest.mark.parametrize(("current", "target", "conclusion", "status"), [
    ("1:1.0", "2.0", "confirmed", "manual_confirmation"),
    ("1.0.1", "1.0-2", "confirmed", "manual_confirmation"),
    ("1.0", "1.0", "confirmed", "invalid_target"),
    ("1.0", "2.0", "manual_confirmation", "manual_confirmation"),
])
def test_uncertain_or_equal_patch_targets_cannot_be_approved(
    client: TestClient, database_session: Session,
    current: str, target: str, conclusion: str, status: str,
) -> None:
    database_session.add(VulnerabilityCase(
        id="case-uncertain", asset_id="asset-uncertain", asset_name="host",
        cve_id="CVE-2026-0004", status="open", audit_history=[],
    ))
    database_session.add(VulnerabilityFinding(
        id="finding-uncertain", case_id="case-uncertain", scan_task_id="scan-uncertain",
        scan_task_name="nightly", asset_id="asset-uncertain", asset_name="host",
        knowledge_version="kb-1", cve_id="CVE-2026-0004", advisory_id="ADV-4",
        title="Uncertain patch", severity="high", package_name="openssl",
        installed_version=current, fixed_version=target, source_name="Vendor",
        vendor_url=None, conclusion=conclusion, confidence="medium",
        evidence_chain=[], detected_at=datetime.now(timezone.utc),
    ))
    database_session.commit()
    item = client.get("/api/v1/patches").json()[0]
    assert item["status"] == status and item["target_version_issue"]
    rejected = client.post("/api/v1/patches/finding-uncertain/approve", json={
        "reason": "人工确认", "confirmation": "APPROVE finding-uncertain",
    })
    assert rejected.status_code == 409
    database_session.expire_all()
    assert database_session.get(VulnerabilityCase, "case-uncertain").status == "open"
