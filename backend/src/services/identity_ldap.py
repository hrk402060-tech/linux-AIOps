from __future__ import annotations

import ssl
from typing import Any
from urllib.parse import urlsplit

from src.services.identity_store import IdentityAuthenticationError, IdentitySourceError


def _connect(configuration: dict[str, Any], user: str, password: str) -> Any:
    from ldap3 import AUTO_BIND_NO_TLS, AUTO_BIND_TLS_BEFORE_BIND, Connection, Server, Tls
    from ldap3.core.exceptions import LDAPException

    address = urlsplit(configuration["server_url"])
    use_ssl = address.scheme == "ldaps"
    tls = Tls(validate=ssl.CERT_REQUIRED, version=ssl.PROTOCOL_TLS_CLIENT)
    server = Server(
        address.hostname, port=address.port or (636 if use_ssl else 389),
        use_ssl=use_ssl, tls=tls,
        connect_timeout=configuration["timeout_seconds"],
    )
    try:
        return Connection(
            server, user=user, password=password,
            auto_bind=AUTO_BIND_NO_TLS if use_ssl else AUTO_BIND_TLS_BEFORE_BIND,
            receive_timeout=configuration["timeout_seconds"], raise_exceptions=True,
            read_only=True, auto_referrals=False,
        )
    except LDAPException as error:
        raise IdentityAuthenticationError("目录认证失败或 TLS 连接不可用") from error


def _reader(configuration: dict[str, Any]) -> Any:
    try:
        return _connect(
            configuration, configuration["bind_dn"], configuration["bind_password"],
        )
    except IdentityAuthenticationError as error:
        raise IdentitySourceError("目录服务器连接失败、绑定账号无效或 TLS 校验未通过") from error


def test_connection(configuration: dict[str, Any]) -> str:
    from ldap3 import BASE
    from ldap3.core.exceptions import LDAPException

    connection = _reader(configuration)
    try:
        connection.search(
            configuration["base_dn"], "(objectClass=*)", search_scope=BASE,
            attributes=["objectClass"], size_limit=1,
            time_limit=configuration["timeout_seconds"],
        )
        if not connection.entries:
            raise IdentitySourceError("已连接目录，但搜索根不存在或无读取权限")
        return "TLS、目录绑定和搜索根读取成功"
    except LDAPException as error:
        raise IdentitySourceError("已连接目录，但搜索根读取失败") from error
    finally:
        connection.unbind()


def authenticate(
    configuration: dict[str, Any], username: str, password: str,
) -> dict[str, str]:
    from ldap3 import SUBTREE
    from ldap3.core.exceptions import LDAPException
    from ldap3.utils.conv import escape_filter_chars

    if not password:
        raise IdentityAuthenticationError("用户名或密码错误")
    connection = _reader(configuration)
    try:
        connection.search(
            configuration["base_dn"],
            configuration["user_filter"].replace("{username}", escape_filter_chars(username)),
            search_scope=SUBTREE,
            attributes=list(dict.fromkeys([
                configuration["subject_attribute"], configuration["username_attribute"],
                configuration["display_name_attribute"],
            ])),
            size_limit=2, time_limit=configuration["timeout_seconds"],
        )
        if len(connection.entries) != 1:
            raise IdentityAuthenticationError("用户名或密码错误")
        entry = connection.entries[0]
        subject = _single_attribute(entry, configuration["subject_attribute"])
        if not subject or len(subject) > 255:
            raise IdentitySourceError("目录未提供有效的不可变用户标识")
        user_connection = _connect(configuration, entry.entry_dn, password)
        user_connection.unbind()
        return {
            "subject": subject,
            "display_name": _single_attribute(entry, configuration["display_name_attribute"]),
        }
    except LDAPException as error:
        raise IdentitySourceError("目录查询失败，请检查筛选器和读取权限") from error
    finally:
        connection.unbind()


def _single_attribute(entry: Any, name: str) -> str:
    values = entry.entry_attributes_as_dict.get(name, [])
    if len(values) != 1:
        return ""
    value = values[0]
    return value.hex() if isinstance(value, bytes) else str(value)
