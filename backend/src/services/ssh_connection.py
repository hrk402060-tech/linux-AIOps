from io import StringIO

import paramiko
from sqlalchemy import select
from sqlalchemy.orm import object_session

from src.models.asset import Asset
from src.models.credential import Credential, CredentialType
from src.models.ssh_host_key import SshHostKey
from src.services.platform_settings import connection_policy
from src.services.ssh_host_keys import host_key_from_record


def parse_private_key(secret: str) -> paramiko.PKey:
    for key_class in (paramiko.Ed25519Key, paramiko.RSAKey, paramiko.ECDSAKey):
        try:
            return key_class.from_private_key(StringIO(secret))
        except paramiko.SSHException:
            continue
    raise ValueError("不支持或无效的 SSH 私钥")


def open_client(
    asset: Asset, credential: Credential, secret: str,
    timeout: float | None = None,
    *, host_key: paramiko.PKey | None = None,
) -> paramiko.SSHClient:
    session = object_session(asset) if isinstance(asset, Asset) else None
    policy = connection_policy(session) if session is not None else None
    host_key = configured_host_key(asset, host_key)
    budget = timeout if timeout is not None else policy.connect_timeout_seconds if policy else 10
    attempts = 1 + (policy.retry_count if policy else 0)
    for attempt in range(attempts):
        try:
            client = _connect_once(asset, credential, secret, budget, host_key)
        except (paramiko.AuthenticationException, paramiko.BadHostKeyException, ValueError):
            raise
        except (OSError, paramiko.SSHException):
            if attempt + 1 >= attempts:
                raise
            continue
        transport = client.get_transport()
        if transport is not None and policy is not None:
            transport.set_keepalive(policy.heartbeat_seconds)
        return client
    raise RuntimeError("SSH connection attempts exhausted")


def configured_host_key(asset: Asset, supplied: paramiko.PKey | None = None) -> paramiko.PKey | None:
    session = object_session(asset) if isinstance(asset, Asset) else None
    if session is None or connection_policy(session).host_key_policy != "registered_only":
        return supplied
    record = session.scalar(select(SshHostKey).where(SshHostKey.asset_id == asset.id))
    if record is None or (record.host, record.port) != (asset.host, asset.ssh_port):
        raise ValueError("已启用主机密钥严格校验，此资产没有匹配当前地址的已登记密钥")
    return host_key_from_record(record)


def _connect_once(
    asset: Asset, credential: Credential, secret: str, timeout: float,
    host_key: paramiko.PKey | None,
) -> paramiko.SSHClient:
    client = paramiko.SSHClient()
    if host_key is None:
        # The default direct-connect policy does not persist unknown keys.
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    else:
        host = asset.host if asset.ssh_port == 22 else f"[{asset.host}]:{asset.ssh_port}"
        client.get_host_keys().add(host, host_key.get_name(), host_key)
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
    try:
        client.connect(
            hostname=asset.host,
            port=asset.ssh_port,
            username=credential.username,
            password=secret if credential.credential_type == CredentialType.PASSWORD else None,
            pkey=parse_private_key(secret) if credential.credential_type == CredentialType.PRIVATE_KEY else None,
            timeout=timeout,
            banner_timeout=timeout,
            auth_timeout=timeout,
            look_for_keys=False,
            allow_agent=False,
        )
    except (OSError, paramiko.SSHException, ValueError):
        client.close()
        raise
    return client
