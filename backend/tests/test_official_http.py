from unittest.mock import Mock, patch

import pytest

from src.services import official_http
from src.services.knowledge import KnowledgeImportError


@pytest.mark.parametrize("url", [
    "http://ubuntu.com/security/notices.json",
    "https://ubuntu.com.evil.test/security/notices.json",
    "https://ubuntu.com:9443/security/notices.json",
    "https://root:secret@ubuntu.com/security/notices.json",
    "https://raw.githubusercontent.com/untrusted/feed/main/data.json",
])
def test_official_download_rejects_untrusted_url_before_network(url: str) -> None:
    with patch.object(official_http, "build_opener") as opener:
        with pytest.raises(KnowledgeImportError):
            official_http.read_bytes(url)
    opener.assert_not_called()


def test_official_download_bounds_size_and_redirects() -> None:
    response = Mock()
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=None)
    response.geturl.return_value = "https://httpd.apache.org/security/json/"
    response.read.return_value = b"12345"
    with patch.object(official_http, "build_opener") as opener:
        opener.return_value.open.return_value = response
        with pytest.raises(KnowledgeImportError, match="大小"):
            official_http.read_bytes(response.geturl(), max_bytes=4)
    with pytest.raises(KnowledgeImportError):
        official_http.OfficialRedirects().redirect_request(
            Mock(), Mock(), 302, "", {}, "https://127.0.0.1/private",
        )
