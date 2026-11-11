from datetime import datetime, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import select

from src.models.knowledge import KnowledgeRelease, KnowledgeSource
from src.schemas.knowledge import AdvisoryPackage, AffectedPackage, OfflineKnowledgePackage
from src.services import knowledge_publish


def package(source: str, family: str, cve: str) -> OfflineKnowledgePackage:
    now = datetime(2026, 11, 1, tzinfo=timezone.utc)
    return OfflineKnowledgePackage(
        schema_version="1.0", package_id=f"{family}-batch", version=f"{family}-batch",
        name=source, source_name=source, published_at=now,
        advisories=[AdvisoryPackage(
            cve_id=cve, advisory_id=f"SA-{cve}", title="Vendor notice", severity="high",
            os_family=family, vendor_url="https://example.test/security",
            affected_packages=[AffectedPackage(
                name="openssl", affected_version="< 3.0-4", fixed_version="3.0-4",
                os_versions=["12"],
            )], published_at=now, updated_at=now,
        )],
    )


def test_incremental_multi_source_publications_retain_previous_evidence(client: TestClient) -> None:
    client.get("/api/v1/knowledge/sources")
    factory = client.app.state.session_factory
    with factory() as session:
        for source_name, family, cve in [
            ("openEuler Security Advisories", "openeuler", "CVE-2026-10001"),
            ("Ubuntu Security Notices", "ubuntu", "CVE-2026-10002"),
            ("Ubuntu Security Notices", "ubuntu", "CVE-2026-10003"),
        ]:
            source = session.scalar(select(KnowledgeSource).where(KnowledgeSource.name == source_name))
            run = knowledge_publish.start_source_run(session, source, "official_https_api")
            knowledge_publish.publish_source_package(
                session, source, package(source_name, family, cve), run, "official-https-api",
            )
        active = list(session.scalars(select(KnowledgeRelease).where(KnowledgeRelease.status == "active")))
        assert len(active) == 1
        assert active[0].version.startswith("knowledge-")
        assert len(active[0].advisories) == 3


def test_latest_unchanged_source_does_not_reactivate_older_cross_source_snapshot(client: TestClient) -> None:
    client.get("/api/v1/knowledge/sources")
    with client.app.state.session_factory() as session:
        source = session.scalar(select(KnowledgeSource).where(KnowledgeSource.name == "Ubuntu Security Notices"))
        incoming = package(source.name, "ubuntu", "CVE-2026-10002")
        first = knowledge_publish.start_source_run(session, source, "official_https_api")
        knowledge_publish.publish_source_package(session, source, incoming, first, "official-https-api")
        second = knowledge_publish.start_source_run(session, source, "official_https_api")
        with patch("src.services.knowledge_publish.knowledge.build_release") as build:
            knowledge_publish.publish_source_package(session, source, incoming, second, "official-https-api")
        build.assert_not_called()
        assert second.status == "completed"


def test_unchanged_source_after_another_source_keeps_both_in_active_release(client: TestClient) -> None:
    with client.app.state.session_factory() as session:
        for source_name, family in [
            ("Ubuntu Security Notices", "ubuntu"),
            ("Red Hat Security Advisories", "rhel"),
            ("Ubuntu Security Notices", "ubuntu"),
        ]:
            source = session.scalar(select(KnowledgeSource).where(KnowledgeSource.name == source_name))
            run = knowledge_publish.start_source_run(session, source, "official_https_api")
            knowledge_publish.publish_source_package(
                session, source, package(source_name, family, "CVE-2026-12345"),
                run, "official-https-adapter",
            )
        releases = list(session.scalars(select(KnowledgeRelease)))
        assert len(releases) == 2
        active = next(release for release in releases if release.status == "active")
        assert {item.os_family for item in active.advisories} == {"ubuntu", "rhel"}
