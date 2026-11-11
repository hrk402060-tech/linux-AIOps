from io import StringIO

import paramiko

from src.models.asset import Asset
from src.models.credential import Credential, CredentialType


def parse_private_key(secret: str) -> paramiko.PKey:
    for key_class in (paramiko.Ed25519Key, paramiko.RSAKey, paramiko.ECDSAKey):
        try:
            return key_class.from_private_key(StringIO(secret))
        except paramiko.SSHException:
            continue
    raise ValueError("不支持或无效的 SSH 私钥")


def open_client(
    asset: Asset, credential: Credential, secret: str,
    timeout: float = 10,
    *, host_key: paramiko.PKey | None = None,
) -> paramiko.SSHClient:
    client = paramiko.SSHClient()
    if host_key is None:
        # Read-only collection and interactive access keep their existing trust policy.
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
