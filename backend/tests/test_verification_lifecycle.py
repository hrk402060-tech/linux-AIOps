import pytest
from fastapi.testclient import TestClient

from test_vulnerability_lifecycle import _asset, _credential, _finding, _scan
from test_vulnerability_lifecycle import _registered_host_key  # noqa: F401


@pytest.mark.parametrize('scan_type,checks,status', [
    ('comprehensive', [], 'pending_verification'),
    ('service_vulnerability', [{**_finding(), 'installed_version': '5.10.0-2',
                                'outcome': 'not_vulnerable'}], 'pending_verification'),
    ('host_vulnerability', [{**_finding(), 'installed_version': '5.10.0-2',
                             'outcome': 'not_vulnerable'}], 'fixed'),
    ('comprehensive', [{**_finding(), 'source_name': 'CNVD',
                       'installed_version': '5.10.0-2',
                       'outcome': 'not_vulnerable'}], 'pending_verification'),
])
def test_verification_scope_and_evidence_are_required(
    client: TestClient, scan_type: str, checks: list[dict[str, object]], status: str,
) -> None:
    asset = _asset(client, _credential(client), 'verification-scope', '192.0.2.81')
    _scan(client, asset, [_finding()])
    finding = client.get('/api/v1/vulnerabilities').json()['items'][0]
    assert client.patch(f"/api/v1/vulnerabilities/{finding['id']}/disposition", json={
        'status': 'pending_verification', 'reason': '人工实施补丁后等待验证',
        'operator': 'ignored-client-identity',
    }).status_code == 200
    task = _scan(client, asset, [], {'kernel': '5.10.0-2'}, checks, scan_type)
    detail = client.get(f"/api/v1/vulnerabilities/{finding['id']}").json()
    assert detail['disposition_status'] == status
    assert detail['verification_scan_id'] == (task['id'] if status == 'fixed' else None)
    assert detail['disposition_history'][-1]['operator'] == (
        'system:scan' if status == 'fixed' else 'break-glass-admin'
    )
    assert task['result_summary']['asset_results'][0]['remediation_checks'] == checks
