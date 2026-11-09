from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request

import pytest

from src.services.ai_http import open_model_response


def test_redirect_never_forwards_api_key_or_evidence() -> None:
    received: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            received.append(self.path)
            self.send_response(307)
            self.send_header('Location', '/unexpected-target')
            self.end_headers()

        def do_GET(self) -> None:
            received.append(self.path)
            self.send_response(200)
            self.end_headers()

        def log_message(self, format: str, *args: object) -> None:
            return None

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    worker = Thread(target=server.serve_forever)
    worker.start()
    try:
        request = Request(f'http://127.0.0.1:{server.server_port}/model', data=b'redacted-evidence',
                          headers={'Authorization': 'Bearer test-model-key'})
        with pytest.raises(HTTPError) as caught:
            open_model_response(request, 5)
        assert caught.value.code == 307
        assert received == ['/model']
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
