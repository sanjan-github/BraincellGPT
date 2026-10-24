"""Small hardened HTTP helpers for outbound model-provider requests."""
from __future__ import annotations

import urllib.request


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Never follow provider redirects to a potentially different destination."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def open_provider_request(request, timeout: int):
    """Open a provider request without following redirects."""
    opener = urllib.request.build_opener(NoRedirectHandler())
    return opener.open(request, timeout=timeout)
