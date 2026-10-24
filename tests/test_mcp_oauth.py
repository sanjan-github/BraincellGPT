import email.message
import io
import json
import time
import unittest
import urllib.error
from urllib.parse import parse_qs, urlsplit
from unittest.mock import MagicMock, patch

from backend import mcp_oauth
from backend.mcp_stdio import MCPServerSpec, _StreamableHTTPClient


class MCPOAuthFlowTests(unittest.TestCase):
    def setUp(self):
        mcp_oauth.clear_tokens()
        self.store_get_patcher = patch("backend.mcp_oauth.get_secret", return_value=None)
        self.store_set_patcher = patch("backend.mcp_oauth.set_secret", return_value=True)
        self.store_get_patcher.start()
        self.store_set_patcher.start()

    def tearDown(self):
        mcp_oauth.clear_tokens()
        self.store_get_patcher.stop()
        self.store_set_patcher.stop()

    def test_authorization_code_flow_uses_state_pkce_and_resource_binding(self):
        resource = {"resource": "https://mcp.example.test/mcp", "authorization_servers": ["https://auth.example.test"], "scopes_supported": ["files:read"]}
        metadata = {
            "issuer": "https://auth.example.test",
            "authorization_endpoint": "https://auth.example.test/authorize",
            "token_endpoint": "https://auth.example.test/token",
            "code_challenge_methods_supported": ["S256"],
        }
        token_payloads = []
        expected_verifier = []

        def exchange(url, body=None, content_type="application/json"):
            token_payloads.append((url, body, content_type))
            return {"access_token": "access-value", "refresh_token": "refresh-value", "expires_in": 600}

        def open_browser(url, new=0):
            params = parse_qs(urlsplit(url).query)
            self.assertEqual(params["resource"], ["https://mcp.example.test/mcp"])
            self.assertEqual(params["code_challenge_method"], ["S256"])
            self.assertEqual(params["scope"], ["files:read"])
            state = params["state"][0]
            message = next(iter(mcp_oauth._PENDING.values()))
            self.assertNotEqual(message.verifier, params["code_challenge"][0])
            expected_verifier.append(message.verifier)
            mcp_oauth.complete_callback({"state": [state], "code": ["authorization-code"]})
            return True

        with patch("backend.mcp_oauth._discover", return_value=(resource, metadata, "https://auth.example.test")), \
             patch("backend.mcp_oauth._register_client", return_value="registered-client"), \
             patch("backend.mcp_oauth._request_json", side_effect=exchange), \
             patch("backend.mcp_oauth.webbrowser.open", side_effect=open_browser):
            token = mcp_oauth.access_token(
                "https://mcp.example.test/mcp", 'Bearer resource_metadata="https://mcp.example.test/.well-known/oauth-protected-resource/mcp", scope="files:read"',
                8765, timeout=1,
            )

        self.assertEqual(token, "access-value")
        self.assertEqual(token_payloads[0][1]["grant_type"], "authorization_code")
        self.assertEqual(token_payloads[0][1]["code_verifier"], expected_verifier[0])
        self.assertEqual(token_payloads[0][1]["resource"], "https://mcp.example.test/mcp")
        self.assertEqual(token_payloads[0][2], "application/x-www-form-urlencoded")

    def test_expired_access_token_refreshes_with_same_resource(self):
        server = "https://mcp.example.test/mcp"
        mcp_oauth._TOKENS[server] = {
            "access_token": "old-access", "refresh_token": "refresh", "expires_at": time.time() - 1,
            "client_id": "client", "token_endpoint": "https://auth.example.test/token", "resource": server,
            "scope": "read",
        }
        with patch("backend.mcp_oauth._request_json", return_value={"access_token": "new-access", "expires_in": 120}) as request:
            self.assertEqual(mcp_oauth.access_token(server, None, 8765), "new-access")
        self.assertEqual(request.call_args.args[1]["grant_type"], "refresh_token")
        self.assertEqual(request.call_args.args[1]["resource"], server)
        self.assertEqual(mcp_oauth._TOKENS[server]["refresh_token"], "refresh")

    def test_unauthorized_but_unexpired_access_token_attempts_refresh(self):
        server = "https://mcp.example.test/mcp"
        mcp_oauth._TOKENS[server] = {
            "access_token": "rejected-access", "refresh_token": "refresh", "expires_at": time.time() + 600,
            "client_id": "client", "token_endpoint": "https://auth.example.test/token", "resource": server,
            "scope": "read",
        }
        with patch("backend.mcp_oauth._request_json", return_value={"access_token": "fresh-access", "expires_in": 120}) as request:
            token = mcp_oauth.access_token(server, None, 8765, force_refresh=True)
        self.assertEqual(token, "fresh-access")
        self.assertEqual(request.call_args.args[1]["grant_type"], "refresh_token")

    def test_refreshed_token_is_persisted_and_reloaded_from_secure_store(self):
        server = "https://mcp.example.test/mcp"
        records = {}
        def read_record(name):
            return records.get(name)
        def write_record(name, value):
            records[name] = value
            return True

        mcp_oauth._TOKENS[server] = {
            "access_token": "old-access", "refresh_token": "old-refresh", "expires_at": time.time() - 1,
            "client_id": "client", "token_endpoint": "https://auth.example.test/token", "resource": server,
            "scope": "read",
        }
        with patch("backend.mcp_oauth.get_secret", side_effect=read_record), \
             patch("backend.mcp_oauth.set_secret", side_effect=write_record), \
             patch("backend.mcp_oauth._request_json", return_value={
                 "access_token": "persisted-access", "refresh_token": "rotated-refresh", "expires_in": 600,
             }):
            self.assertEqual(mcp_oauth.access_token(server, None, 8765), "persisted-access")
            mcp_oauth.clear_tokens()
            self.assertEqual(mcp_oauth.access_token(server, None, 8765), "persisted-access")
        saved = json.loads(records[mcp_oauth._token_store_key(server)])
        self.assertEqual(saved["refresh_token"], "rotated-refresh")

    def test_callback_rejects_unknown_state(self):
        with self.assertRaisesRegex(ValueError, "state is invalid"):
            mcp_oauth.complete_callback({"state": ["unknown"], "code": ["code"]})

    def test_forget_server_clears_memory_and_delegates_secure_store_deletion(self):
        server = "https://mcp.example.test/mcp"
        mcp_oauth._TOKENS[server] = {"access_token": "secret"}
        with patch("backend.mcp_oauth.delete_secret", return_value=True) as delete:
            self.assertTrue(mcp_oauth.forget_server(server))
        self.assertNotIn(server, mcp_oauth._TOKENS)
        delete.assert_called_once_with(mcp_oauth._token_store_key(server))

    def test_oauth_metadata_urls_reject_non_https_and_private_hosts(self):
        with self.assertRaisesRegex(ValueError, "public HTTPS"):
            mcp_oauth._https_endpoint("http://auth.example.test/token")
        with patch("backend.mcp_oauth.resolve_public_addresses", side_effect=ValueError("private")):
            with self.assertRaisesRegex(ValueError, "private"):
                mcp_oauth._https_endpoint("https://auth.example.test/token")

    def test_discovery_fetches_path_specific_resource_and_authorization_metadata(self):
        resource = {"resource": "https://mcp.example.test/mcp", "authorization_servers": ["https://auth.example.test/tenant"]}
        authorization = {
            "issuer": "https://auth.example.test/tenant",
            "authorization_endpoint": "https://auth.example.test/authorize",
            "token_endpoint": "https://auth.example.test/token",
        }
        with patch("backend.mcp_oauth.resolve_public_addresses", return_value=((2, 1, 6, ("93.184.216.34", 443)),)), \
             patch("backend.mcp_oauth._request_json", side_effect=[resource, authorization]) as request:
            found_resource, found_authorization, issuer = mcp_oauth._discover("https://mcp.example.test/mcp", None)
        self.assertEqual(found_resource, resource)
        self.assertEqual(found_authorization, authorization)
        self.assertEqual(issuer, "https://auth.example.test/tenant")
        self.assertEqual(request.call_args_list[0].args[0], "https://mcp.example.test/.well-known/oauth-protected-resource/mcp")
        self.assertEqual(request.call_args_list[1].args[0], "https://auth.example.test/.well-known/oauth-authorization-server/tenant")

    def test_discovery_rejects_mismatched_resource_metadata(self):
        resource = {"resource": "https://other.example.test/mcp", "authorization_servers": ["https://auth.example.test"]}
        with patch("backend.mcp_oauth._request_json", return_value=resource):
            with self.assertRaisesRegex(ValueError, "different protected resource"):
                mcp_oauth._discover("https://mcp.example.test/mcp", None)


