from fastapi.testclient import TestClient

from src.services.credential_risks import execute_task


def test_offline_risk_assessment_never_returns_secret(client: TestClient) -> None:
    secret = "root123"
    credential = client.post('/api/v1/credentials', json={'name': 'risk-password', 'credential_type': 'password', 'username': 'root', 'secret': secret}).json()
    created = client.post('/api/v1/credential-risks', json={'name': '托管密码风险检查', 'credential_ids': [credential['id']]})
    assert created.status_code == 201
    assert created.json()['offline_only'] is True
    task_id = created.json()['id']
    with client.app.state.session_factory() as session:
        from src.models.credential_risk import CredentialRiskTask
        from src.services.credential_risks import start_task
        start_task(session, session.get(CredentialRiskTask, task_id))
    execute_task(client.app.state.session_factory, task_id, client.app.state.credential_vault)
    response = client.get('/api/v1/credential-risks')
    body = response.text
    assert secret not in body
    assert response.json()[0]['results'][0]['risk_level'] == 'high'
    assert response.json()[0]['results'][0]['status'] == 'at_risk'


def test_private_key_is_rejected(client: TestClient) -> None:
    credential = client.post('/api/v1/credentials', json={'name': 'risk-key', 'credential_type': 'private_key', 'username': 'root', 'secret': 'key-material'}).json()
    response = client.post('/api/v1/credential-risks', json={'name': 'invalid type', 'credential_ids': [credential['id']]})
    assert response.status_code == 409
