import json
import socket
import ssl
from pathlib import Path
import unittest
import urllib.request
from unittest.mock import MagicMock, patch

from backend import provider_http
from backend import agent


def addrinfo(address, family):
    if family == socket.AF_INET6:
        sockaddr = (address, 443, 0, 0)
    else:
        sockaddr = (address, 443)
    return (family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", sockaddr)


class PublicAddressResolutionTests(unittest.TestCase):
    def test_resolves_and_pins_public_ipv4(self):
        record = addrinfo("93.184.216.34", socket.AF_INET)
        with patch("backend.provider_http.socket.getaddrinfo", return_value=[record]) as lookup:
            addresses = provider_http.resolve_public_addresses("provider.example", 8443)

        self.assertEqual(addresses, ((socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, record[4]),))
        lookup.assert_called_once_with("provider.example", 8443, type=socket.SOCK_STREAM)

    def test_resolves_and_pins_public_ipv6(self):
        record = addrinfo("2606:4700:4700::1111", socket.AF_INET6)
        with patch("backend.provider_http.socket.getaddrinfo", return_value=[record]):
            addresses = provider_http.resolve_public_addresses("provider.example", 443)

        self.assertEqual(addresses[0][0], socket.AF_INET6)
        self.assertEqual(addresses[0][3], record[4])

    def test_rejects_private_loopback_and_link_local_answers(self):
        examples = (
            ("127.0.0.1", socket.AF_INET),
            ("10.0.0.4", socket.AF_INET),
            ("169.254.10.20", socket.AF_INET),
            ("::1", socket.AF_INET6),
            ("fe80::1", socket.AF_INET6),
        )
        for address, family in examples:
            with self.subTest(address=address), patch(
                "backend.provider_http.socket.getaddrinfo", return_value=[addrinfo(address, family)]
            ):
                with self.assertRaisesRegex(ValueError, "private or local"):
                    provider_http.resolve_public_addresses("provider.example", 443)

    def test_rejects_mixed_public_and_private_answers(self):
        records = [
            addrinfo("93.184.216.34", socket.AF_INET),
            addrinfo("10.0.0.4", socket.AF_INET),
        ]
        with patch("backend.provider_http.socket.getaddrinfo", return_value=records):
            with self.assertRaisesRegex(ValueError, "private or local"):
                provider_http.resolve_public_addresses("provider.example", 443)

    def test_dns_failure_is_a_controlled_validation_error(self):
        with patch("backend.provider_http.socket.getaddrinfo", side_effect=socket.gaierror("no such host")):
            with self.assertRaisesRegex(ValueError, "Could not resolve"):
                provider_http.resolve_public_addresses("provider.example", 443)


class PinnedHttpsConnectionTests(unittest.TestCase):
    def test_provider_requests_identify_braincellgpt_without_overriding_explicit_agent(self):
        opener = MagicMock()
        with patch("backend.provider_http.urllib.request.build_opener", return_value=opener):
            default_request = urllib.request.Request("https://provider.example/v1")
            provider_http.open_provider_request(default_request, timeout=4)
            explicit_request = urllib.request.Request(
                "https://provider.example/v1", headers={"User-Agent": "Provider SDK/1.0"}
            )
            provider_http.open_provider_request(explicit_request, timeout=4)

        self.assertEqual(default_request.get_header("User-agent"), "BrainCellGPT/0.1")
        self.assertEqual(explicit_request.get_header("User-agent"), "Provider SDK/1.0")

    def test_connects_to_pinned_ip_without_dns_and_keeps_hostname_for_tls(self):
        target = (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, ("93.184.216.34", 443))
        raw_socket = MagicMock()
        context = MagicMock()
        context.wrap_socket.side_effect = lambda sock, server_hostname: sock
        connection = provider_http._PinnedHTTPSConnection(
            "provider.example:443", addresses=(target,), timeout=5, context=context
        )

        with patch("backend.provider_http.socket.socket", return_value=raw_socket) as socket_factory, patch(
            "backend.provider_http.socket.getaddrinfo", side_effect=AssertionError("unexpected DNS lookup")
        ):
            connection.connect()

        socket_factory.assert_called_once_with(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP)
        raw_socket.connect.assert_called_once_with(("93.184.216.34", 443))
        context.wrap_socket.assert_called_once_with(raw_socket, server_hostname="provider.example")
        self.assertEqual(connection.host, "provider.example")

    def test_tries_other_validated_addresses_if_a_socket_family_is_unavailable(self):
        ipv6 = (socket.AF_INET6, socket.SOCK_STREAM, socket.IPPROTO_TCP, ("2606:4700:4700::1111", 443, 0, 0))
        ipv4 = (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, ("93.184.216.34", 443))
        raw_socket = MagicMock()
        context = MagicMock()
        context.wrap_socket.side_effect = lambda sock, server_hostname: sock
        connection = provider_http._PinnedHTTPSConnection(
            "provider.example:443", addresses=(ipv6, ipv4), timeout=5, context=context
        )

        with patch(
            "backend.provider_http.socket.socket", side_effect=[OSError("IPv6 unavailable"), raw_socket]
        ) as socket_factory:
            connection.connect()

        self.assertEqual(socket_factory.call_count, 2)
        raw_socket.connect.assert_called_once_with(ipv4[3])
        context.wrap_socket.assert_called_once_with(raw_socket, server_hostname="provider.example")

    def test_default_pinned_handler_keeps_certificate_validation_enabled(self):
        handler = provider_http._PinnedHTTPSHandler(
            ((socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, ("93.184.216.34", 443)),)
        )

        self.assertEqual(handler._context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(handler._context.check_hostname)

    def test_pinned_opener_disables_proxies_and_redirects(self):
        request = urllib.request.Request("https://provider.example/v1")
        pinned = ((socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, ("93.184.216.34", 443)),)
        opener = MagicMock()
        opener.open.return_value = "response"
        with patch("backend.provider_http.urllib.request.build_opener", return_value=opener) as build_opener:
            self.assertEqual(provider_http.open_provider_request(request, timeout=4, pinned_addresses=pinned), "response")

        handlers = build_opener.call_args.args
        self.assertIsInstance(handlers[0], urllib.request.ProxyHandler)
        self.assertEqual(handlers[0].proxies, {})
        self.assertIsInstance(handlers[1], provider_http.NoRedirectHandler)
        self.assertIsInstance(handlers[2], provider_http._PinnedHTTPSHandler)
        opener.open.assert_called_once_with(request, timeout=4)


class AgentCustomProviderPinningTests(unittest.TestCase):
    def test_agent_uses_the_custom_provider_addresses_it_validated(self):
        response = MagicMock()
        response.read.return_value = json.dumps({
            "choices": [{"message": {"content": "agent reply", "tool_calls": []}}]
        }).encode()
        response.__enter__.return_value = response
        pinned = ((socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, ("93.184.216.34", 443)),)

        with patch("backend.agent.resolve_public_addresses", return_value=pinned), patch(
            "backend.agent.open_provider_request", return_value=response
        ) as opener:
            result = agent.run_agent(
                "hello", [], Path.cwd(), "custom", "custom-key", "https://provider.example/v1",
                "custom-model", {}, allow_edits=False,
            )

        self.assertEqual(result["reply"], "agent reply")
        self.assertEqual(opener.call_args.kwargs["pinned_addresses"], pinned)


class SharedProviderJsonReaderTests(unittest.TestCase):
    def test_reads_valid_json_with_bounded_reader(self):
        response = MagicMock()
        response.read.return_value = b'{"ok": true}'
        self.assertEqual(provider_http.read_provider_json(response, 32), {"ok": True})
        response.read.assert_called_once_with(33)

    def test_rejects_oversized_provider_response(self):
        response = MagicMock()
        response.read.return_value = b"x" * 9
        with self.assertRaises(provider_http.ProviderResponseTooLarge):
            provider_http.read_provider_json(response, 8)
        response.read.assert_called_once_with(9)

    def test_rejects_invalid_provider_json(self):
        response = MagicMock()
        response.read.return_value = b"{bad json"
        with self.assertRaises(provider_http.ProviderInvalidJSON):
            provider_http.read_provider_json(response, 32)


if __name__ == "__main__":
    unittest.main()