class MCPTransportOAuthTests(unittest.TestCase):
    def test_401_starts_oauth_then_retries_with_bearer_and_redacts_token(self):
        spec = MCPServerSpec("remote", None, (), {}, "https://mcp.example.test/mcp", {})
        response = MagicMock()
        response.read.return_value = b'{"jsonrpc":"2.0","id":1,"result":{"content":[]}}'
        response.__enter__.return_value = response
        response.headers = {"Content-Type": "application/json"}
        error_headers = email.message.Message()
        error_headers["WWW-Authenticate"] = 'Bearer realm="mcp", scope="read"'
        unauthorized = urllib.error.HTTPError(
            spec.url, 401, "Unauthorized", error_headers, io.BytesIO(b""),
        )
        with patch("backend.mcp_stdio.resolve_public_addresses", return_value=((2, 1, 6, ("93.184.216.34", 443)),)), \
             patch("backend.mcp_stdio.open_provider_request", side_effect=[unauthorized, response]) as opener, \
             patch("backend.mcp_stdio.oauth_access_token", return_value="oauth-secret") as oauth:
            client = _StreamableHTTPClient(spec, oauth_port=8765)
            client.era = "modern"
            result = client._post({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}, 1)

        self.assertEqual(result["result"]["content"], [])
        self.assertEqual(opener.call_count, 2)
        self.assertEqual(opener.call_args.args[0].get_header("Authorization"), "Bearer oauth-secret")
        self.assertEqual(opener.call_args.kwargs["pinned_addresses"], client.addresses)
        self.assertEqual(oauth.call_args_list[0].args[1], 'Bearer realm="mcp", scope="read"')
        self.assertEqual(client._redact("oauth-secret"), "[redacted]")

    def test_insufficient_scope_challenge_reauthorizes_and_retries_once(self):
        spec = MCPServerSpec("remote", None, (), {}, "https://mcp.example.test/mcp", {}, "client")
        response = MagicMock()
        response.read.return_value = b'{"jsonrpc":"2.0","id":1,"result":{}}'
        response.__enter__.return_value = response
        response.headers = {"Content-Type": "application/json"}
        error_headers = email.message.Message()
        error_headers["WWW-Authenticate"] = 'Bearer error="insufficient_scope", scope="files:read files:write"'
        forbidden = urllib.error.HTTPError(spec.url, 403, "Forbidden", error_headers, io.BytesIO(b""))
        with patch("backend.mcp_stdio.resolve_public_addresses", return_value=((2, 1, 6, ("93.184.216.34", 443)),)), \
             patch("backend.mcp_stdio.open_provider_request", side_effect=[forbidden, response]), \
             patch("backend.mcp_stdio.oauth_access_token", return_value="new-scope-token") as oauth:
            client = _StreamableHTTPClient(spec, oauth_port=8765)
            client.era = "modern"
            client._oauth_authorized = True
            client._set_oauth_token("old-scope-token")
            result = client._post({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}, 1)
        self.assertEqual(result["result"], {})
        self.assertTrue(any(call.kwargs.get("force_reauthorize") is True for call in oauth.call_args_list))
        self.assertTrue(any("files:write" in str(call.args[1]) for call in oauth.call_args_list if len(call.args) > 1))


if __name__ == "__main__":
    unittest.main()
