from __future__ import annotations

import os
import unittest
from pathlib import Path

import app
from backend.mcp_oauth import _discover
from backend.mcp_stdio import MCPToolRouter


class LiveProviderTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("BRAINCELLGPT_LIVE_PROVIDER"), "Set BRAINCELLGPT_LIVE_PROVIDER and BRAINCELLGPT_LIVE_API_KEY to enable the billable provider check")
    def test_live_provider_chat_completes(self):
        provider = os.environ["BRAINCELLGPT_LIVE_PROVIDER"].strip().lower()
        api_key = os.environ["BRAINCELLGPT_LIVE_API_KEY"]
        reply = app.generate_reply(
            "Reply with the exact words live provider check passed.", [], "normal",
            provider=provider, user_api_key=api_key,
            base_url=os.environ.get("BRAINCELLGPT_LIVE_BASE_URL", ""),
            model=os.environ.get("BRAINCELLGPT_LIVE_MODEL", ""),
        )
        self.assertIsInstance(reply, str)
        self.assertTrue(reply.strip())


class LiveMCPTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("BRAINCELLGPT_LIVE_MCP_URL"), "Set BRAINCELLGPT_LIVE_MCP_URL to enable remote MCP discovery")
    def test_live_remote_mcp_server_discovers_capabilities(self):
        config = [{
            "name": "live-check",
            "url": os.environ["BRAINCELLGPT_LIVE_MCP_URL"],
            **({"headers": {"Authorization": "Bearer ${BRAINCELLGPT_LIVE_MCP_TOKEN}"}}
               if os.environ.get("BRAINCELLGPT_LIVE_MCP_TOKEN") else {}),
        }]
        environment = dict(os.environ, BRAINCELLGPT_MCP_SERVERS=__import__("json").dumps(config))
        router = MCPToolRouter(environment, Path.cwd())
        try:
            self.assertTrue(router.tools, "The live MCP server returned no tools.")
        finally:
            router.close()

    @unittest.skipUnless(os.environ.get("BRAINCELLGPT_LIVE_OAUTH_MCP_URL"), "Set BRAINCELLGPT_LIVE_OAUTH_MCP_URL to enable live OAuth metadata validation")
    def test_live_oauth_resource_and_authorization_metadata(self):
        challenge = os.environ.get("BRAINCELLGPT_LIVE_OAUTH_CHALLENGE", "Bearer resource_metadata=\"/.well-known/oauth-protected-resource\"")
        resource, authorization, issuer = _discover(os.environ["BRAINCELLGPT_LIVE_OAUTH_MCP_URL"], challenge)
        self.assertTrue(resource.get("resource"))
        self.assertTrue(issuer.startswith("https://"))
        self.assertIn("S256", authorization.get("code_challenge_methods_supported", []))
        self.assertTrue(authorization.get("authorization_endpoint", "").startswith("https://"))
        self.assertTrue(authorization.get("token_endpoint", "").startswith("https://"))


if __name__ == "__main__":
    unittest.main()
