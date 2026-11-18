"""Bounded portable logical snapshots; verification never connects to production."""
import base64
from datetime import date, datetime, time, timezone
from decimal import Decimal
from enum import Enum
import hashlib
import json
from pathlib import Path
import tempfile
import time as clock
from typing import Any

from pydantic import SecretStr
from sqlalchemy import (
    Boolean, CheckConstraint, Column, Date, DateTime, Float, ForeignKeyConstraint,
    Integer, JSON, LargeBinary, MetaData, Numeric, String, Table, Text, Time,
    UniqueConstraint, cast, create_engine, func, inspect, select,
)
from sqlalchemy.engine import Connection, Engine

from src.config import Settings

MAX_SNAPSHOT_BYTES = 16 * 1024 * 1024
MAX_ROWS = 100_000
MAX_ROW_BYTES = 2 * 1024 * 1024
MAX_CAPTURE_SECONDS = 300
FORMAT = "linux-ops-logical-v1"


class SnapshotError(ValueError):
    pass


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("utf-8")


def _encode(value: object) -> object:
    if isinstance(value, bytes):
        return base64.b64encode(value).decode("ascii")
    if isinstance(value, datetime):
        aware = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return aware.astimezone(timezone.utc).isoformat()
    if isinstance(value, (date, time)):
        return value.isoformat()
    if isinstance(value, (Decimal, Enum)):
        return str(value.value) if isinstance(value, Enum) else str(value)
    return value


def _kind(sql_type: Any) -> str:
    for cls, name in (
        (Boolean, "boolean"), (DateTime, "datetime"), (Date, "date"),
        (Time, "time"), (LargeBinary, "binary"), (JSON, "json"),
        (Integer, "integer"), (Float, "float"), (Numeric, "numeric"),
        (String, "text"),
    ):
        if isinstance(sql_type, cls):
            return name
    raise SnapshotError("unsupported_column_type")


def _column_schema(column: Column, connection: Connection) -> dict[str, object]:
    options = {
        name: getattr(column.type, name)
        for name in ("length", "precision", "scale", "timezone")
        if getattr(column.type, name, None) is not None
    }
    default = column.server_default
    return {
        "name": column.name, "kind": _kind(column.type),
        "nullable": column.nullable, "primary": column.primary_key,
        "source_type": str(column.type.compile(dialect=connection.dialect)),
        "type_options": options,
        "server_default": str(getattr(default, "arg", repr(default))) if default is not None else None,
        "enum_values": list(getattr(column.type, "enums", ())),
        "identity": repr(column.identity) if column.identity is not None else None,
    }


def _schema(table: Table, connection: Connection) -> dict[str, object]:
    return {
        "name": table.name,
        "columns": [_column_schema(column, connection) for column in table.columns],
        "foreign_keys": [
            {"columns": [item.parent.name for item in constraint.elements],
             "targets": [f"{item.column.table.name}.{item.column.name}"
                         for item in constraint.elements]}
            for constraint in table.foreign_key_constraints
        ],
        "unique_constraints": [
            {"name": constraint.name, "columns": [column.name for column in constraint.columns]}
            for constraint in table.constraints if isinstance(constraint, UniqueConstraint)
        ],
        "check_constraints": [
            {"name": constraint.name, "sql": str(constraint.sqltext)}
            for constraint in table.constraints if isinstance(constraint, CheckConstraint)
        ],
        "indexes": [
            {"name": index.name, "unique": index.unique,
             "expressions": [str(expression) for expression in index.expressions]}
            for index in table.indexes
        ],
    }


def digest_rows(rows: list[dict[str, object]]) -> str:
    hashes = sorted(hashlib.sha256(canonical(row)).hexdigest() for row in rows)
    return hashlib.sha256("".join(hashes).encode("ascii")).hexdigest()


def _capture_table(connection: Connection, table: Table) -> dict[str, object]:
    # Check text/binary sizes in SQL before bringing any potentially huge row into memory.
    size = sum(func.coalesce(func.length(cast(column, Text)), 0) for column in table.columns)
    if connection.scalar(select(1).select_from(table).where(
        size > MAX_ROW_BYTES
    ).limit(1)) is not None:
        raise SnapshotError("backup_row_size_limit")
    rows: list[dict[str, object]] = []
    size_bytes = 0
    result = connection.execute(select(table).execution_options(stream_results=True))
    for row in result.mappings():
        encoded = {key: _encode(value) for key, value in row.items()}
        size_bytes += len(canonical(encoded))
        if len(rows) >= MAX_ROWS or size_bytes > MAX_SNAPSHOT_BYTES:
            result.close()
            raise SnapshotError("backup_size_limit")
        rows.append(encoded)
    return {**_schema(table, connection), "rows": rows, "row_count": len(rows), "sha256": digest_rows(rows)}


def _check_transactional_tables(connection: Connection, names: set[str]) -> None:
    if connection.dialect.name not in {"mysql", "mariadb"}:
        return
    inspector = inspect(connection)
    for name in names:
        options = inspector.get_table_options(name)
        if str(options.get("mysql_engine", options.get("mariadb_engine", ""))).lower() != "innodb":
            raise SnapshotError("backup_requires_transactional_tables")


