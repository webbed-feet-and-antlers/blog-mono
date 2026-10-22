"""Outbound HTTP guard for all pipeline fetches.

Rules: http/https only, target host must resolve to public addresses
(no loopback, private, link-local, reserved, multicast, or unspecified
ranges), and redirects are blocked so a validated host cannot pivot to an
internal one mid-request.
"""
from __future__ import annotations

import ipaddress
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import Union


class UnsafeUrlError(ValueError):
    pass


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, "redirect blocked by kg_pipeline guard", headers, fp)


def assert_safe_url(url: str) -> None:
    parts = urllib.parse.urlsplit(str(url))
    if parts.scheme not in ("http", "https"):
        raise UnsafeUrlError(f"only http/https allowed, got scheme {parts.scheme!r}")
    host = parts.hostname
    if not host:
        raise UnsafeUrlError(f"URL has no host: {url!r}")
    if host.lower() in ("localhost",) or host.endswith(".localhost"):
        raise UnsafeUrlError("localhost targets are not allowed")

    # IP-literal hosts are checked directly; names are resolved and every
    # address must be public.
    try:
        ip = ipaddress.ip_address(host)
        candidate_ips = [ip]
    except ValueError:
        port = parts.port or (443 if parts.scheme == "https" else 80)
        try:
            infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
        except socket.gaierror as exc:
            raise UnsafeUrlError(f"cannot resolve host {host!r}: {exc}") from exc
        candidate_ips = [ipaddress.ip_address(info[4][0]) for info in infos]

    for ip in candidate_ips:
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise UnsafeUrlError(f"host {host!r} resolves to non-public address {ip}")

    if parts.port is not None and not (0 < parts.port < 65536):
        raise UnsafeUrlError(f"invalid port {parts.port}")


_OPENER = urllib.request.build_opener(_NoRedirectHandler)


def guarded_urlopen(req: Union[str, urllib.request.Request], timeout: float = 30):
    """urlopen that refuses non-public targets and follows no redirects."""
    url = req.full_url if isinstance(req, urllib.request.Request) else req
    assert_safe_url(url)
    return _OPENER.open(req, timeout=timeout)
