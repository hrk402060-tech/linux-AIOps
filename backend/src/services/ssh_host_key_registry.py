from datetime import datetime, timezone
from uuid import uuid4

import paramiko

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.models.asset import Asset
from src.models.ssh_host_key import SshHostKey
from src.models.ssh_host_key_audit import SshHostKeyAudit
from src.schemas.ssh_host_key import SshHostKeyConfirm
from src.services import ssh_host_keys
from src.services.scan_engine import ScanCollectionError


class HostKeyConfirmationError(ValueError):
    pass


class HostKeyRegistrationError(ScanCollectionError):
    pass


def get_registered(session: Session, asset: Asset) -> SshHostKey | None:
    return session.scalar(select(SshHostKey).where(SshHostKey.asset_id == asset.id))


def require_registered(session: Session, asset: Asset) -> paramiko.PKey:
    record = get_registered(session, asset)
    if record is None:
        raise HostKeyRegistrationError(
            "未登记 SSH 主机指纹，请先在资产管理中获取并人工确认"
        )
    if record.host != asset.host or record.port != asset.ssh_port:
        raise HostKeyRegistrationError(
            "SSH 地址已变化，请重新获取并人工确认主机指纹"
        )
    try:
        return ssh_host_keys.host_key_from_record(record)
    except ValueError as exc:
        raise HostKeyRegistrationError(str(exc)) from exc


def ensure_registered(
    session: Session, asset: Asset, operator: str = "system:auto",
) -> paramiko.PKey:
    """Pin a first-use host key without silently rotating an existing pin."""
    item = get_registered(session, asset)
    if item is not None:
        return require_registered(session, asset)
    try:
        candidate = ssh_host_keys.probe_host_key(asset.host, asset.ssh_port)
    except (OSError, ssh_host_keys.paramiko.SSHException, ValueError) as exc:
        raise HostKeyRegistrationError(
            "无法获取 SSH 主机指纹，请确认服务器可达"
        ) from exc
    item = SshHostKey(
        id=str(uuid4()), asset_id=asset.id, host=asset.host, port=asset.ssh_port,
        key_type=candidate.key_type, fingerprint=candidate.fingerprint,
        public_key=candidate.public_key, confirmed_by=operator,
        confirmed_at=datetime.now(timezone.utc),
    )
    session.add(item)
    session.flush()
    session.add(SshHostKeyAudit(
        id=str(uuid4()), asset_id=asset.id, asset_name=asset.name,
        host=asset.host, port=asset.ssh_port, key_type=candidate.key_type,
        action="auto_registered", fingerprint=candidate.fingerprint,
        operator=operator, detail="资产首次只读采集时自动登记 SSH 主机密钥",
    ))
    return ssh_host_keys.host_key_from_record(item)


def confirm(
    session: Session, asset: Asset, payload: SshHostKeyConfirm, operator: str,
) -> SshHostKey:
    try:
        candidate = ssh_host_keys.verify_candidate(asset.host, asset.ssh_port, payload.fingerprint)
    except (OSError, ssh_host_keys.paramiko.SSHException, ValueError) as exc:
        raise HostKeyConfirmationError(str(exc)) from exc
    item = get_registered(session, asset)
    action = "confirmed"
    if item is None:
        item = SshHostKey(id=str(uuid4()), asset_id=asset.id, host=asset.host, port=asset.ssh_port)
        session.add(item)
    elif item.fingerprint == candidate.fingerprint:
        action = "reconfirmed"
    else:
        action = "rotated"
    item.host = asset.host
    item.port = asset.ssh_port
    item.key_type = candidate.key_type
    item.fingerprint = candidate.fingerprint
    item.public_key = candidate.public_key
    item.confirmed_by = operator
    item.confirmed_at = datetime.now(timezone.utc)
    session.flush()
    session.add(SshHostKeyAudit(
        id=str(uuid4()), asset_id=asset.id, asset_name=asset.name,
        host=asset.host, port=asset.ssh_port, key_type=candidate.key_type,
        action=action,
        fingerprint=candidate.fingerprint, operator=operator,
        detail="人工确认后登记 SSH 主机密钥",
    ))
    session.commit()
    session.refresh(item)
    return item
