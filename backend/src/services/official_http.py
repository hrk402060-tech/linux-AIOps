import json
from email.message import Message
from typing import BinaryIO
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from src.services.knowledge import KnowledgeImportError

OFFICIAL_PATHS = {
    "ubuntu.com": ("/security/",),
    "usn.ubuntu.com": ("/",),
    "security-tracker.debian.org": ("/tracker/",),
    "salsa.debian.org": ("/security-tracker-team/security-tracker/",),
    "access.redhat.com": ("/hydra/rest/securitydata/",),
    "security.access.redhat.com": ("/data/",),
    "anas.openanolis.cn": ("/",),
    "errata.openanolis.cn": ("/",),
    "httpd.apache.org": ("/security/",),
    "nginx.org": ("/en/security_advisories.html", "/xml/en/security_advisories.xml"),
    "raw.githubusercontent.com": (
        "/CVEProject/cvelistV5/main/cves/",
        "/nginx/nginx.org/master/xml/en/security_advisories.xml",
    ),
}


def validate_url(url: str) -> None:
    parsed = urlsplit(url)
    prefixes = OFFICIAL_PATHS.get(parsed.hostname or "", ())
    if (
        parsed.scheme != "https" or parsed.username or parsed.password
        or parsed.port not in (None, 443)
        or not any(parsed.path.startswith(prefix) for prefix in prefixes)
    ):
        raise KnowledgeImportError("漏洞情报地址不在官方 HTTPS 来源列表中")


class OfficialRedirects(HTTPRedirectHandler):
    def redirect_request(
        self, req: Request, fp: BinaryIO, code: int, msg: str,
        headers: Message, newurl: str,
    ) -> Request | None:
        validate_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def read_bytes(url: str, max_bytes: int = 32 * 1024 * 1024, timeout: int = 30) -> bytes:
    validate_url(url)
    request = Request(url, headers={
        "User-Agent": "Linux-AIOps/1.0 vulnerability-intelligence",
        "Accept": "application/json, application/xml, text/plain, */*",
    })
    try:
        with build_opener(OfficialRedirects()).open(request, timeout=timeout) as response:
            validate_url(response.geturl())
            content = response.read(max_bytes + 1)
    except (HTTPError, URLError, OSError, TimeoutError) as exc:
        raise KnowledgeImportError(f"官方漏洞源暂时不可达：{urlsplit(url).hostname}") from exc
    if len(content) > max_bytes:
        raise KnowledgeImportError("官方漏洞源响应超过大小限制")
    return content


def read_json(
    url: str, max_bytes: int = 32 * 1024 * 1024, timeout: int = 30,
) -> dict[str, object] | list[object]:
    try:
        value = json.loads(read_bytes(url, max_bytes=max_bytes, timeout=timeout))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise KnowledgeImportError("官方漏洞源返回无效 JSON") from exc
    if not isinstance(value, (dict, list)):
        raise KnowledgeImportError("官方漏洞源返回结构无效")
    return value
