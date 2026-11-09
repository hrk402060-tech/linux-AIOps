"""Exercise only a disposable PostgreSQL database, never a deployed database."""

import os
from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.orm import Session

from src.models.auth import User  # noqa: F401
from src.models.cmdb import CmdbChangeAudit
from src.models.platform_setting import PlatformSetting
from src.services.probe_policy import save_policy


def main() -> None:
    url = os.environ["DATABASE_URL"]
    if os.environ.get("TRUST_MIGRATION_ISOLATED") != "1" or "@trust-postgres/" not in url:
        raise RuntimeError("Migration verification requires the disposable trust-postgres target")
    config = Config("/app/alembic.ini")
    command.upgrade(config, "20261022_42")
    engine = create_engine(url)
    original_tables = set(inspect(engine).get_table_names())
    marker_id = str(uuid4())
    with Session(engine) as session:
        session.add(CmdbChangeAudit(
            id=marker_id, entity_type="migration_check", entity_id=marker_id,
            action="retain", operator_username="isolated-check", trace_id=str(uuid4()),
            before_snapshot=None, after_snapshot={"retained": True},
        ))
        session.commit()
    command.upgrade(config, "20261024_43")
    assert set(inspect(engine).get_table_names()) - original_tables == {"platform_settings"}
    with Session(engine) as session:
        save_policy(session, "192.0.2.0/24", "isolated-admin")
    engine.dispose()
    with Session(engine) as session:
        policy = session.get(PlatformSetting, "probe_allowed_cidrs")
        assert policy is not None and policy.value == "192.0.2.0/24"
    with engine.connect() as connection:
        audits = connection.execute(select(CmdbChangeAudit.__table__).order_by(CmdbChangeAudit.id)).all()
    command.downgrade(config, "20261022_42")
    assert set(inspect(engine).get_table_names()) == original_tables
    with engine.connect() as connection:
        assert connection.execute(select(CmdbChangeAudit.__table__).order_by(CmdbChangeAudit.id)).all() == audits
    command.upgrade(config, "20261024_43")
    with Session(engine) as session:
        assert session.get(PlatformSetting, "probe_allowed_cidrs") is None
        assert session.get(CmdbChangeAudit, marker_id).after_snapshot == {"retained": True}
    engine.dispose()
    print("PostgreSQL 42 -> 43 -> 42 -> 43 passed; existing audit evidence retained")


if __name__ == "__main__":
    main()
