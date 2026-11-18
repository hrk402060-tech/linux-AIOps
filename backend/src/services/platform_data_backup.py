import base64
import hashlib
import json
import os
from pathlib import Path
import stat
from uuid import UUID

from cryptography.exceptions import InvalidTag
from sqlalchemy.engine import Engine

from src.config import Settings
from src.services.crypto import CredentialCryptoError, CredentialVault
from src.services.platform_data_snapshot import (
    MAX_SNAPSHOT_BYTES, SnapshotError, canonical, capture, verify_restore,
)

STORAGE_ROOT = Path("/var/lib/linux-ops")
SECRET_TYPE = "platform_database_backup"
MAX_ARTIFACT_BYTES = MAX_SNAPSHOT_BYTES * 2


def backup_directory(value: str) -> Path:
    path = Path(value)
    root = STORAGE_ROOT.resolve()
    if not path.is_absolute() or ".." in path.parts or path == STORAGE_ROOT:
        raise SnapshotError("backup_path_invalid")
    if not path.is_relative_to(STORAGE_ROOT) or not path.resolve().is_relative_to(root):
        raise SnapshotError("backup_path_invalid")
    current = path
    while current != STORAGE_ROOT.parent:
        if current.is_symlink():
            raise SnapshotError("backup_path_symlink")
        if current == STORAGE_ROOT:
            break
        current = current.parent
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.stat().st_mode & 0o077:
        raise SnapshotError("backup_path_permissions")
    return path


def _read(directory: Path, filename: str) -> bytes:
    if Path(filename).name != filename or not filename.endswith(".aiops"):
        raise SnapshotError("backup_path_invalid")
    descriptor = os.open(directory / filename, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_ARTIFACT_BYTES:
            raise SnapshotError("backup_file_size_limit")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            return stream.read(MAX_ARTIFACT_BYTES + 1)
    finally:
        os.close(descriptor)


def create_backup(
    engine: Engine, settings: Settings, vault: CredentialVault,
    identifier: str, configured_path: str,
) -> dict[str, object]:
    identifier = str(UUID(identifier))
    directory = backup_directory(configured_path)
    snapshot = capture(engine, settings)
    content = canonical(snapshot).decode("utf-8")
    ciphertext, nonce = vault.encrypt(content, identifier, SECRET_TYPE)
    envelope = canonical({
        "format": "linux-ops-encrypted-v1", "id": identifier,
        "key_version": vault.key_version,
        "nonce": base64.b64encode(nonce).decode("ascii"),
        "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
    })
    filename = f"{identifier}.aiops"
    result: dict[str, object] = {
        "filename": filename, "sha256": hashlib.sha256(envelope).hexdigest(),
        "bytes": len(envelope), "key_version": vault.key_version,
    }
    descriptor = os.open(directory / filename,
                         os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(envelope)
            stream.flush()
            os.fsync(stream.fileno())
        # Read back and decrypt the actual disk artifact before reporting success.
        result.update(verify_backup(vault, identifier, str(directory), result))
    except BaseException:
        (directory / filename).unlink(missing_ok=True)
        raise
    return result


def verify_backup(
    vault: CredentialVault, identifier: str, configured_path: str,
    reference: dict[str, object],
) -> dict[str, object]:
    directory = backup_directory(configured_path)
    raw = _read(directory, str(reference["filename"]))
    if hashlib.sha256(raw).hexdigest() != reference["sha256"]:
        raise SnapshotError("backup_integrity_failed")
    try:
        envelope = json.loads(raw)
        if envelope["format"] != "linux-ops-encrypted-v1" or envelope["id"] != identifier:
            raise SnapshotError("backup_identity_mismatch")
        plaintext = vault.decrypt(
            base64.b64decode(envelope["ciphertext"], validate=True),
            base64.b64decode(envelope["nonce"], validate=True),
            identifier, SECRET_TYPE, envelope["key_version"],
        )
        snapshot = json.loads(plaintext)
    except (ValueError, KeyError, TypeError, InvalidTag, CredentialCryptoError) as error:
        raise SnapshotError("backup_decrypt_failed") from error
    return verify_restore(snapshot)
