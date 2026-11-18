from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlsplit

from src.services.identity_store import IdentityAuthenticationError, IdentitySourceError

MAX_RESPONSE_BYTES = 1024 * 1024
ALGORITHMS = ("RS256", "RS384", "RS512", "ES256", "ES384", "ES512", "EdDSA")


def _bounded_json(response: Any) -> dict[str, Any]:
    raw = bytearray()
    for chunk in response.iter_bytes():
        raw.extend(chunk)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise IdentitySourceError("OIDC 响应超过允许大小")
    result = json.loads(raw)
    if not isinstance(result, dict):
        raise IdentitySourceError("OIDC 响应格式无效")
    return result


def _https_url(value: Any) -> str:
    if not isinstance(value, str):
        raise IdentitySourceError("OIDC 发现文档缺少有效端点")
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https" or not parsed.hostname or parsed.username
        or parsed.password or parsed.fragment
    ):
        raise IdentitySourceError("OIDC 端点必须使用 HTTPS")
    return value


def _read_json(url: str, timeout: int) -> dict[str, Any]:
    import httpx

    try:
        with httpx.Client(
            timeout=timeout, follow_redirects=False, trust_env=False,
        ) as client:
            with client.stream("GET", _https_url(url)) as response:
                response.raise_for_status()
                return _bounded_json(response)
    except (httpx.HTTPError, ValueError, TypeError) as error:
        if isinstance(error, IdentitySourceError):
            raise
        raise IdentitySourceError("OIDC HTTPS 请求失败，请检查地址、证书和网络") from error


def discover(configuration: dict[str, Any]) -> dict[str, Any]:
    issuer = configuration["issuer_url"]
    metadata = _read_json(
        f"{issuer.rstrip('/')}/.well-known/openid-configuration", configuration["timeout_seconds"],
    )
    if metadata.get("issuer") != issuer:
        raise IdentitySourceError("OIDC issuer 与发现文档不匹配")
    for name in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
        _https_url(metadata.get(name))
    if "code" not in metadata.get("response_types_supported", ["code"]):
        raise IdentitySourceError("OIDC 身份源不支持授权码登录")
    return metadata


def test_connection(configuration: dict[str, Any]) -> str:
    from authlib.common.errors import AuthlibBaseError
    from authlib.integrations.httpx_client import OAuth2Client
    from joserfc.errors import JoseError
    from joserfc.jwk import KeySet
    import httpx

    metadata = discover(configuration)
    try:
        KeySet.import_key_set(_read_json(metadata["jwks_uri"], configuration["timeout_seconds"]))
        with OAuth2Client(
            configuration["client_id"], configuration["client_secret"],
            timeout=configuration["timeout_seconds"], follow_redirects=False, trust_env=False,
        ) as client:
            with client.stream("POST", metadata["token_endpoint"], data={
                    "grant_type": "authorization_code",
                    "code": "connection-test-invalid-authorization-code",
                    "redirect_uri": configuration["redirect_uri"],
                    "code_verifier": "connection-test-verifier-invalid-code",
            }, auth=client.client_auth("client_secret_basic")) as response:
                status, result = response.status_code, _bounded_json(response)
        if status != 400 or result.get("error") != "invalid_grant":
            raise IdentitySourceError("OIDC 客户端验证失败，请检查 client ID、密钥和回调地址")
        return "发现文档、签名公钥与客户端认证检查成功；请完成一次管理员外部登录"
    except (httpx.HTTPError, AuthlibBaseError, JoseError, ValueError, TypeError) as error:
        if isinstance(error, IdentitySourceError):
            raise
        raise IdentitySourceError("OIDC 客户端或签名公钥验证失败") from error


def authorization_url(
    configuration: dict[str, Any], metadata: dict[str, Any],
    state: str, nonce: str, verifier: str,
) -> str:
    from authlib.integrations.httpx_client import OAuth2Client

    with OAuth2Client(
        configuration["client_id"], redirect_uri=configuration["redirect_uri"],
        scope=" ".join(configuration["scopes"]), code_challenge_method="S256",
        timeout=configuration["timeout_seconds"], trust_env=False,
    ) as client:
        url, _ = client.create_authorization_url(
            metadata["authorization_endpoint"], state=state, nonce=nonce,
            code_verifier=verifier,
        )
        return str(url)


def exchange(
    configuration: dict[str, Any], metadata: dict[str, Any],
    code: str, verifier: str, nonce: str,
) -> dict[str, str]:
    from authlib.common.errors import AuthlibBaseError
    from authlib.integrations.httpx_client import OAuth2Client
    import httpx

    try:
        with OAuth2Client(
            configuration["client_id"], configuration["client_secret"],
            redirect_uri=configuration["redirect_uri"],
            token_endpoint_auth_method="client_secret_basic",
            timeout=configuration["timeout_seconds"], follow_redirects=False, trust_env=False,
        ) as client:
            token = client.fetch_token(
                metadata["token_endpoint"], code=code, code_verifier=verifier,
                grant_type="authorization_code",
            )
        return validate_token(configuration, metadata, token, nonce)
    except (httpx.HTTPError, AuthlibBaseError, ValueError, TypeError, KeyError) as error:
        if isinstance(error, IdentityAuthenticationError):
            raise
        raise IdentityAuthenticationError("OIDC 授权码验证失败，请重新登录") from error


def validate_token(
    configuration: dict[str, Any], metadata: dict[str, Any],
    token: dict[str, Any], nonce: str,
) -> dict[str, str]:
    from authlib.oidc.core import CodeIDToken
    from joserfc import jwt
    from joserfc.errors import JoseError
    from joserfc.jwk import KeySet

    raw = token.get("id_token")
    if not isinstance(raw, str) or len(raw) > 65536:
        raise IdentityAuthenticationError("OIDC 未返回有效身份令牌")
    try:
        keys = KeySet.import_key_set(_read_json(
            metadata["jwks_uri"], configuration["timeout_seconds"],
        ))
        decoded = jwt.decode(raw, keys, algorithms=list(ALGORITHMS))
        claims = CodeIDToken(
            decoded.claims, decoded.header,
            options={
                "iss": {"essential": True, "value": configuration["issuer_url"]},
                "aud": {"essential": True, "value": configuration["client_id"]},
                "exp": {"essential": True}, "iat": {"essential": True},
                "sub": {"essential": True}, "nonce": {"essential": True, "value": nonce},
            },
            params={
                "client_id": configuration["client_id"], "nonce": nonce,
                "access_token": token.get("access_token"),
            },
        )
        claims.validate(leeway=30)
        subject = claims["sub"]
        if not isinstance(subject, str) or not subject or len(subject) > 255:
            raise IdentityAuthenticationError("OIDC 未提供有效用户标识")
        return {"subject": subject}
    except (JoseError, ValueError, TypeError, KeyError) as error:
        raise IdentityAuthenticationError("OIDC 令牌签名或身份声明验证失败") from error
