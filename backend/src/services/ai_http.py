from http.client import HTTPResponse
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


class _RejectRedirect(HTTPRedirectHandler):
    def redirect_request(
        self, req: Request, fp: object, code: int, msg: str,
        headers: object, newurl: str,
    ) -> None:
        return None


def open_model_response(request: Request, timeout: int) -> HTTPResponse:
    # A configured model must never forward a key or evidence to a redirect or proxy.
    return build_opener(ProxyHandler({}), _RejectRedirect()).open(request, timeout=timeout)
