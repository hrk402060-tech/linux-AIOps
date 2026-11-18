from datetime import datetime, timezone
import json

from fastapi.testclient import TestClient
import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.models.ai_agent import AgentRecommendation
from src.models.vulnerability import VulnerabilityCase, VulnerabilityFinding
from src.models.asset import Asset, AssetEnvironment, OsFamily
from src.services import external_vulnerability_ai


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


def _configure_external(client: TestClient, *, allow_egress: bool) -> None:
    saved = client.post('/api/v1/agent/providers/config', json={
        'provider_id': 'deepseek', 'api_key': 'test-vulnerability-provider-key', 'enabled': True,
    })
    assert saved.status_code == 201
    if allow_egress:
        policy = client.get('/api/v1/system/settings').json()['settings']['ai_policy']
        saved_policy = client.put('/api/v1/system/settings/ai_policy', json={
            **policy, 'external_vulnerability_enabled': True,
        })
        assert saved_policy.status_code == 200


def _completion(**decision: object) -> dict[str, object]:
    return {'choices': [{'finish_reason': 'stop', 'message': {
        'content': json.dumps(decision),
    }}]}


def test_external_triage_requires_explicit_egress_and_never_sends_host_evidence(
    client: TestClient, database_session: Session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    finding = _add_finding(database_session)
    calls: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=_completion(priority='normal', controls=['monitoring']))

    monkeypatch.setattr(external_vulnerability_ai, '_client', lambda: httpx.Client(
        transport=httpx.MockTransport(respond),
    ))
    payload = {'finding_id': finding.id, 'provider_id': 'deepseek'}
    _configure_external(client, allow_egress=False)
    assert client.post('/api/v1/agent/triage', json=payload).status_code == 403
    assert not calls
    _configure_external(client, allow_egress=True)
    result = client.post('/api/v1/agent/triage', json=payload)
    assert result.status_code == 201, result.text
    request = calls.pop()
    assert str(request.url) == 'https://api.deepseek.com/chat/completions'
    assert request.headers['authorization'] == 'Bearer test-vulnerability-provider-key'
    body = json.loads(request.content)
    assert 'tools' not in body
    assert json.loads(body['messages'][-1]['content']) == {
        'cve_id': 'CVE-2026-3001', 'severity': 'critical', 'conclusion': 'confirmed',
    }
    advice = result.json()
    assert advice['status'] == 'pending_review' and advice['can_execute'] is False
    assert advice['priority'] == 'urgent' and advice['provider_id'] == 'deepseek'
    assert '告警' in str(advice['remediation_steps'])


@pytest.mark.parametrize('reply, expected', [
    (httpx.Response(401, text='private upstream request details'), 502),
    (httpx.Response(429), 503),
    (httpx.Response(307, headers={'location': 'https://untrusted.example'}), 502),
    (httpx.Response(200, json=_completion(priority='high', controls=['not_a_control'])), 502),
    (httpx.Response(200, json=_completion(priority='high', controls=['monitoring'], instructions='ignored')), 502),
])
def test_external_triage_does_not_render_or_persist_invalid_provider_output(
    client: TestClient, database_session: Session, monkeypatch: pytest.MonkeyPatch,
    reply: httpx.Response, expected: int,
) -> None:
    finding = _add_finding(database_session)
    _configure_external(client, allow_egress=True)
    monkeypatch.setattr(external_vulnerability_ai, '_client', lambda: httpx.Client(
        transport=httpx.MockTransport(lambda _: reply),
    ))
    result = client.post('/api/v1/agent/triage', json={
        'finding_id': finding.id, 'provider_id': 'deepseek',
    })
    assert result.status_code == expected
    assert 'private upstream' not in result.text
    assert database_session.scalar(select(AgentRecommendation)) is None


def test_provider_test_uses_synthetic_input_and_does_not_create_advice(
    client: TestClient, database_session: Session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_external(client, allow_egress=False)

    def respond(request: httpx.Request) -> httpx.Response:
        messages = json.loads(request.content)['messages']
        assert json.loads(messages[-1]['content']) == {'connection_check': True}
        return httpx.Response(200, json=_completion(priority='review', controls=['monitoring']))

    monkeypatch.setattr(external_vulnerability_ai, '_client', lambda: httpx.Client(
        transport=httpx.MockTransport(respond),
    ))
    tested = client.post('/api/v1/agent/providers/config/deepseek/test')
    assert tested.status_code == 200
    assert tested.json()['network_called'] is True
    assert database_session.scalar(select(AgentRecommendation)) is None
