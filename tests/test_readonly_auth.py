"""Security boundary tests for the read-only Auth0 credential.

Run with: python -m unittest discover -s tests -v
These tests mock key discovery and token decoding; they do not call Auth0,
Render, Supabase, Twelve Data, notification providers, or trading services.
"""
import unittest
from unittest.mock import Mock, patch

import readonly_auth


class ReadonlyAuthTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(
            "os.environ",
            {
                "AUTH0_READONLY_ISSUER": "https://example.eu.auth0.com/",
                "AUTH0_READONLY_AUDIENCE": "https://scanner-test.example/api",
                "AUTH0_READONLY_SCOPE": "scanner:read",
            },
            clear=False,
        )
        self.env.start()
        readonly_auth._jwks_client.cache_clear()

    def tearDown(self):
        self.env.stop()
        readonly_auth._jwks_client.cache_clear()

    @patch("readonly_auth.jwt.decode")
    @patch("readonly_auth._jwks_client")
    def test_accepts_valid_scoped_token(self, jwks_factory, decode):
        jwks_factory.return_value.get_signing_key_from_jwt.return_value.key = "public-key"
        decode.return_value = {
            "iss": "https://example.eu.auth0.com/",
            "aud": "https://scanner-test.example/api",
            "scope": "openid scanner:read",
            "sub": "mcp-client",
        }
        self.assertTrue(readonly_auth.verify_access_token("signed.jwt.token"))
        decode.assert_called_once()
        self.assertEqual(decode.call_args.kwargs["algorithms"], ["RS256"])

    @patch("readonly_auth.jwt.decode")
    @patch("readonly_auth._jwks_client")
    def test_rejects_wrong_or_extra_audience(self, jwks_factory, decode):
        jwks_factory.return_value.get_signing_key_from_jwt.return_value.key = "public-key"
        decode.return_value = {
            "iss": "https://example.eu.auth0.com/",
            "aud": ["https://scanner-test.example/api", "https://other.example"],
            "scope": "scanner:read",
            "sub": "mcp-client",
        }
        self.assertFalse(readonly_auth.verify_access_token("signed.jwt.token"))

    @patch("readonly_auth.jwt.decode")
    @patch("readonly_auth._jwks_client")
    def test_rejects_missing_scope(self, jwks_factory, decode):
        jwks_factory.return_value.get_signing_key_from_jwt.return_value.key = "public-key"
        decode.return_value = {
            "iss": "https://example.eu.auth0.com/",
            "aud": "https://scanner-test.example/api",
            "scope": "openid",
            "sub": "mcp-client",
        }
        self.assertFalse(readonly_auth.verify_access_token("signed.jwt.token"))

    @patch("readonly_auth._jwks_client")
    def test_fails_closed_when_jwks_unavailable(self, jwks_factory):
        jwks_factory.side_effect = RuntimeError("JWKS unavailable")
        self.assertFalse(readonly_auth.verify_access_token("signed.jwt.token"))

    def test_disabled_when_configuration_missing(self):
        with patch.dict(
            "os.environ",
            {"AUTH0_READONLY_ISSUER": "", "AUTH0_READONLY_AUDIENCE": ""},
        ):
            self.assertFalse(readonly_auth.is_configured())
            self.assertFalse(readonly_auth.verify_access_token("anything"))


if __name__ == "__main__":
    unittest.main()
