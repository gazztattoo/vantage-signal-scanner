"""Strict Auth0 JWT verification for the scanner's read-only API credential."""
import os
from functools import lru_cache

import jwt
from jwt import InvalidTokenError
from jwt import PyJWKClient


def _settings():
    issuer = os.environ.get("AUTH0_READONLY_ISSUER", "").strip()
    audience = os.environ.get("AUTH0_READONLY_AUDIENCE", "").strip()
    scope = os.environ.get("AUTH0_READONLY_SCOPE", "scanner:read").strip()
    if issuer:
        issuer = issuer.rstrip("/") + "/"
    return issuer, audience, scope


def is_configured():
    issuer, audience, scope = _settings()
    return bool(issuer and audience and scope == "scanner:read")


@lru_cache(maxsize=2)
def _jwks_client(jwks_url):
    return PyJWKClient(jwks_url, cache_jwk_set=True, lifespan=300, timeout=5)


def verify_access_token(token):
    """Return True only for a valid Auth0 RS256 token with scanner:read."""
    issuer, audience, required_scope = _settings()
    if not (issuer and audience and required_scope == "scanner:read"):
        return False
    try:
        jwks_url = issuer + ".well-known/jwks.json"
        signing_key = _jwks_client(jwks_url).get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=audience,
            issuer=issuer,
            options={
                "require": ["exp", "iat", "sub", "iss", "aud"],
                "verify_signature": True,
                "verify_exp": True,
                "verify_iat": True,
                "verify_iss": True,
                "verify_aud": True,
            },
            leeway=0,
        )
        # Require the exact dedicated API audience; reject extra audiences.
        token_aud = claims.get("aud")
        if token_aud != audience:
            return False
        scopes = claims.get("scope", "")
        if isinstance(scopes, str):
            scope_set = set(scopes.split())
        elif isinstance(scopes, list) and all(isinstance(item, str) for item in scopes):
            scope_set = set(scopes)
        else:
            return False
        return required_scope in scope_set
    except Exception:
        # Fail closed for malformed tokens, key lookup/network failures, and
        # unexpected verifier errors. Never fall back to the panel credential.
        return False
