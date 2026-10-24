"""Small hardened HTTP helpers for outbound model-provider requests."""
from __future__ import annotations

import http.client
import ipaddress
import socket
import ssl
import urllib.request
from threading import BoundedSemaphore


# One process-wide cap shared by chat and coding-agent provider requests.
MAX_CONCURRENT_PROVIDER_REQUESTS = 8
PROVIDER_SLOTS = BoundedSemaphore(MAX_CONCURRENT_PROVIDER_REQUESTS)


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Never follow provider redirects to a potentially different destination."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def resolve_public_addresses(hostname: str, port: int):
    """Resolve a provider host once and return only its validated socket targets.

    Every answer must be globally routable. Returning getaddrinfo records keeps
    the address family and sockaddr that the connection must use, avoiding a
    second hostname lookup after validation.
    """
    try:
        records = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ValueError("Could not resolve the custom provider host.") from exc
    if not records:
        raise ValueError("Could not resolve the custom provider host.")

    addresses = []
    seen = set()
    for family, socktype, proto, _canonname, sockaddr in records:
        try:
            address_text = sockaddr[0]
            address = ipaddress.ip_address(address_text)
        except (IndexError, TypeError, ValueError) as exc:
            raise ValueError("Could not validate the custom provider host address.") from exc
        if not address.is_global:
            raise ValueError("Custom provider URL cannot resolve to a private or local network address.")
        key = (family, socktype, proto, sockaddr)
        if key not in seen:
            seen.add(key)
            addresses.append((family, socktype, proto, sockaddr))
    return tuple(addresses)


def _connect_to_pinned_address(connection, addresses):
    """Connect directly to one of the validated numeric socket addresses."""
    if connection._tunnel_host:
        raise OSError("Proxy tunnels are not supported for pinned provider requests.")

    errors = []
    for family, socktype, proto, sockaddr in addresses:
        sock = None
        try:
            sock = socket.socket(family, socktype, proto)
            if connection.timeout is not socket._GLOBAL_DEFAULT_TIMEOUT:
                sock.settimeout(connection.timeout)
            if connection.source_address:
                sock.bind(connection.source_address)
            sock.connect(sockaddr)
            connection.sock = sock
            return sock
        except OSError as exc:
            errors.append(exc)
            if sock is not None:
                sock.close()

    if errors:
        raise errors[-1]
    raise OSError("No validated provider addresses are available.")


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS connection that dials pinned IPs but verifies TLS for the URL host."""

    def __init__(self, host, *, addresses, **kwargs):
        super().__init__(host, **kwargs)
        self._validated_addresses = addresses

    def connect(self):
        sock = _connect_to_pinned_address(self, self._validated_addresses)
        # `self.host` is still the original URL hostname, so the default SSL
        # context performs certificate hostname validation and sends correct SNI.
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


class _PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self, addresses):
        # Make the certificate-verifying context explicit across Python versions.
        super().__init__(context=ssl.create_default_context())
        self._validated_addresses = addresses

    def https_open(self, request):
        return self.do_open(
            lambda host, **kwargs: _PinnedHTTPSConnection(
                host, addresses=self._validated_addresses, **kwargs
            ),
            request,
            context=self._context,
            check_hostname=self._check_hostname,
        )


def open_provider_request(request, timeout: int, pinned_addresses=None):
    """Open a provider request without redirects, optionally pinning its host.

    Pinned requests bypass environment proxies so the actual socket connects to
    the validated address. The URL remains unchanged for Host, SNI, and TLS
    certificate verification.
    """
    if pinned_addresses is None:
        opener = urllib.request.build_opener(NoRedirectHandler())
    else:
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            NoRedirectHandler(),
            _PinnedHTTPSHandler(tuple(pinned_addresses)),
        )
    return opener.open(request, timeout=timeout)
