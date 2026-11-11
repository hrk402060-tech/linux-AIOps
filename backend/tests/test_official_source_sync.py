from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select

from src.models.knowledge import KnowledgeSource
from src.services import knowledge, knowledge_publish, official_source_sync
from test_knowledge_multi_source import package


@pytest.mark.parametrize("source_name,family", [
    ("Ubuntu Security Notices", "ubuntu"),
    ("Debian Security Advisories", "debian"),
    ("Red Hat Security Advisories", "rhel"),
    ("Anolis Security Advisories", "anolis"),
    ("Upstream Service Advisories", "other"),
])
def test_registered_adapter_can_sync_through_existing_admin_api(
    client: TestClient, source_name: str, family: str,
) -> None:
    sources = client.get("/api/v1/knowledge/sources").json()
    source = next(item for item in sources if item["name"] == source_name)
    assert source["online_url"].startswith("https://")
    adapter = Mock(return_value=package(source_name, family, "CVE-2026-12345"))
    with patch.dict(official_source_sync.SOURCE_ADAPTERS, {source_name: adapter}):
        response = client.post(f"/api/v1/knowledge/sources/{source['id']}/sync")
    assert response.status_code == 200
    assert response.json()["source_name"] == source_name
    assert response.json()["status"] == "completed"
    assert client.get("/api/v1/knowledge/advisories").json()[0]["source_name"] == source_name


def test_official_failure_records_failure_and_preserves_previous_snapshot(client: TestClient) -> None:
    source = next(item for item in client.get("/api/v1/knowledge/sources").json()
                  if item["name"] == "Red Hat Security Advisories")
    adapter = Mock(return_value=package(source["name"], "rhel", "CVE-2026-12345"))
    with patch.dict(official_source_sync.SOURCE_ADAPTERS, {source["name"]: adapter}):
        assert client.post(f"/api/v1/knowledge/sources/{source['id']}/sync").status_code == 200
        before = client.get("/api/v1/knowledge/summary").json()
        adapter.side_effect = knowledge.KnowledgeImportError("官方接口暂时不可达")
        failed = client.post(f"/api/v1/knowledge/sources/{source['id']}/sync")
    assert failed.status_code == 422
    assert client.get("/api/v1/knowledge/summary").json() == before
    assert client.get("/api/v1/knowledge/sync-runs").json()[0]["status"] == "failed"
    coverage = client.get("/api/v1/knowledge/coverage").json()
    assert next(row for row in coverage["sources"] if row["source_id"] == source["id"])["health"] == "failed"


def test_offline_source_cannot_be_enabled_as_unsigned_official_connector(client: TestClient) -> None:
    source = next(item for item in client.get("/api/v1/knowledge/sources").json()
                  if item["name"] == "CNVD")
    client.app.state.knowledge_connectors = {"CNVD": {"package_url": "https://example.test"}}
    with patch("src.services.knowledge_sync.sync_source") as connector:
        response = client.post(f"/api/v1/knowledge/sources/{source['id']}/sync")
    assert response.status_code == 409
    connector.assert_not_called()


def test_disabled_official_source_rejects_sync(client: TestClient) -> None:
    with client.app.state.session_factory() as session:
        source = session.scalar(select(KnowledgeSource).where(
            KnowledgeSource.name == "Red Hat Security Advisories",
        ))
        source.enabled = False
        session.commit()
        identity = source.id
    adapter = Mock()
    with patch.dict(official_source_sync.SOURCE_ADAPTERS, {"Red Hat Security Advisories": adapter}):
        assert client.post(f"/api/v1/knowledge/sources/{identity}/sync").status_code == 409
    adapter.assert_not_called()


def test_sync_mutex_rejects_duplicate_and_recovers_expired_run(client: TestClient) -> None:
    with client.app.state.session_factory() as session:
        source = session.scalar(select(KnowledgeSource).where(
            KnowledgeSource.name == "Red Hat Security Advisories",
        ))
        run = knowledge_publish.start_source_run(session, source, "official_https_api")
        with pytest.raises(knowledge.KnowledgeConflictError):
            knowledge_publish.start_source_run(session, source, "official_https_api")
        run.started_at = datetime.now(timezone.utc) - timedelta(hours=2)
        session.commit()
        new = knowledge_publish.start_source_run(session, source, "official_https_api")
        session.refresh(run)
        assert new.id != run.id
        assert run.status == "failed"
        assert run.finished_at is not None
        with pytest.raises(knowledge.KnowledgeConflictError):
            knowledge_publish.publish_source_package(
                session, source, package(source.name, "rhel", "CVE-2026-12345"),
                run, "official-https-adapter",
            )
