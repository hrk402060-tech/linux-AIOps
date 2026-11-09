from unittest.mock import Mock

import paramiko
import pytest

from src.models.asset import Asset, AssetEnvironment, OsFamily
from src.models.ssh_host_key import SshHostKey
from src.services import ssh_host_key_registry
from src.services.ssh_host_keys import HostKeyCandidate, _fingerprint


def _asset() -> Asset:
    return Asset(
        id="asset-1", name="collector-node", host="10.0.0.50", ssh_port=22,
        os_family=OsFamily.OPENEULER, environment=AssetEnvironment.TEST,
        group_name="test", credential_id=None, labels=[],
    )


def _record(key: paramiko.PKey) -> SshHostKey:
    return SshHostKey(
        id="host-key-1", asset_id="asset-1", host="10.0.0.50", port=22,
        key_type=key.get_name(), fingerprint=_fingerprint(key.asbytes()),
        public_key=f"{key.get_name()} {key.get_base64()}", confirmed_by="admin",
    )


def test_registered_host_key_is_required_and_parsed() -> None:
    expected = paramiko.RSAKey.generate(1024)
    session = Mock()
    session.scalar.return_value = _record(expected)

    actual = ssh_host_key_registry.require_registered(session, _asset())

    assert actual.get_name() == expected.get_name()
    assert actual.asbytes() == expected.asbytes()


def test_missing_or_stale_registration_is_rejected() -> None:
    session = Mock()
    session.scalar.return_value = None
    with pytest.raises(
        ssh_host_key_registry.HostKeyRegistrationError, match="未登记 SSH 主机指纹"
    ):
        ssh_host_key_registry.require_registered(session, _asset())

    record = _record(paramiko.RSAKey.generate(1024))
    record.host = "10.0.0.51"
    session.scalar.return_value = record
    with pytest.raises(
        ssh_host_key_registry.HostKeyRegistrationError, match="SSH 地址已变化"
    ):
        ssh_host_key_registry.require_registered(session, _asset())


def test_malformed_registration_is_rejected_without_key_fallback() -> None:
    record = _record(paramiko.RSAKey.generate(1024))
    record.public_key = f"{record.key_type} not-base64"
    session = Mock()
    session.scalar.return_value = record

    with pytest.raises(
        ssh_host_key_registry.HostKeyRegistrationError, match="格式无效"
    ):
        ssh_host_key_registry.require_registered(session, _asset())


def test_missing_registration_is_auto_pinned_for_read_only_collection(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = paramiko.RSAKey.generate(1024)
    candidate = HostKeyCandidate(
        expected.get_name(), _fingerprint(expected.asbytes()),
        f"{expected.get_name()} {expected.get_base64()}",
    )
    session = Mock()
    session.scalar.return_value = None
    monkeypatch.setattr(
        "src.services.ssh_host_key_registry.ssh_host_keys.probe_host_key",
        Mock(return_value=candidate),
    )

    actual = ssh_host_key_registry.ensure_registered(session, _asset())

    assert actual.asbytes() == expected.asbytes()
    registered = session.add.call_args_list[0].args[0]
    assert registered.asset_id == "asset-1"
    assert registered.fingerprint == candidate.fingerprint
    assert registered.confirmed_by == "system:auto"
    audit = session.add.call_args_list[1].args[0]
    assert audit.action == "auto_registered"


def test_existing_registration_is_not_silently_rotated(monkeypatch: pytest.MonkeyPatch) -> None:
    old = paramiko.RSAKey.generate(1024)
    new = paramiko.RSAKey.generate(1024)
    session = Mock()
    session.scalar.return_value = _record(old)
    monkeypatch.setattr(
        "src.services.ssh_host_key_registry.ssh_host_keys.probe_host_key",
        Mock(return_value=HostKeyCandidate(
            new.get_name(), _fingerprint(new.asbytes()),
            f"{new.get_name()} {new.get_base64()}",
        )),
    )

    actual = ssh_host_key_registry.ensure_registered(session, _asset())

    assert actual.asbytes() == old.asbytes()
    session.add.assert_not_called()