def capture(engine: Engine, settings: Settings) -> dict[str, Any]:
    dialect = engine.dialect.name
    if dialect not in {"sqlite", "postgresql", "mysql", "mariadb"}:
        raise SnapshotError("backup_database_unsupported")
    config = {
        key: value.get_secret_value() if isinstance(value, SecretStr) else value
        for key, value in settings.model_dump().items() if key != "credential_master_key"
    }
    snapshot: dict[str, Any] = {
        "format": FORMAT, "source_dialect": dialect,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "config": config, "requires_original_master_key": True, "tables": [],
    }
    with engine.connect() as connection:
        if dialect != "sqlite":
            connection = connection.execution_options(isolation_level="REPEATABLE READ")
            connection.begin()
        else:
            connection.exec_driver_sql("BEGIN")
        try:
            if dialect == "postgresql":
                connection.exec_driver_sql("SET TRANSACTION READ ONLY")
                connection.exec_driver_sql("SET LOCAL statement_timeout = '120s'")
            metadata = MetaData()
            metadata.reflect(bind=connection)
            names = set(inspect(connection).get_table_names())
            if names != set(metadata.tables):
                raise SnapshotError("backup_metadata_incomplete")
            _check_transactional_tables(connection, names)
            total_rows = total_bytes = 0
            started = clock.monotonic()
            for table in metadata.tables.values():
                if clock.monotonic() - started > MAX_CAPTURE_SECONDS:
                    raise SnapshotError("backup_capture_timeout")
                remaining = MAX_ROWS - total_rows
                count = connection.scalar(select(func.count()).select_from(
                    select(table.c[next(iter(table.columns)).name]).limit(remaining + 1).subquery()
                )) or 0
                if count > remaining:
                    raise SnapshotError("backup_row_count_limit")
                data = _capture_table(connection, table)
                total_rows += data["row_count"]
                total_bytes += len(canonical(data))
                if total_bytes > MAX_SNAPSHOT_BYTES:
                    raise SnapshotError("backup_size_limit")
                snapshot["tables"].append(data)
        finally:
            connection.rollback()
    if len(canonical(snapshot)) > MAX_SNAPSHOT_BYTES:
        raise SnapshotError("backup_size_limit")
    return snapshot


_TYPES = {
    "boolean": Boolean, "datetime": DateTime, "date": Date, "time": Time,
    "binary": LargeBinary, "json": JSON, "integer": Integer, "float": Float,
    "numeric": Numeric, "text": Text,
}


def _restore_schema(snapshot: dict[str, Any]) -> MetaData:
    metadata = MetaData()
    for data in snapshot["tables"]:
        columns = [
            Column(item["name"], _TYPES[item["kind"]](**item.get("type_options", {})),
                   nullable=item["nullable"], primary_key=item["primary"])
            for item in data["columns"]
        ]
        constraints = [ForeignKeyConstraint(item["columns"], item["targets"])
                       for item in data["foreign_keys"]]
        constraints.extend(UniqueConstraint(*item["columns"])
                           for item in data.get("unique_constraints", []))
        Table(data["name"], metadata, *columns, *constraints)
    return metadata


def _decode(value: object, kind: str) -> object:
    if value is None:
        return None
    if kind == "binary":
        return base64.b64decode(value, validate=True)
    if kind == "datetime":
        return datetime.fromisoformat(value)
    if kind == "date":
        return date.fromisoformat(value)
    if kind == "time":
        return time.fromisoformat(value)
    if kind == "numeric":
        return Decimal(value)
    return value


def verify_restore(snapshot: dict[str, Any]) -> dict[str, object]:
    if snapshot.get("format") != FORMAT or len(canonical(snapshot)) > MAX_SNAPSHOT_BYTES:
        raise SnapshotError("backup_format_or_size_limit")
    if sum(len(item["rows"]) for item in snapshot["tables"]) > MAX_ROWS:
        raise SnapshotError("backup_row_count_limit")
    for data in snapshot["tables"]:
        if len(data["rows"]) != data["row_count"] or digest_rows(data["rows"]) != data["sha256"]:
            raise SnapshotError("backup_integrity_failed")
    metadata = _restore_schema(snapshot)
    with tempfile.TemporaryDirectory(prefix="linux-ops-restore-") as directory:
        path = Path(directory) / "verify.sqlite"
        path.touch(mode=0o600)
        target = create_engine(f"sqlite:///{path}")
        try:
            metadata.create_all(target)
            _restore_rows(target, metadata, snapshot)
        finally:
            target.dispose()
    return {"verified": True, "verification_target": "isolated_sqlite",
            "source_dialect": snapshot["source_dialect"],
            "native_engine_restore_verified": False,
            "format": FORMAT,
            "table_count": len(snapshot["tables"]),
            "row_count": sum(item["row_count"] for item in snapshot["tables"]),
            "requires_original_master_key": True}


def _restore_rows(target: Engine, metadata: MetaData, snapshot: dict[str, Any]) -> None:
    with target.begin() as connection:
        for data in snapshot["tables"]:
            table = metadata.tables[data["name"]]
            kinds = {item["name"]: item["kind"] for item in data["columns"]}
            for offset in range(0, len(data["rows"]), 200):
                values = [{key: _decode(value, kinds[key]) for key, value in row.items()}
                          for row in data["rows"][offset:offset + 200]]
                connection.execute(table.insert(), values)
        if connection.exec_driver_sql("PRAGMA foreign_key_check").first() is not None:
            raise SnapshotError("backup_restore_foreign_key_failed")
        for data in snapshot["tables"]:
            rows = [{key: _encode(value) for key, value in row.items()}
                    for row in connection.execute(select(metadata.tables[data["name"]])).mappings()]
            if len(rows) != data["row_count"] or digest_rows(rows) != data["sha256"]:
                raise SnapshotError("backup_restore_integrity_failed")
