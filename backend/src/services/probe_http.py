import http.client
import ipaddress
import socket
import ssl
import time
from threading import BoundedSemaphore, Timer
from urllib.parse import urlsplit

_connections = BoundedSemaphore(4)


class BudgetHTTPSConnection(http.client.HTTPSConnection):
    def connect(self) -> None:
        started = time.monotonic()
        http.client.HTTPConnection.connect(self)
        remaining = float(self.timeout) - (time.monotonic() - started)
        if remaining <= 0:
            raise TimeoutError
        self.sock.settimeout(remaining)
        self.sock = self._context.wrap_socket(self.sock, server_hostname=self.host)


def approved_networks(cidrs: str) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    try:
        return [ipaddress.ip_network(value.strip()) for value in cidrs.split(",") if value.strip()]
    except ValueError as exc:
        raise ValueError("Invalid server probe allowlist") from exc


def _platform_addresses() -> set[str]:
    addresses: set[str] = set()
    for host in (socket.gethostname(), "backend", "frontend", "postgres"):
        try:
            addresses.update(row[4][0] for row in socket.getaddrinfo(host, None))
        except socket.gaierror:
            continue  # Compose aliases are absent outside the deployment network.
    return addresses


def validate_url(url: str, asset_host: str, allowed_cidrs: str = "") -> str:
    try:
        parsed = urlsplit(url)
        address = ipaddress.ip_address(parsed.hostname or "")
        approved = ipaddress.ip_address(asset_host)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Probe destination must be the selected asset's literal IP address") from exc
    if parsed.scheme not in {"http", "https"} or parsed.username is not None or parsed.password is not None:
        raise ValueError("Only HTTP(S) URLs without credentials are allowed")
    if address != approved:
        raise ValueError("Destination must match the selected linked asset IP")
    if not any(address in network for network in approved_networks(allowed_cidrs)):
        raise ValueError("Destination is not in the server-approved probe allowlist")
    if str(address) in _platform_addresses():
        raise ValueError("Platform management destinations are forbidden")
    if address.is_loopback or address.is_link_local or address.is_multicast or address.is_unspecified:
        raise ValueError("Loopback, metadata, link-local and multicast destinations are forbidden")
    if address.version == 6 and address.ipv4_mapped is not None:
        raise ValueError("IPv4-mapped IPv6 destinations are forbidden")
    if parsed.query or parsed.fragment or "\\" in url or any(ord(char) < 33 for char in url):
        raise ValueError("Query strings, fragments, whitespace and backslashes are forbidden")
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("Invalid destination port")
    return url


def check_http(url: str, method: str, expected_status: int, timeout: int) -> dict[str, object]:
    if not _connections.acquire(blocking=False):
        raise ValueError("Probe concurrency limit reached; retry later")
    try:
        return _check_http(url, method, expected_status, timeout)
    finally:
        _connections.release()


def _expire_connection(connection: http.client.HTTPConnection) -> None:
    sock = connection.sock
    if sock is not None:
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            return  # A concurrent normal close already ended the connection.


def _check_http(url: str, method: str, expected_status: int, timeout: int) -> dict[str, object]:
    parsed = urlsplit(url)
    started = time.monotonic()
    connection: http.client.HTTPConnection | None = None
    deadline: Timer | None = None
    try:
        host = parsed.hostname or ""
        if parsed.scheme == "https":
            connection = BudgetHTTPSConnection(host, port=parsed.port, timeout=timeout, context=ssl.create_default_context())
        else:
            connection = http.client.HTTPConnection(host, port=parsed.port, timeout=timeout)
        connection.connect()
        remaining = timeout - (time.monotonic() - started)
        if remaining <= 0:
            raise TimeoutError
        deadline = Timer(remaining, _expire_connection, args=(connection,))
        deadline.daemon = True
        deadline.start()
        connection.request(method, parsed.path or "/", headers={"User-Agent": "Sentinel-ReadOnly-Probe/1.0", "Connection": "close"})
        response = connection.getresponse()
        matches = response.status == expected_status
        return {"status": "healthy" if matches else "unhealthy", "http_status": response.status, "latency_ms": int((time.monotonic() - started) * 1000), "error_code": None if matches else "http_status_mismatch"}
    except (socket.timeout, TimeoutError):
        code = "timeout"
    except ssl.SSLError:
        code = "tls_error"
    except (OSError, http.client.HTTPException):
        code = "timeout" if time.monotonic() - started >= timeout else "connection_error"
    finally:
        if deadline is not None:
            deadline.cancel()
        if connection is not None:
            connection.close()
    return {"status": "error", "http_status": None, "latency_ms": int((time.monotonic() - started) * 1000), "error_code": code}
