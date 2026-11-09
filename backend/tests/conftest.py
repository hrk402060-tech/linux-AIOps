import base64
from collections.abc import Generator
import os
from pathlib import Path
import sys

from fastapi.testclient import TestClient
import pytest
from sqlalchemy.orm import Session

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))
os.environ.setdefault(
    "CREDENTIAL_MASTER_KEY", base64.b64encode(bytes(range(32))).decode("ascii")
)
os.environ.setdefault("API_ADMIN_TOKEN", "test-admin-token-with-at-least-32-characters")

@pytest.fixture
def client(tmp_path: Path) -> Generator[TestClient, None, None]:
    from src.config import Settings
    from src.main import create_app

    encoded_key = base64.b64encode(bytes(range(32))).decode("ascii")
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'credentials.db'}",
        credential_master_key=encoded_key,
        credential_key_version="test-v1",
        api_admin_token="test-admin-token-with-at-least-32-characters",
        auto_create_schema=True,
        diagnostic_workers_enabled=False,
    )
    with TestClient(
        create_app(settings),
        headers={"Authorization": "Bearer test-admin-token-with-at-least-32-characters"},
    ) as test_client:
        yield test_client


@pytest.fixture
def database_session(client: TestClient) -> Generator[Session, None, None]:
    session = client.app.state.session_factory()
    try:
        yield session
    finally:
        session.close()
