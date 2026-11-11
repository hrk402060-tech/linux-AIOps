from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
import os
from threading import Barrier

import pytest
from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from src.database import Base, create_session_factory
from src.models.knowledge import (
    KnowledgeAdvisory, KnowledgeRelease, KnowledgeSource, KnowledgeSyncRun,
)
from src.models.platform_setting import PlatformSetting
from src.services import knowledge, knowledge_publish
from test_knowledge_multi_source import package

DATABASE_URL = os.environ.get("KNOWLEDGE_TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="requires isolated PostgreSQL")


@pytest.fixture
def factory() -> Generator[sessionmaker[Session], None, None]:
    url = make_url(DATABASE_URL)
    if url.get_backend_name() != "postgresql" or url.database != "qa_knowledge":
        pytest.fail("Knowledge concurrency tests require a dedicated qa_knowledge database")
    sessions = create_session_factory(DATABASE_URL)
    engine = sessions.kw["bind"]
    tables = [model.__table__ for model in (
        PlatformSetting, KnowledgeSource, KnowledgeRelease, KnowledgeAdvisory, KnowledgeSyncRun,
    )]
    Base.metadata.create_all(engine, tables=tables)
    with sessions() as session:
        knowledge.ensure_sources(session)
    try:
        yield sessions
    finally:
        Base.metadata.drop_all(engine, tables=tables)
        engine.dispose()


def test_concurrent_first_sync_claims_only_one_run(factory: sessionmaker[Session]) -> None:
    barrier = Barrier(2)

    def claim() -> str:
        with factory() as session:
            source = session.scalar(select(KnowledgeSource).where(
                KnowledgeSource.name == "Ubuntu Security Notices",
            ))
            barrier.wait(timeout=10)
            try:
                knowledge_publish.start_source_run(session, source, "official_https_api")
            except knowledge.KnowledgeConflictError:
                return "conflict"
            return "started"

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(claim) for _ in range(2)]
        assert sorted(future.result(timeout=30) for future in futures) == ["conflict", "started"]
    with factory() as session:
        assert len(list(session.scalars(select(KnowledgeSyncRun)))) == 1


def test_concurrent_sources_preserve_both_snapshots(factory: sessionmaker[Session]) -> None:
    prepared: list[tuple[str, str, str]] = []
    with factory() as session:
        for name, family in [("Ubuntu Security Notices", "ubuntu"), ("Red Hat Security Advisories", "rhel")]:
            source = session.scalar(select(KnowledgeSource).where(KnowledgeSource.name == name))
            run = knowledge_publish.start_source_run(session, source, "official_https_api")
            prepared.append((source.id, run.id, family))
    barrier = Barrier(2)

    def publish(item: tuple[str, str, str]) -> None:
        source_id, run_id, family = item
        with factory() as session:
            source = session.get(KnowledgeSource, source_id)
            run = session.get(KnowledgeSyncRun, run_id)
            barrier.wait(timeout=10)
            knowledge_publish.publish_source_package(
                session, source, package(source.name, family, "CVE-2026-12345"),
                run, "official-https-adapter",
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(publish, item) for item in prepared]
        for future in futures:
            future.result(timeout=30)
    with factory() as session:
        active = list(session.scalars(select(KnowledgeRelease).where(
            KnowledgeRelease.status == "active",
        )))
        assert len(active) == 1
        assert {row.os_family for row in active[0].advisories} == {"ubuntu", "rhel"}
