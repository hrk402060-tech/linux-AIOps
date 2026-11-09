from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from src.models.vulnerability import VulnerabilityCase, VulnerabilityFinding
from src.models.asset import Asset, AssetEnvironment, OsFamily


def _add_finding(session: Session) -> VulnerabilityFinding:
    session.add(Asset(id="asset-agent-1", name="openEuler gateway", host="192.0.2.10",
                      os_family=OsFamily.OPENEULER, environment=AssetEnvironment.TEST))
    session.add(VulnerabilityCase(id="case-agent-1", asset_id="asset-agent-1",
                                  asset_name="openEuler gateway", cve_id="CVE-2026-3001",
                                  status="open", audit_history=[]))
    session.flush()
    finding = VulnerabilityFinding(
        id="finding-agent-1",
        case_id="case-agent-1",
        scan_task_id="scan-agent-1",
        scan_task_name="生产网关扫描",
        asset_id="asset-agent-1",
        asset_name="openEuler gateway",
        knowledge_version="2026.09.04",
        cve_id="CVE-2026-3001",
        advisory_id="openEuler-SA-2026-3001",
        title="Kernel privilege boundary issue",
        severity="critical",
        package_name="kernel",
        installed_version="5.10.0-1",
        fixed_version="5.10.0-2",
        source_name="openEuler Security Advisories",
        vendor_url="https://example.test/openEuler-SA-2026-3001",
        conclusion="confirmed",
        confidence="high",
        evidence_chain=[
            {"type": "source", "value": "openEuler Security Advisories"},
            {"type": "advisory", "value": "openEuler-SA-2026-3001"},
            {"type": "installed_version", "value": "5.10.0-1"},
            {"type": "fixed_version", "value": "5.10.0-2"},
            {"type": "conclusion", "value": "confirmed"},
        ],
        detected_at=datetime.now(timezone.utc),
    )
    session.add(finding)
    session.commit()
    return finding


def test_agent_workspace_exposes_local_provider_and_findings(
    client: TestClient, database_session: Session
) -> None:
    _add_finding(database_session)

    response = client.get("/api/v1/agent")

    assert response.status_code == 200
    body = response.json()
    assert body["providers"][0] == {
        "id": "local-rules",
        "name": "本地研判引擎",
        "kind": "local",
        "status": "ready",
        "requires_api_key": False,
    }
    assert body["summary"]["pending_findings"] == 1
    assert body["findings"][0]["cve_id"] == "CVE-2026-3001"
    assert body["recommendations"] == []


def test_agent_triage_creates_auditable_advice_and_requires_review(
    client: TestClient, database_session: Session
) -> None:
    finding = _add_finding(database_session)

    response = client.post(
        "/api/v1/agent/triage",
        json={"finding_id": finding.id, "provider_id": "local-rules"},
    )

    assert response.status_code == 201
    advice = response.json()
    assert advice["finding_id"] == finding.id
    assert advice["status"] == "pending_review"
    assert advice["provider_id"] == "local-rules"
    assert advice["evidence_snapshot"][0]["type"] == "source"
    assert advice["remediation_steps"]
    assert advice["can_execute"] is False

    reviewed = client.patch(
        f"/api/v1/agent/recommendations/{advice['id']}/review",
        json={"decision": "approved"},
    )
    assert reviewed.status_code == 200
    assert reviewed.json()["status"] == "approved"
    assert reviewed.json()["reviewed_at"] is not None
    assert reviewed.json()["can_execute"] is False


def test_agent_rejects_unknown_finding_or_provider(client: TestClient) -> None:
    assert client.post(
        "/api/v1/agent/triage",
        json={"finding_id": "missing", "provider_id": "local-rules"},
    ).status_code == 404
    assert client.post(
        "/api/v1/agent/triage",
        json={"finding_id": "missing", "provider_id": "external-vendor"},
    ).status_code == 422


def test_provider_config_masks_external_api_key_and_never_returns_plaintext(client: TestClient) -> None:
    catalog = client.get('/api/v1/agent/providers/config')
    assert catalog.status_code == 200
    deepseek = next(item for item in catalog.json() if item['id'] == 'deepseek')
    assert deepseek['requires_api_key'] is True
    assert deepseek['configured'] is False

    saved = client.post('/api/v1/agent/providers/config', json={
        'provider_id': 'deepseek', 'api_key': 'deepseek-test-key-123456', 'enabled': True,
        'operator': 'security-admin',
    })
    assert saved.status_code == 201
    assert saved.json()['api_key_masked'] == '********'
    assert 'deepseek-test-key-123456' not in saved.text

    listed = client.get('/api/v1/agent/providers/config').json()
    configured = next(item for item in listed if item['id'] == 'deepseek')
    assert configured['enabled'] is True
    assert configured['api_key_masked'] == '********'


def test_local_provider_rejects_api_key_configuration(client: TestClient) -> None:
    response = client.post('/api/v1/agent/providers/config', json={
        'provider_id': 'local-rules', 'api_key': 'not-needed-key', 'operator': 'security-admin',
    })
    assert response.status_code == 400


def test_provider_config_can_validate_and_toggle_without_network(client: TestClient) -> None:
    client.post('/api/v1/agent/providers/config', json={
        'provider_id': 'openai', 'api_key': 'openai-test-key-123456', 'enabled': True,
        'operator': 'security-admin',
    })
    validated = client.post(
        '/api/v1/agent/providers/config/openai/validate', json={'operator': 'security-admin'}
    )
    assert validated.status_code == 200
    assert validated.json() == {
        'provider_id': 'openai', 'configured': True, 'enabled': True,
        'secret_exposed': False, 'network_called': False,
        'message': '服务端密钥可解密；未发起外部网络请求',
    }
    disabled = client.patch(
        '/api/v1/agent/providers/config/openai/enabled',
        json={'enabled': False, 'operator': 'security-admin'},
    )
    assert disabled.status_code == 200
    assert disabled.json()['enabled'] is False

    audit = client.get(
        '/api/v1/audit-log?source=ai_provider_config&operator=break-glass-admin'
    ).json()
    assert audit['total'] == 3
    assert {item['action'] for item in audit['items']} == {
        'configured', 'validated', 'disabled',
    }
    assert 'openai-test-key-123456' not in str(audit)


def test_provider_config_normalizes_operator_whitespace(client: TestClient) -> None:
    response = client.post('/api/v1/agent/providers/config', json={
        'provider_id': 'openai', 'api_key': 'openai-test-key-123456',
        'enabled': True, 'operator': '  security-admin  ',
    })
    assert response.status_code == 201

    audit = client.get('/api/v1/audit-log?source=ai_provider_config').json()
    assert audit['items'][0]['operator'] == 'break-glass-admin'
