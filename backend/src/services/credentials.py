from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.models.credential import Credential
from src.models.asset import Asset
from src.schemas.credential import CredentialCreate, CredentialUpdate
from src.services.crypto import CredentialVault


class CredentialNameConflictError(ValueError):
    """Raised when a credential name is already in use."""


class CredentialInUseError(ValueError):
    """Raised when a credential is assigned to one or more assets."""


def list_credentials(session: Session) -> list[Credential]:
    statement = select(Credential).order_by(Credential.updated_at.desc())
    return list(session.scalars(statement))


def get_credential(session: Session, credential_id: str) -> Credential | None:
    return session.get(Credential, credential_id)


def create_credential(
    session: Session, payload: CredentialCreate, vault: CredentialVault
) -> Credential:
    credential_id = str(uuid4())
    encrypted, nonce = vault.encrypt(payload.secret, credential_id, payload.credential_type.value)
    credential = Credential(
        id=credential_id,
        name=payload.name,
        credential_type=payload.credential_type,
        username=payload.username,
        encrypted_secret=encrypted,
        nonce=nonce,
        key_version=vault.key_version,
    )
    session.add(credential)
    _commit_or_conflict(session)
    session.refresh(credential)
    return credential


def update_credential(
    session: Session, credential: Credential, payload: CredentialUpdate, vault: CredentialVault
) -> Credential:
    values = payload.model_dump(exclude_unset=True)
    if "name" in values:
        credential.name = values["name"]
    if "username" in values:
        credential.username = values["username"]
    if "secret" in values:
        encrypted, nonce = vault.encrypt(
            values["secret"], credential.id, credential.credential_type.value
        )
        credential.encrypted_secret = encrypted
        credential.nonce = nonce
        credential.key_version = vault.key_version
    _commit_or_conflict(session)
    session.refresh(credential)
    return credential


def delete_credential(session: Session, credential: Credential) -> None:
    usage_count = session.scalar(
        select(func.count(Asset.id)).where(Asset.credential_id == credential.id)
    )
    if usage_count:
        raise CredentialInUseError("Credential is assigned to assets")
    session.delete(credential)
    session.commit()


def _commit_or_conflict(session: Session) -> None:
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise CredentialNameConflictError("Credential name already exists") from exc
