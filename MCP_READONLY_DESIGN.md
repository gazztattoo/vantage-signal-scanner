# Global Signal Scanner — Read-only MCP integration

## Status
Design-only checkpoint. This document does not add a server, credentials, routes,
database policies, Render services, or deployment configuration.

Target branch: `mcp-readonly-test`
Baseline: `8019244f244b06c3b5153a38aa56763899f4953a`

## Non-negotiable boundaries
- Never modify `main` or production services as part of this integration.
- Do not deploy or push to `push-test`; Render auto-deploys that branch.
- Do not expose source code, API keys, tokens, or environment-variable values.
- No SQL, arbitrary HTTP, shell, filesystem, or generic proxy tools.
- No write endpoints, signal scans, push subscriptions, push tests, or notifications.
- MCP tools must be read-only and restricted to the TEST environment.
- Show diffs and request approval before any deployment or promotion.

## Intended MCP tool allowlist
1. `get_panel_health` — GET `/api/health`
2. `get_market_status` — GET `/api/market-status`
3. `get_signal_analysis` — GET `/api/analysis` with validated symbol/interval
4. `get_signal_history` — GET `/api/history` with bounded limit and allowlisted filters
5. `get_signal_performance` — GET `/api/performance`
6. `get_lab_status` — GET `/api/lab-status`

No tool may call POST, PUT, PATCH, or DELETE routes.

## Authentication design requirements
- Use Auth0 as the OAuth 2.1 authorization server / identity provider.
- Validate access-token signature (RS256), issuer, audience, expiry, and required scope.
- Require a dedicated read-only scope such as `scanner:read`.
- Do not treat an Auth0 login token as an existing panel API token.
- Do not embed Auth0 client secrets, signing keys, or API credentials in source.
- Do not reuse the panel's broad `PANEL_ACCESS_CODE` or `ACCESS_TOKEN_SECRET`
  to mint general panel tokens for the MCP service.
- Confirm ChatGPT's supported OAuth client-registration mode before configuring
  Auth0 callbacks or creating a production-facing authorization client.

## Upstream data-access blocker
The current protected TEST API endpoints use the panel's general bearer token.
That token is not least-privilege: it may authorize routes beyond GET-only reads.
The MCP service must not obtain or mint that token.

Before implementation, choose and review one of these approaches:
- Add a separately scoped read-only API credential and verifier to the TEST API,
  with GET-only authorization and no access to mutation routes; or
- Provide a separate read-only data facade with narrowly scoped credentials and
  explicit endpoint allowlisting.

The Supabase service-role key must never be placed in the MCP service.

## Required verification before deployment
- Unit tests proving every exposed tool only calls its exact allowlisted GET route.
- Tests rejecting unknown symbols, intervals, filters, oversized limits, bad JWT
  issuer/audience/signature, missing scope, and expired tokens.
- Static scan confirming no secrets are committed.
- Review the complete diff and deployment plan with the user.
- Keep the MCP service separate from the existing panel and API services.


## OAuth role clarification (verified against MCP SDK v2 docs)
- Auth0 is the external authorization server / identity provider.
- The MCP service is an OAuth resource server. It validates bearer access tokens;
  it must not issue tokens or run a custom login/token endpoint.
- The MCP service must publish Protected Resource Metadata (RFC 9728) and
  advertise its Auth0 issuer and the read-only scope.
- The Auth0 application created in the dashboard is not yet configured:
  callback/logout/origin fields must remain blank until the public MCP URL and
  the exact OAuth client-registration approach are confirmed.
- Do not reveal or copy the Auth0 Client Secret into chat or commit it to GitHub.
- Before completing Auth0 setup, verify that the selected Auth0 tenant supports
  the client-registration method ChatGPT expects (Dynamic Client Registration
  or Client ID Metadata Documents). Do not assume the Regular Web application
  by itself completes MCP OAuth.

## Current verified API boundary
- `/api/health` and `/api/market-status` are public GET endpoints.
- `/api/analysis`, `/api/price`, `/api/history`, `/api/performance`,
  and `/api/lab-status` are behind the same signed panel bearer token.
- The current API access middleware also protects push subscribe/test/unsubscribe
  routes with that same token. Therefore the token is broader than read-only.
- The MCP service must not call `/api/access`, because that would require the
  panel access code and mint the broad panel token.
- No MCP implementation should be connected to the protected routes until a
  separate read-only credential/authorization boundary is implemented and tested
  in TEST, with no access to push or mutation routes.
