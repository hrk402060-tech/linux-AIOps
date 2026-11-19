import os
import subprocess
import sys
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, func, inspect, select
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session

from src.auth import AuthPrincipal
from src.config import Settings
from src.database import Base
from src.main import create_app  # noqa: F401 -- register every referenced model when run alone
from src.models.auth import User
from src.models.kubernetes_cluster import KubernetesCluster, KubernetesClusterAudit
from src.models.kubernetes_helm_approval import KubernetesHelmApproval
from src.schemas.kubernetes_cluster import KubernetesClusterPatch
from src.services import kubernetes_clusters as store
from src.services import kubernetes_helm_approvals as approvals
from src.services.kubernetes_cluster_tests import consume_test, register_test
from src.services.kubernetes_kubeconfig import KubernetesClusterError, ParsedKubeconfig
from src.services.kubernetes_overview_jobs import db_now


@pytest.fixture
def isolated_pg() -> Generator[tuple[Engine, str], None, None]:
    value = os.environ.get("K8S_PG_TEST_URL")
    if not value:
        pytest.skip("Requires a dedicated PostgreSQL test container")
    schema = f"k8s_test_{uuid4().hex}"
    root = create_engine(value)
    with root.begin() as connection:
        connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    url = make_url(value).update_query_dict({"options": f"-csearch_path={schema}"})
    engine = create_engine(url)
    try:
        yield engine, url.render_as_string(hide_password=False)
    finally:
        engine.dispose()
        with root.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        root.dispose()


def test_postgres_receipts_and_revision_are_atomic(isolated_pg: tuple[Engine, str]) -> None:
    engine, _ = isolated_pg
    Base.metadata.create_all(engine)
    parsed = ParsedKubeconfig(
        "https://192.0.2.20:6443", "selected", "token", "reader",
        None, "synthetic-test-token", None, None, None, None,
    )
    digest = "a" * 64
    with Session(engine) as session:
        receipt = register_test(session, parsed, {"status": "passed", "target_policy_digest": digest}, "a")
        session.add(KubernetesCluster(
            id="concurrent-cluster", name="concurrent", endpoint=parsed.endpoint, context=parsed.context,
        ))
        session.commit()
    barrier = Barrier(2)

    def consume() -> bool:
        with Session(engine) as session:
            barrier.wait(timeout=10)
            try:
                consume_test(session, receipt, parsed, "a", digest)
                session.commit()
                return True
            except KubernetesClusterError:
                session.rollback()
                return False

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(consume) for _ in range(2)]
        assert sorted(future.result(timeout=15) for future in futures) == [False, True]
    barrier = Barrier(2)

    def rename(name: str) -> bool:
        with Session(engine) as session:
            row = session.get(KubernetesCluster, "concurrent-cluster")
            barrier.wait(timeout=10)
            try:
                store.patch_cluster(
                    session, row, KubernetesClusterPatch(name=name, expected_revision=1), "a", "test",
                )
                return True
            except store.KubernetesClusterConflict:
                return False

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(rename, name) for name in ("first", "second")]
        assert sorted(future.result(timeout=15) for future in futures) == [False, True]


def test_postgres_migration_roundtrip(isolated_pg: tuple[Engine, str]) -> None:
    engine, url = isolated_pg
    root = Path(__file__).resolve().parents[1]
    parsed_url = make_url(url)
    env = {
        **os.environ,
        "DATABASE_URL": parsed_url.difference_update_query(["options"]).render_as_string(hide_password=False),
        "PGOPTIONS": parsed_url.query["options"],
    }

    def migrate(*arguments: str) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "alembic", *arguments], cwd=root, env=env,
            capture_output=True, text=True, check=False, timeout=60,
        )
        assert result.returncode == 0, result.stderr

    migrate("upgrade", "20261110_51")
    before = set(inspect(engine).get_table_names())
    migrate("upgrade", "head")
    added = {"kubernetes_clusters", "kubernetes_cluster_credentials",
             "kubernetes_cluster_audits", "kubernetes_connection_tests", "kubernetes_collection_runs",
             "kubernetes_resource_snapshots", "platform_data_jobs", "kubernetes_helm_approvals"}
    assert set(inspect(engine).get_table_names()) == before | added
    migrate("downgrade", "20261110_51")
    assert set(inspect(engine).get_table_names()) == before


def test_postgres_helm_decision_is_atomic(
    isolated_pg: tuple[Engine, str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, _ = isolated_pg
    Base.metadata.create_all(engine)
    monkeypatch.setattr(approvals, "_validate_current", lambda *_args: None)
    with Session(engine) as session:
        now = db_now(session)
        session.add_all([
            KubernetesCluster(id="cluster", name="demo", endpoint="https://192.0.2.20:6443", context="demo"),
            User(id="requester", username="requester", role="admin", password_hash="unused", enabled=True),
        ])
        session.flush()
        session.add(KubernetesHelmApproval(
            id="candidate", cluster_id="cluster", cluster_revision=1, credential_revision=1, policy_digest="a" * 64,
            source_digest="b" * 64, manifest_digest="c" * 64, chart={"id": "demo"}, resources=[],
            namespace="demo", release_name="demo", status="pending", requester_id="requester",
            requested_by="requester", created_at=now, submitted_at=now, expires_at=now + timedelta(minutes=5),
        ))
        session.commit()
    barrier = Barrier(2)
    settings = Settings.model_construct()

    def decide(username: str) -> str:
        with Session(engine, expire_on_commit=False) as session:
            barrier.wait(timeout=10)
            try:
                principal = AuthPrincipal(username, "admin", username, "session")
                approvals.transition(session, "cluster", "candidate", settings, principal,
                                     approvals.ApprovalDecision(confirmation="yes"))
            except HTTPException as error:
                assert error.status_code == 409
                return "conflict"
            return "approved"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(decide, ["first", "second"], timeout=20))
    assert sorted(results) == ["approved", "conflict"]
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(KubernetesClusterAudit)) == 1
